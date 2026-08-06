from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PROJECT_ID_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9_.-]+$")
SAFE_NAME_RE = re.compile(r"[^A-Za-zА-Яа-яЁё0-9_.()\- ]+")

STATUS_WAITING_FILES = "WAITING_FILES"
STATUS_PREPROCESSING = "PREPROCESSING"
STATUS_WAITING_HITL = "WAITING_HITL"
STATUS_READY_TO_RUN = "READY_TO_RUN"
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"

FILE_RULES: dict[str, set[str]] = {
    "schemes": {".pdf"},
    "passports": {".pdf"},
    "template": {".xlsx"},
    "additional": {".pdf", ".xlsx", ".json", ".csv"},
}

FIELD_META: dict[str, dict[str, Any]] = {
    "p_kw": {"label": "мощность", "unit": "кВт", "min": 0.001, "max": 100000.0},
    "u_v": {"label": "напряжение", "unit": "В", "options": [220, 230, 380, 400, 415]},
    "i_a": {"label": "номинальный ток", "unit": "А", "min": 0.001, "max": 100000.0},
    "eta_pct": {"label": "КПД", "unit": "%", "min": 1.0, "max": 100.0},
    "phases": {"label": "число фаз", "unit": "", "options": [1, 3]},
}


class ProjectServiceError(RuntimeError):
    pass


class ProjectNotFoundError(ProjectServiceError):
    pass


class ProjectConflictError(ProjectServiceError):
    pass


