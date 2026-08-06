from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Iterable


PROJECT_ID_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9_.-]+$")


class ProjectNotFoundError(FileNotFoundError):
    pass


class InvalidProjectIdError(ValueError):
    pass


@dataclass(frozen=True)
class CachedJson:
    mtime_ns: int
    value: Any


class ProjectResultRepository:
    """Read-only adapter over pipeline JSON artifacts.

    The repository intentionally does not invoke Qdrant or LLMs. The list page
    must be fast and deterministic; expensive refresh operations belong to
    explicit background actions.
    """

    FILE_ALIASES: dict[str, tuple[str, ...]] = {
        "items": ("items_final.json", "items_final(1).json"),
        "requirements": ("requirements.json", "requirements(3).json"),
        "shortlist": ("shortlist.json", "shortlist(2).json"),
        "normative": (
            "normative_review_llm.json",
            "normative_review_llm(1).json",
            "normative_review.json",
            "normative_review(1).json",
        ),
        "priced": (
            "catalog_with_prices_grouped.json",
            "normative_review_openai.json",
            "normative_review_openai(1).json",
        ),
        "consistency": (
            "ai_consistency_review.json",
            "consistency_report.json",
        ),
        "critic": ("grok_critic_summary.json",),
        "run_result": ("run_result.json", "run_result(2).json"),
    }

    def __init__(self, runs_root: str | Path) -> None:
        self.runs_root = Path(runs_root).resolve()
        self._cache: dict[Path, CachedJson] = {}
        self._lock = RLock()

    def project_dir(self, project_id: str) -> Path:
        project_id = str(project_id or "").strip()
        if not project_id or not PROJECT_ID_RE.fullmatch(project_id):
            raise InvalidProjectIdError("Недопустимый идентификатор проекта")
        path = (self.runs_root / project_id).resolve()
        if self.runs_root not in path.parents and path != self.runs_root:
            raise InvalidProjectIdError("Недопустимый путь проекта")
        if not path.exists() or not path.is_dir():
            raise ProjectNotFoundError(project_id)
        return path

    def _resolve_file(self, project_dir: Path, logical_name: str) -> Path | None:
        for filename in self.FILE_ALIASES[logical_name]:
            candidate = project_dir / filename
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def _read_json(self, path: Path | None, default: Any) -> Any:
        if path is None:
            return default
        stat = path.stat()
        with self._lock:
            cached = self._cache.get(path)
            if cached and cached.mtime_ns == stat.st_mtime_ns:
                return cached.value
            value = json.loads(path.read_text(encoding="utf-8"))
            self._cache[path] = CachedJson(stat.st_mtime_ns, value)
            return value

    @staticmethod
    def _index(rows: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(rows, list):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            tag = str(row.get("tag") or "").strip().upper()
            if tag:
                result[tag] = row
        return result

    @staticmethod
    def _candidate_from_shortlist_option(option: dict[str, Any]) -> dict[str, Any]:
        candidate = option.get("candidate")
        if isinstance(candidate, dict):
            merged = dict(candidate)
            for key in ("rank", "selector_score", "selector_reasons"):
                if key in option and key not in merged:
                    merged[key] = option[key]
            return merged
        return dict(option)

    @classmethod
    def _shortlist_candidates(cls, row: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not row:
            return []
        raw = row.get("top_candidates") or row.get("candidate_options") or []
        if not isinstance(raw, list):
            return []
        return [cls._candidate_from_shortlist_option(x) for x in raw if isinstance(x, dict)]

    @staticmethod
    def _candidate_identity(candidate: dict[str, Any] | None) -> tuple[str, str, str]:
        candidate = candidate or {}
        return (
            str(candidate.get("vendor") or "").strip().upper(),
            str(candidate.get("series") or "").strip().upper(),
            str(candidate.get("model") or "").strip().upper(),
        )

    @classmethod
    def _merge_candidates(
        cls,
        base: Iterable[dict[str, Any]],
        enriched: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        enriched_index = {
            cls._candidate_identity(candidate): candidate
            for candidate in enriched
            if cls._candidate_identity(candidate) != ("", "", "")
        }
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for candidate in list(base) + list(enriched):
            identity = cls._candidate_identity(candidate)
            if identity in seen:
                continue
            seen.add(identity)
            merged = dict(candidate)
            if identity in enriched_index:
                merged.update(enriched_index[identity])
            result.append(merged)
        return result

    @staticmethod
    def _manual_review_checks(normative: dict[str, Any] | None) -> list[dict[str, Any]]:
        checks = (normative or {}).get("engineering_checks") or []
        if not isinstance(checks, list):
            return []
        return [
            check
            for check in checks
            if isinstance(check, dict)
            and str(check.get("status") or "").lower() in {"manual_review", "needs_review", "warning"}
        ]

    @classmethod
    def _derive_status(
        cls,
        normative: dict[str, Any] | None,
        consistency: dict[str, Any] | None,
        selected: dict[str, Any] | None,
    ) -> tuple[str, list[str]]:
        warnings: list[str] = []
        consistency_status = str((consistency or {}).get("status") or "").lower()
        if consistency_status and consistency_status not in {"ok", "supported"}:
            warnings.append("Проверка согласованности требует внимания")

        manual_checks = cls._manual_review_checks(normative)
        if manual_checks:
            warnings.append(f"Инженерных проверок: {len(manual_checks)}")

        verdict = str((normative or {}).get("verdict") or "").lower()
        if verdict in {"unsupported", "rejected", "needs_review", "manual_review"}:
            warnings.append("Нормативный результат требует ручной проверки")
        elif verdict == "supported_with_conditions":
            warnings.append("Решение поддержано с условиями")

        if not selected:
            warnings.append("Кандидат не выбран")

        if consistency_status not in {"", "ok", "supported"} or verdict in {
            "unsupported",
            "rejected",
            "needs_review",
            "manual_review",
        }:
            return "needs_review", warnings
        if manual_checks or verdict == "supported_with_conditions":
            return "warning", warnings
        if not selected:
            return "missing", warnings
        return "ready", warnings

    def _load_indexes(self, project_id: str) -> dict[str, Any]:
        project_dir = self.project_dir(project_id)
        data: dict[str, Any] = {}
        for logical_name in self.FILE_ALIASES:
            default: Any = {} if logical_name == "run_result" else []
            data[logical_name] = self._read_json(
                self._resolve_file(project_dir, logical_name), default
            )
        data["items_index"] = self._index(data["items"])
        data["requirements_index"] = self._index(data["requirements"])
        data["shortlist_index"] = self._index(data["shortlist"])
        data["normative_index"] = self._index(data["normative"])
        data["priced_index"] = self._index(data["priced"])
        data["consistency_index"] = self._index(data["consistency"])
        return data

    def list_equipment(self, project_id: str) -> dict[str, Any]:
        data = self._load_indexes(project_id)
        ordered_tags: list[str] = []
        for rows_name in ("items", "requirements", "shortlist"):
            rows = data[rows_name]
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                tag = str(row.get("tag") or "").strip().upper()
                if tag and tag not in ordered_tags:
                    ordered_tags.append(tag)

        summaries: list[dict[str, Any]] = []
        for tag in ordered_tags:
            item = data["items_index"].get(tag, {})
            requirement = data["requirements_index"].get(tag, {})
            shortlist = data["shortlist_index"].get(tag, {})
            normative = data["normative_index"].get(tag, {})
            priced = data["priced_index"].get(tag, {})
            consistency = data["consistency_index"].get(tag, {})

            shortlist_candidates = self._shortlist_candidates(shortlist)
            priced_candidates = self._shortlist_candidates(priced)
            candidates = self._merge_candidates(shortlist_candidates, priced_candidates)
            selected = (
                (priced.get("candidate") if isinstance(priced, dict) else None)
                or (normative.get("candidate") if isinstance(normative, dict) else None)
                or (candidates[0] if candidates else None)
            )
            status, warnings = self._derive_status(normative, consistency, selected)
            summaries.append(
                {
                    "tag": tag,
                    "display_name": item.get("display_name")
                    or requirement.get("display_name")
                    or item.get("model")
                    or "Электроприёмник",
                    "equipment_class": item.get("equipment_class")
                    or requirement.get("equipment_class")
                    or consistency.get("equipment_class"),
                    "status": status,
                    "warnings": warnings,
                    "verdict": normative.get("verdict"),
                    "confidence": normative.get("confidence"),
                    "estimated_current_a": requirement.get("estimated_current_a"),
                    "selection_current_a": requirement.get("selection_current_a"),
                    "suggested_nominal_a": requirement.get("suggested_nominal_a"),
                    "device_class": requirement.get("device_class"),
                    "candidate_count": shortlist.get("candidates_count")
                    or normative.get("shortlist_candidates_count")
                    or len(candidates),
                    "shortlist_size": len(candidates),
                    "selected_candidate": selected,
                }
            )

        counters = {
            "total": len(summaries),
            "ready": sum(x["status"] == "ready" for x in summaries),
            "warning": sum(x["status"] == "warning" for x in summaries),
            "needs_review": sum(x["status"] == "needs_review" for x in summaries),
            "missing": sum(x["status"] == "missing" for x in summaries),
        }
        classes = sorted(
            {str(x.get("equipment_class")) for x in summaries if x.get("equipment_class")}
        )
        return {
            "project_id": project_id,
            "counters": counters,
            "equipment_classes": classes,
            "items": summaries,
            "run_result": data.get("run_result") or {},
        }

    def get_equipment(self, project_id: str, tag: str, limit: int = 8) -> dict[str, Any] | None:
        tag_norm = str(tag or "").strip().upper()
        data = self._load_indexes(project_id)
        item = data["items_index"].get(tag_norm)
        requirement = data["requirements_index"].get(tag_norm)
        shortlist = data["shortlist_index"].get(tag_norm)
        normative = data["normative_index"].get(tag_norm)
        priced = data["priced_index"].get(tag_norm)
        consistency = data["consistency_index"].get(tag_norm)

        if not any((item, requirement, shortlist, normative, priced)):
            return None

        shortlist_candidates = self._shortlist_candidates(shortlist)
        priced_candidates = self._shortlist_candidates(priced)
        candidates = self._merge_candidates(shortlist_candidates, priced_candidates)
        selected = (
            ((priced or {}).get("candidate") if isinstance(priced, dict) else None)
            or ((normative or {}).get("candidate") if isinstance(normative, dict) else None)
            or (candidates[0] if candidates else None)
        )
        status, warnings = self._derive_status(normative, consistency, selected)

        safe_limit = max(1, min(int(limit), 50))
        return {
            "project_id": project_id,
            "tag": tag_norm,
            "status": status,
            "warnings": warnings,
            "item": item or {},
            "requirement": requirement or {},
            "selected_candidate": selected or {},
            "candidate_options": candidates[:safe_limit],
            "candidate_options_total": len(candidates),
            "candidate_pool_total": (shortlist or {}).get("candidates_count"),
            "normative": normative or {},
            "consistency": consistency or {},
        }