class ProjectValidationError(ProjectServiceError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _safe_filename(filename: str) -> str:
    name = Path(filename or "file").name.strip()
    name = SAFE_NAME_RE.sub("_", name).strip(" .")
    return name[:180] or "file"


def _validate_file_signature(suffix: str, content: bytes) -> None:
    """Reject obvious extension/content mismatches before the parsers see them."""
    if suffix == ".pdf" and not content.lstrip().startswith(b"%PDF"):
        raise ProjectValidationError("Файл имеет расширение PDF, но не содержит заголовок PDF")
    if suffix == ".xlsx" and not content.startswith(b"PK"):
        raise ProjectValidationError("Файл имеет расширение XLSX, но не является ZIP-контейнером Excel")


class ProjectService:
    """Persistent project sessions for the web wizard.

    Project metadata and uploads live under ``projects_root``. Final pipeline
    artifacts live under ``runs_root`` and are consumed by the read-only
    ProjectResultRepository.
    """

    def __init__(self, projects_root: str | Path, runs_root: str | Path) -> None:
        self.projects_root = Path(projects_root).resolve()
        self.runs_root = Path(runs_root).resolve()
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def validate_project_id(project_id: str) -> str:
        project_id = str(project_id or "").strip()
        if not project_id or not PROJECT_ID_RE.fullmatch(project_id):
            raise ProjectValidationError(
                "Шифр проекта может содержать буквы, цифры, точку, дефис и подчёркивание"
            )
        return project_id

    def project_dir(self, project_id: str) -> Path:
        project_id = self.validate_project_id(project_id)
        path = (self.projects_root / project_id).resolve()
        if self.projects_root not in path.parents:
            raise ProjectValidationError("Недопустимый путь проекта")
        return path

    def run_dir(self, project_id: str) -> Path:
        project_id = self.validate_project_id(project_id)
        path = (self.runs_root / project_id).resolve()
        if self.runs_root not in path.parents:
            raise ProjectValidationError("Недопустимый путь результатов")
        return path

    def _state_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "project_state.json"

    def _default_state(self, project_id: str, status: str = STATUS_WAITING_FILES) -> dict[str, Any]:
        now = utc_now()
        return {
            "project_id": project_id,
            "status": status,
            "progress": 0,
            "stage": "Создание проекта",
            "created_at": now,
            "updated_at": now,
            "files": [],
            "questions": [],
            "events": [],
            "error": None,
            "pipeline_mode": None,
            "run_result": None,
            "stale_results": False,
            "scheme_scan_completed": False,
            "detected_equipment": [],
            "access_key": None,
            "shared_invite": None,
        }

    def create_project(
        self,
        project_id: str | None = None,
        *,
        overwrite: bool = False,
        access_key: str | None = None,
        shared_invite: str | None = None,
    ) -> dict[str, Any]:
        project_id = self.validate_project_id(project_id or f"project-{uuid.uuid4().hex[:12]}")
        project_dir = self.project_dir(project_id)
        with self._lock:
            if project_dir.exists() and not overwrite:
                raise ProjectConflictError(f"Проект {project_id} уже существует")
            if overwrite and project_dir.exists():
                shutil.rmtree(project_dir)
            for relative in ("uploads/schemes", "uploads/passports", "uploads/template", "uploads/additional", "working"):
                (project_dir / relative).mkdir(parents=True, exist_ok=True)
            state = self._default_state(project_id)
            state["access_key"] = access_key
            state["shared_invite"] = shared_invite
            self._append_event_to_state(
                state,
                "bot",
                "Проект создан. Сначала загрузите схемы и экспликации в PDF.",
                "project_created",
            )
            self._save_state(state)
            result = self.public_state(state)
            if access_key:
                result["project_key"] = access_key
            return result

    def require_access(self, project_id: str, provided_key: str | None) -> None:
        with self._lock:
            state = self._load_state_raw(project_id)
            expected = state.get("access_key")
            if expected and str(provided_key or "") != str(expected):
                raise ProjectValidationError("Для доступа к этому проекту требуется ключ сессии")

    def _load_state_raw(self, project_id: str) -> dict[str, Any]:
        state_path = self._state_path(project_id)
        if state_path.exists():
            return json.loads(state_path.read_text(encoding="utf-8"))

        # Existing pipeline run can be opened without manually importing it.
        run_dir = self.run_dir(project_id)
        if run_dir.exists() and any(run_dir.glob("*.json")):
            state = self._default_state(project_id, STATUS_COMPLETED)
            state.update({"progress": 100, "stage": "Результаты готовы"})
            self._append_event_to_state(
                state,
                "bot",
                "Найдены готовые результаты пайплайна. Открываю сводный список электроприёмников.",
                "existing_run_imported",
            )
            project_dir = self.project_dir(project_id)
            project_dir.mkdir(parents=True, exist_ok=True)
            self._save_state(state)
            return state
        raise ProjectNotFoundError(project_id)

    def get_state(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            return self.public_state(self._load_state_raw(project_id))

    def list_projects(self) -> list[dict[str, Any]]:
        project_ids = {path.name for path in self.projects_root.iterdir() if path.is_dir()}
        project_ids.update(path.name for path in self.runs_root.iterdir() if path.is_dir())
        result: list[dict[str, Any]] = []
        for project_id in sorted(project_ids):
            try:
                state = self.get_state(project_id)
            except (ProjectServiceError, OSError, ValueError):
                continue
            result.append({
                "project_id": project_id,
                "status": state.get("status"),
                "stage": state.get("stage"),
                "progress": state.get("progress"),
                "updated_at": state.get("updated_at"),
                "results_available": state.get("results_available"),
                "file_counts": state.get("file_counts"),
            })
        result.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        return result

    @staticmethod
    def progress_hint(progress: int, status: str) -> str:
        progress = int(progress or 0)
        if status == STATUS_COMPLETED:
            return "Готово. Можно разбирать результаты по каждому электроприёмнику."
        if status == STATUS_FAILED:
            return "Расчёт остановлен. Откройте журнал ошибки и исправьте исходные данные."
        if progress < 15:
            return "Собираю исходные данные — начнём со схем и экспликаций."
        if progress < 30:
            return "Изучаю документы и сопоставляю обозначения оборудования."
        if progress < 45:
            return "Уточняю недостающие характеристики. Здесь нужен ваш инженерный опыт."
        if progress < 60:
            return "Всё в порядке, начинаю полный расчёт."
        if progress < 75:
            return "Да подожди ты, всё чётко — проверяю расчётные параметры."
        if progress < 90:
            return "Не торопись, всему своё время — подбираю аппараты и нормативные основания."
        return "Осталось чуть-чуть — собираю итоговый Excel и сводку."

    @staticmethod
    def public_state(state: dict[str, Any]) -> dict[str, Any]:
        files = state.get("files") or []
        questions = state.get("questions") or []
        answered = sum(bool(q.get("answered")) or bool(q.get("skipped")) for q in questions)
        safe_state = {key: value for key, value in state.items() if key != "access_key"}
        return {
            **safe_state,
            "file_counts": {
                category: sum(1 for item in files if item.get("category") == category)
                for category in FILE_RULES
            },
            "questions_total": len(questions),
            "questions_answered": answered,
            "questions_remaining": max(0, len(questions) - answered),
            "ready_for_preprocess": self_files_ready(files),
            "results_available": state.get("status") == STATUS_COMPLETED,
            "stale_results": bool(state.get("stale_results")),
            "progress_hint": ProjectService.progress_hint(state.get("progress", 0), str(state.get("status") or "")),
            "shared_project": bool(state.get("access_key")),
        }

    def _save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = utc_now()
        _atomic_write_json(self._state_path(state["project_id"]), state)

    @staticmethod
    def _append_event_to_state(
        state: dict[str, Any], actor: str, text: str, event_type: str = "message", **meta: Any
    ) -> None:
        state.setdefault("events", []).append(
            {
                "id": uuid.uuid4().hex,
                "actor": actor,
                "text": text,
                "type": event_type,
                "created_at": utc_now(),
                **meta,
            }
        )

    def append_event(self, project_id: str, actor: str, text: str, event_type: str = "message", **meta: Any) -> None:
        with self._lock:
            state = self._load_state_raw(project_id)
            self._append_event_to_state(state, actor, text, event_type, **meta)
            self._save_state(state)

    def _invalidate_after_file_change(self, state: dict[str, Any], changed_category: str | None = None) -> None:
        had_results = state.get("status") == STATUS_COMPLETED or bool(state.get("run_result"))
        state.update({
            "status": STATUS_WAITING_FILES,
            "stage": "Загрузка исходных данных",
            "progress": 5,
            "questions": [],
            "error": None,
            "pipeline_mode": None,
            "run_result": None,
            "stale_results": had_results or bool(state.get("stale_results")),
        })
        if changed_category == "schemes":
            state["scheme_scan_completed"] = False
            state["detected_equipment"] = []
        working = self.project_dir(state["project_id"]) / "working"
        names = ["passports_parsed.json", "items_preliminary.json", "equipment_registry.json", "user_inputs.json"]
        if changed_category == "schemes":
            names.append("equipment_registry_preview.json")
        for name in names:
            (working / name).unlink(missing_ok=True)
        (self.run_dir(state["project_id"]) / "user_inputs.json").unlink(missing_ok=True)

    def list_uploads(self, project_id: str) -> list[dict[str, Any]]:
        with self._lock:
            state = self._load_state_raw(project_id)
            return list(state.get("files") or [])

    def delete_upload(self, project_id: str, file_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._load_state_raw(project_id)
            record = next((row for row in state.get("files", []) if row.get("id") == file_id), None)
            if not record:
                raise ProjectValidationError("Файл не найден")
            category = str(record.get("category") or "")
            filename = Path(str(record.get("filename") or "")).name
            path = self.project_dir(project_id) / "uploads" / category / filename
            path.unlink(missing_ok=True)
            state["files"] = [row for row in state.get("files", []) if row.get("id") != file_id]
            self._invalidate_after_file_change(state, category)
            self._append_event_to_state(state, "user", f"Удалён файл «{filename}».", "file_deleted", file_id=file_id)
            self._append_event_to_state(state, "bot", self._files_progress_message(state.get("files") or []), "file_progress")
            self._save_state(state)
            return self.public_state(state)

    def save_upload(
        self,
        project_id: str,
        category: str,
        filename: str,
        content: bytes,
        document_type: str | None = None,
    ) -> dict[str, Any]:
        category = str(category or "").strip().lower()
        if category not in FILE_RULES:
            raise ProjectValidationError("Неизвестная категория файла")
        if not content:
            raise ProjectValidationError("Загружен пустой файл")
        max_bytes = int(os.getenv("BOILER_ELEC_MAX_UPLOAD_MB", "80")) * 1024 * 1024
        if len(content) > max_bytes:
            raise ProjectValidationError("Файл превышает допустимый размер")

        safe_name = _safe_filename(filename)
        suffix = Path(safe_name).suffix.lower()
        if suffix not in FILE_RULES[category]:
            allowed = ", ".join(sorted(FILE_RULES[category]))
            raise ProjectValidationError(f"Для категории {category} допустимы: {allowed}")
        _validate_file_signature(suffix, content)

        digest = hashlib.sha256(content).hexdigest()
        with self._lock:
            state = self._load_state_raw(project_id)
            duplicate = next((f for f in state.get("files", []) if f.get("sha256") == digest), None)
            if duplicate:
                return {**duplicate, "duplicate": True}

            upload_dir = self.project_dir(project_id) / "uploads" / category
            upload_dir.mkdir(parents=True, exist_ok=True)
            if category == "template":
                old_templates = [row for row in state.get("files", []) if row.get("category") == "template"]
                for row in old_templates:
                    (upload_dir / Path(str(row.get("filename") or "")).name).unlink(missing_ok=True)
                state["files"] = [row for row in state.get("files", []) if row.get("category") != "template"]
            destination = upload_dir / safe_name
            if destination.exists():
                destination = upload_dir / f"{destination.stem}_{digest[:8]}{destination.suffix}"
            destination.write_bytes(content)
            record = {
                "id": uuid.uuid4().hex,
                "category": category,
                "filename": destination.name,
                "size": len(content),
                "sha256": digest,
                "uploaded_at": utc_now(),
                "document_type": str(document_type or "").strip().upper()[:24] or None,
            }
            state.setdefault("files", []).append(record)
            self._invalidate_after_file_change(state, category)
            self._append_event_to_state(
                state,
                "user",
                f"Загружен файл «{destination.name}» ({category}).",
                "file_uploaded",
                file_id=record["id"],
            )
            self._append_event_to_state(
                state,
                "bot",
                self._files_progress_message(state.get("files") or []),
                "file_progress",
            )
            self._save_state(state)
            return record

    @staticmethod
    def _files_progress_message(files: list[dict[str, Any]]) -> str:
        counts = {category: sum(f.get("category") == category for f in files) for category in FILE_RULES}
        missing: list[str] = []
        if counts["schemes"] == 0:
            missing.append("схемы PDF")
        if counts["passports"] == 0:
            missing.append("паспорта PDF")
        if counts["template"] == 0:
            missing.append("шаблон XLSX")
        if missing:
            return "Осталось загрузить: " + ", ".join(missing) + "."
        return "Минимальный комплект файлов получен. Можно выполнить предварительный анализ."

    def upload_paths(self, project_id: str) -> dict[str, Path]:
        root = self.project_dir(project_id) / "uploads"
        return {category: root / category for category in FILE_RULES}

    def set_scheme_scanning(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._load_state_raw(project_id)
            if not any(row.get("category") == "schemes" for row in state.get("files") or []):
                raise ProjectValidationError("Сначала загрузите хотя бы одну схему PDF")
            state.update({"stage": "Распознавание оборудования по схемам", "progress": 12, "error": None})
            self._append_event_to_state(state, "bot", "Изучаю схемы и формирую предварительный список электроприёмников.", "scheme_scan_started")
            self._save_state(state)
            return self.public_state(state)

    def complete_scheme_scan(self, project_id: str, registry: list[dict[str, Any]], mode: str) -> dict[str, Any]:
        detected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in registry:
            if not isinstance(row, dict):
                continue
            tag = str(row.get("tag") or "").strip()
            if not tag or tag.upper() in seen:
                continue
            seen.add(tag.upper())
            detected.append({
                "tag": tag,
                "name": row.get("base_name") or row.get("display_name") or row.get("name"),
                "equipment_class": row.get("equip_class") or row.get("equipment_class"),
            })
        with self._lock:
            state = self._load_state_raw(project_id)
            state.update({
                "scheme_scan_completed": True,
                "detected_equipment": detected,
                "pipeline_mode": mode,
                "stage": "Схемы обработаны — ожидаются паспорта",
                "progress": 18,
                "error": None,
            })
            preview = self.project_dir(project_id) / "working" / "equipment_registry_preview.json"
            _atomic_write_json(preview, registry)
            tags = ", ".join(row["tag"] for row in detected[:12])
            suffix = f" Первые теги: {tags}." if tags else ""
            self._append_event_to_state(
                state,
                "bot",
                f"По схемам найдено позиций: {len(detected)}.{suffix} Теперь загрузите паспорта оборудования для этих ЭП.",
                "scheme_scan_completed",
            )
            self._save_state(state)
            return self.public_state(state)

    def fail_scheme_scan(self, project_id: str, error: str) -> None:
        with self._lock:
            state = self._load_state_raw(project_id)
            state.update({"stage": "Загрузка исходных данных", "progress": 10, "error": None})
            self._append_event_to_state(state, "bot", f"Не удалось автоматически разобрать схемы: {error}. Можно загрузить паспорта и продолжить общий анализ.", "scheme_scan_warning")
            self._save_state(state)

    def set_preprocessing(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._load_state_raw(project_id)
            if not self_files_ready(state.get("files") or []):
                raise ProjectValidationError("Сначала загрузите схемы, паспорта и шаблон XLSX")
            state.update(
                {
                    "status": STATUS_PREPROCESSING,
                    "stage": "Предварительный анализ документов",
                    "progress": 15,
                    "error": None,
                    "stale_results": False,
                }
            )
            self._append_event_to_state(
                state, "bot", "Начинаю предварительный разбор схем и паспортов.", "preprocess_started"
            )
            self._save_state(state)
            return self.public_state(state)

    def complete_preprocess(
        self,
        project_id: str,
        *,
        passports: list[dict[str, Any]],
        items: list[dict[str, Any]] | None = None,
        registry: list[dict[str, Any]] | None = None,
        mode: str,
        notes: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            state = self._load_state_raw(project_id)
            questions = build_questions(passports, items or [])
            state["questions"] = questions
            state["pipeline_mode"] = mode
            state["stale_results"] = False
            if questions:
                state.update(
                    {
                        "status": STATUS_WAITING_HITL,
                        "stage": "Уточнение исходных данных",
                        "progress": 30,
                    }
                )
                message = f"Предварительный анализ завершён. Требуется ответить на {len(questions)} вопросов."
            else:
                state.update(
                    {
                        "status": STATUS_READY_TO_RUN,
                        "stage": "Исходные данные готовы",
                        "progress": 40,
                    }
                )
                message = "Предварительный анализ завершён. Обязательных уточнений не найдено."
            self._append_event_to_state(state, "bot", message, "preprocess_completed")
            for note in notes or []:
                self._append_event_to_state(state, "bot", note, "notice")
            self._save_state(state)

            working = self.project_dir(project_id) / "working"
            _atomic_write_json(working / "passports_parsed.json", passports)
            _atomic_write_json(working / "items_preliminary.json", items or [])
            _atomic_write_json(working / "equipment_registry.json", registry or [])
            return self.public_state(state)

    def fail(self, project_id: str, error: str) -> None:
        with self._lock:
            state = self._load_state_raw(project_id)
            state.update({"status": STATUS_FAILED, "stage": "Ошибка", "error": str(error)})
            self._append_event_to_state(state, "bot", f"Ошибка: {error}", "error")
            self._save_state(state)

    def _question_is_active(self, state: dict[str, Any], question: dict[str, Any]) -> bool:
        dependency = question.get("depends_on")
        if not dependency:
            return True
        parent = next((q for q in state.get("questions", []) if q.get("id") == dependency.get("question_id")), None)
        return bool(parent and parent.get("answered") and parent.get("answer") == dependency.get("equals"))

    def next_question(self, project_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._load_state_raw(project_id)
            changed = False
            for question in state.get("questions", []):
                if question.get("answered") or question.get("skipped"):
                    continue
                if not self._question_is_active(state, question):
                    if question.get("depends_on"):
                        parent_id = question["depends_on"]["question_id"]
                        parent = next((q for q in state.get("questions", []) if q.get("id") == parent_id), None)
                        if parent and parent.get("answered"):
                            question["skipped"] = True
                            changed = True
                    continue
                if changed:
                    self._save_state(state)
                return question
            if changed:
                self._save_state(state)
            return None

    def list_questions(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._load_state_raw(project_id)
            rows = []
            for question in state.get("questions", []):
                row = dict(question)
                row["active"] = self._question_is_active(state, question)
                rows.append(row)
            user_inputs_path = self.run_dir(project_id) / "user_inputs.json"
            try:
                user_inputs = json.loads(user_inputs_path.read_text(encoding="utf-8")) if user_inputs_path.exists() else {}
            except (OSError, ValueError):
                user_inputs = {}
            return {
                "project_id": project_id,
                "questions": rows,
                "user_inputs": user_inputs,
            }

    def _clear_question_and_dependents(self, state: dict[str, Any], question_id: str) -> None:
        pending = {question_id}
        changed = True
        while changed:
            changed = False
            for question in state.get("questions", []):
                parent_id = (question.get("depends_on") or {}).get("question_id")
                if parent_id in pending and question.get("id") not in pending:
                    pending.add(str(question.get("id")))
                    changed = True
        for question in state.get("questions", []):
            if question.get("id") not in pending:
                continue
            for key in ("answer", "answered_at", "comment"):
                question.pop(key, None)
            question["answered"] = False
            question["skipped"] = False

    def _refresh_hitl_status(self, state: dict[str, Any]) -> None:
        active_pending = False
        for question in state.get("questions", []):
            if question.get("answered") or question.get("skipped"):
                continue
            if self._question_is_active(state, question):
                active_pending = True
                continue
            parent_id = (question.get("depends_on") or {}).get("question_id")
            parent = next((q for q in state.get("questions", []) if q.get("id") == parent_id), None)
            if parent and parent.get("answered"):
                question["skipped"] = True
        if active_pending:
            state.update({"status": STATUS_WAITING_HITL, "stage": "Уточнение исходных данных", "progress": 30})
        else:
            state.update({"status": STATUS_READY_TO_RUN, "stage": "Исходные данные готовы", "progress": 40})

    def reset_question(self, project_id: str, question_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._load_state_raw(project_id)
            if not any(q.get("id") == question_id for q in state.get("questions", [])):
                raise ProjectValidationError("Вопрос не найден")
            self._clear_question_and_dependents(state, question_id)
            self._refresh_hitl_status(state)
            self._write_user_inputs(state)
            self._append_event_to_state(state, "user", f"Ответ на вопрос {question_id} сброшен.", "question_reset", question_id=question_id)
            self._save_state(state)
            return self.public_state(state)

    def answer_question(
        self,
        project_id: str,
        question_id: str,
        *,
        value: Any = None,
        action: str = "answer",
        comment: str | None = None,
        replace: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            state = self._load_state_raw(project_id)
            question = next((q for q in state.get("questions", []) if q.get("id") == question_id), None)
            if not question:
                raise ProjectValidationError("Вопрос не найден")
            if question.get("answered") or question.get("skipped"):
                if not replace:
                    raise ProjectConflictError("На этот вопрос уже дан ответ")
                self._clear_question_and_dependents(state, question_id)
                question = next(q for q in state.get("questions", []) if q.get("id") == question_id)
            if not self._question_is_active(state, question):
                raise ProjectValidationError("Вопрос сейчас не активен")

            if action == "skip":
                if question.get("required"):
                    raise ProjectValidationError("Обязательный вопрос нельзя пропустить")
                question["skipped"] = True
                answer_text = "Пропустить"
            else:
                normalized = validate_question_value(question, value)
                question.update(
                    {
                        "answered": True,
                        "answer": normalized,
                        "answered_at": utc_now(),
                        "comment": comment,
                    }
                )
                answer_text = format_answer(question, normalized)

            event_type = "question_answer_updated" if replace else "question_answer"
            self._append_event_to_state(state, "user", answer_text, event_type, question_id=question_id)
            previous_status = state.get("status")
            self._refresh_hitl_status(state)
            self._write_user_inputs(state)
            if state.get("status") == STATUS_READY_TO_RUN and previous_status != STATUS_READY_TO_RUN:
                self._append_event_to_state(
                    state,
                    "bot",
                    "Все активные вопросы обработаны. Пайплайн готов к запуску.",
                    "hitl_completed",
                )
            self._save_state(state)
            return self.public_state(state)

    def _write_user_inputs(self, state: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {"_meta": {}}
        heating_items_raw = ""
        roof_len: float | None = None
        cabinets_raw = ""
        for question in state.get("questions", []):
            if not question.get("answered"):
                continue
            tag = question.get("tag")
            field = question.get("field")
            value = question.get("answer")
            if tag and tag != "_meta":
                payload.setdefault(tag, {})[field] = value
                continue
            if field == "lighting_in_cabinet":
                payload["_meta"][field] = value
            elif field == "heating_needed":
                payload["_meta"][field] = value
            elif field == "heating_items":
                heating_items_raw = str(value or "")
            elif field == "roof_len_m":
                roof_len = float(value) if value not in (None, "") else None
            elif field == "cabinets":
                cabinets_raw = str(value or "")

        if payload["_meta"].get("heating_needed"):
            heating = {
                "linear_w_per_m": 16.0,
                "items": parse_named_numeric_lines(heating_items_raw, value_key="meters"),
                "roof": {"roof_len_m": roof_len, "multiplier": 8.0} if roof_len else None,
            }
            payload["_meta"]["heating"] = heating
        payload["_meta"]["cabinets"] = parse_named_numeric_lines(cabinets_raw, value_key="p_kw", name_key="tag")

        # Remove empty meta only if no meta fields were answered.
        if not payload["_meta"]:
            payload.pop("_meta")
        working = self.project_dir(state["project_id"]) / "working"
        run_dir = self.run_dir(state["project_id"])
        run_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(working / "user_inputs.json", payload)
        _atomic_write_json(run_dir / "user_inputs.json", payload)
        return payload

    def set_running(self, project_id: str, stage: str = "Запуск пайплайна", progress: int = 45) -> dict[str, Any]:
        with self._lock:
            state = self._load_state_raw(project_id)
            if state.get("status") not in {STATUS_READY_TO_RUN, STATUS_FAILED, STATUS_COMPLETED}:
                raise ProjectConflictError("Пайплайн нельзя запустить в текущем состоянии")
            state.update({"status": STATUS_RUNNING, "stage": stage, "progress": progress, "error": None})
            self._append_event_to_state(state, "bot", "Пайплайн запущен.", "run_started")
            self._save_state(state)
            return self.public_state(state)

    def update_progress(self, project_id: str, stage: str, progress: int) -> None:
        with self._lock:
            state = self._load_state_raw(project_id)
            state.update({"stage": stage, "progress": max(0, min(99, int(progress)))})
            self._save_state(state)

    def complete_run(self, project_id: str, result: dict[str, Any], mode: str) -> None:
        with self._lock:
            state = self._load_state_raw(project_id)
            state.update(
                {
                    "status": STATUS_COMPLETED,
                    "stage": "Результаты готовы",
                    "progress": 100,
                    "run_result": result,
                    "pipeline_mode": mode,
                    "error": None,
                    "stale_results": False,
                }
            )
            items_count = result.get("items_count") or result.get("requirements_count")
            suffix = f" Обработано позиций: {items_count}." if items_count is not None else ""
            self._append_event_to_state(
                state,
                "bot",
                "Расчёт завершён." + suffix + " Откройте сводный список результатов.",
                "run_completed",
            )
            self._save_state(state)

    def dialog(self, project_id: str) -> dict[str, Any]:
        state = self.get_state(project_id)
        counts = state.get("file_counts") or {}
        status = state.get("status")
        action: dict[str, Any] | None = None
        prompt = ""
        if status == STATUS_WAITING_FILES:
            if counts.get("schemes", 0) == 0:
                prompt = "Начнём со схем. Загрузите PDF разделов ТМ, ОВ, ГСВ, ЭО, ХВО и других экспликаций, где есть электроприёмники."
                action = {"type": "scheme_uploads", "label": "Загрузить схемы по разделам"}
            elif not state.get("scheme_scan_completed"):
                prompt = "Схемы получены. Сначала определить по ним список оборудования и требуемых паспортов?"
                action = {"type": "scan_schemes", "label": "Определить оборудование по схемам"}
            elif counts.get("passports", 0) == 0:
                detected = state.get("detected_equipment") or []
                tags = ", ".join(str(row.get("tag")) for row in detected[:10] if row.get("tag"))
                prompt = "Теперь загрузите паспорта оборудования" + (f" для найденных тегов: {tags}." if tags else ".")
                action = {"type": "upload", "category": "passports", "accept": ".pdf", "multiple": True, "label": "Загрузить паспорта PDF"}
            elif counts.get("template", 0) == 0:
                prompt = "Загрузите расчётный шаблон ВРУ в формате XLSX."
                action = {"type": "upload", "category": "template", "accept": ".xlsx", "multiple": False, "label": "Загрузить шаблон XLSX"}
            else:
                prompt = "Комплект файлов собран. Выполнить предварительный анализ и найти недостающие токи, КПД, мощности и другие характеристики?"
                action = {"type": "preprocess", "label": "Проверить исходные данные"}
        elif status == STATUS_PREPROCESSING:
            prompt = "Выполняется предварительный разбор документов."
        elif status == STATUS_WAITING_HITL:
            question = self.next_question(project_id)
            if question:
                prompt = question.get("message") or "Уточните параметр."
                action = {"type": "question", "question": question}
            else:
                prompt = "Все вопросы обработаны."
                action = {"type": "run", "label": "Запустить пайплайн"}
        elif status == STATUS_READY_TO_RUN:
            prompt = "Исходные данные готовы. Запустить полный расчёт?"
            action = {"type": "run", "label": "Запустить полный пайплайн"}
        elif status == STATUS_RUNNING:
            prompt = f"{state.get('stage')}. Выполнено: {state.get('progress', 0)}%."
        elif status == STATUS_COMPLETED:
            prompt = "Результаты готовы. Можно открыть список тегов и варианты аппаратов защиты."
            action = {"type": "results", "label": "Показать результаты"}
        elif status == STATUS_FAILED:
            prompt = f"Выполнение остановлено: {state.get('error') or 'неизвестная ошибка'}."
            action = {"type": "retry", "label": "Повторить запуск"}
        return {"project_id": project_id, "prompt": prompt, "action": action, "state": state}

    def chat_reply(self, project_id: str, message: str) -> dict[str, Any]:
        message = str(message or "").strip()
        if not message:
            raise ProjectValidationError("Введите сообщение")
        with self._lock:
            state = self._load_state_raw(project_id)
            self._append_event_to_state(state, "user", message, "chat_message")
            lower = message.lower()
            if any(word in lower for word in ("статус", "этап", "готов")):
                reply = f"Текущий этап: {state.get('stage')}. Прогресс: {state.get('progress', 0)}%."
            elif any(word in lower for word in ("файл", "загруз")):
                files = state.get("files") or []
                counts = {c: sum(f.get("category") == c for f in files) for c in FILE_RULES}
                reply = (
                    f"Загружено: схем — {counts['schemes']}, паспортов — {counts['passports']}, "
                    f"шаблонов — {counts['template']}."
                )
            elif any(word in lower for word in ("помощ", "что делать", "дальше")):
                reply = self.dialog(project_id)["prompt"]
            elif state.get("status") == STATUS_COMPLETED and any(word in lower for word in ("результ", "автомат", "тег")):
                reply = "Результаты доступны справа. Используйте поиск и раскрывайте карточки тегов."
            else:
                reply = "Я сопровождаю проект по фиксированным этапам. " + self.dialog(project_id)["prompt"]
            self._append_event_to_state(state, "bot", reply, "chat_reply")
            self._save_state(state)
            return {"reply": reply, "state": self.public_state(state)}


def self_files_ready(files: Iterable[dict[str, Any]]) -> bool:
    categories = {str(item.get("category")) for item in files}
    return {"schemes", "passports", "template"}.issubset(categories)


def build_questions(passports: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    seen_tags: set[str] = set()
    combined: list[dict[str, Any]] = []
    for source in (passports, items):
        for row in source:
            if not isinstance(row, dict):
                continue
            tag = str(row.get("tag") or "").strip()
            if not tag or tag in seen_tags:
                continue
            seen_tags.add(tag)
            combined.append(row)

    for row in combined:
        tag = str(row.get("tag") or "").strip()
        missing = list(row.get("missing_fields") or [])
        # Preserve the questionnaire behavior: missing values may be inferred
        # from explicit nulls even if missing_fields was not populated.
        for field in FIELD_META:
            if row.get(field) is None and field not in missing:
                missing.append(field)
        for field in FIELD_META:
            if field not in missing:
                continue
            meta = FIELD_META[field]
            question_type = "select" if meta.get("options") else "number"
            questions.append(
                {
                    "id": f"{tag}.{field}",
                    "tag": tag,
                    "field": field,
                    "type": question_type,
                    "message": f"Для {tag} не найден параметр «{meta['label']}». Укажите значение или пропустите с фиксацией замечания.",
                    "unit": meta.get("unit"),
                    "options": meta.get("options"),
                    "min": meta.get("min"),
                    "max": meta.get("max"),
                    "current_value": row.get(field),
                    "source_file": row.get("source_file"),
                    "required": field in {"p_kw", "u_v", "phases"},
                    "answered": False,
                    "skipped": False,
                }
            )

    questions.extend(
        [
            {
                "id": "meta.lighting_in_cabinet",
                "tag": "_meta",
                "field": "lighting_in_cabinet",
                "type": "boolean",
                "message": "Освещение заведено в отдельный шкаф ШО/ЩО/ШНО/ЩНО?",
                "required": True,
                "answered": False,
                "skipped": False,
            },
            {
                "id": "meta.heating_needed",
                "tag": "_meta",
                "field": "heating_needed",
                "type": "boolean",
                "message": "Нужно учитывать электрообогрев дренажей, кровли, водостоков или газоходов?",
                "required": True,
                "answered": False,
                "skipped": False,
            },
            {
                "id": "meta.heating_items",
                "tag": "_meta",
                "field": "heating_items",
                "type": "multiline",
                "message": "Введите участки электрообогрева построчно в формате «название;метры». Расчёт выполняется из 16 Вт/м.",
                "placeholder": "дренажи газоходов;10\nводостоки;12",
                "required": False,
                "depends_on": {"question_id": "meta.heating_needed", "equals": True},
                "answered": False,
                "skipped": False,
            },
            {
                "id": "meta.roof_len_m",
                "tag": "_meta",
                "field": "roof_len_m",
                "type": "number",
                "message": "Укажите длину одной стороны кровли для расчёта кабеля 8·L, м. При отсутствии обогрева кровли введите 0.",
                "unit": "м",
                "min": 0,
                "max": 10000,
                "required": False,
                "depends_on": {"question_id": "meta.heating_needed", "equals": True},
                "answered": False,
                "skipped": False,
            },
            {
                "id": "meta.cabinets",
                "tag": "_meta",
                "field": "cabinets",
                "type": "multiline",
                "message": "Введите дополнительные шкафы построчно в формате «тег;кВт». Поле можно оставить пустым.",
                "placeholder": "ШУК;1.0\nЩУТ;0.5",
                "required": False,
                "answered": False,
                "skipped": False,
            },
        ]
    )
    return questions


def validate_question_value(question: dict[str, Any], value: Any) -> Any:
    qtype = question.get("type")
    if qtype == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"да", "yes", "y", "true", "1"}:
            return True
        if text in {"нет", "no", "n", "false", "0"}:
            return False
        raise ProjectValidationError("Выберите «Да» или «Нет»")
    if qtype == "select":
        options = question.get("options") or []
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise ProjectValidationError("Выберите допустимое значение") from exc
        if normalized not in options:
            raise ProjectValidationError("Выбрано недопустимое значение")
        return normalized
    if qtype == "number":
        try:
            normalized = float(str(value).replace(",", "."))
        except (TypeError, ValueError) as exc:
            raise ProjectValidationError("Введите число") from exc
        minimum = question.get("min")
        maximum = question.get("max")
        if minimum is not None and normalized < float(minimum):
            raise ProjectValidationError(f"Минимальное значение: {minimum}")
        if maximum is not None and normalized > float(maximum):
            raise ProjectValidationError(f"Максимальное значение: {maximum}")
        return int(normalized) if normalized.is_integer() else normalized
    if qtype == "multiline":
        text = str(value or "").strip()
        if question.get("required") and not text:
            raise ProjectValidationError("Поле обязательно")
        # Validate only non-empty lines here. Parsing occurs in user_inputs builder.
        if question.get("field") in {"heating_items", "cabinets"} and text:
            parse_named_numeric_lines(text, value_key="value")
        return text
    text = str(value or "").strip()
    if question.get("required") and not text:
        raise ProjectValidationError("Поле обязательно")
    return text


def parse_named_numeric_lines(
    text: str,
    *,
    value_key: str,
    name_key: str = "name",
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if ";" not in line:
            raise ProjectValidationError(f"Строка «{line}» должна иметь формат название;число")
        name, value_text = [part.strip() for part in line.split(";", 1)]
        if not name:
            raise ProjectValidationError("Не указано название")
        try:
            number = float(value_text.replace(",", "."))
        except ValueError as exc:
            raise ProjectValidationError(f"В строке «{line}» значение должно быть числом") from exc
        if number <= 0:
            raise ProjectValidationError(f"В строке «{line}» значение должно быть больше нуля")
        result.append({name_key: name, value_key: number})
    return result


def format_answer(question: dict[str, Any], value: Any) -> str:
    if question.get("type") == "boolean":
        return "Да" if value else "Нет"
    unit = question.get("unit") or ""
    return f"{value} {unit}".strip()
