from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.engine.normative_retriever import load_normative_corpus, retrieve_normative_chunks_hybrid
from src.engine.normative_summarizer import finalize_normative_row, _build_candidate_gap
from src.engine.normative_qdrant_store import NormativeQdrantStore


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_requirements_map(requirements_rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in requirements_rows:
        tag = str(row.get("tag") or "").strip()
        if tag:
            out[tag] = row
    return out


def _pick_top_candidate_row(shortlist_row: dict) -> dict | None:
    top = shortlist_row.get("top_candidates")
    if isinstance(top, list) and top:
        first = top[0]
        if isinstance(first, dict):
            return first
    return None

def _pick_top_candidate_rows(shortlist_row: dict, limit: int = 5) -> list[dict]:
    top = shortlist_row.get("top_candidates")
    if not isinstance(top, list):
        return []
    out = []
    for item in top[:limit]:
        if isinstance(item, dict):
            out.append(item)
    return out

def _compact_query_parts(*parts: Any) -> str:
    items: list[str] = []
    seen: set[str] = set()

    for part in parts:
        s = str(part or "").strip()
        if not s or s.lower() == "none":
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(s)

    return " ".join(items)


def _build_query(tag: str, req_full: dict, req_ref: dict, candidate: dict) -> str:
    req_cls = str((req_ref.get("device_class") or req_full.get("device_class") or "")).upper()
    eq_class = str(req_full.get("equipment_class") or "").lower()
    ep_kind = str(req_full.get("ep_kind") or "").lower()
    load_type = str(req_full.get("load_type") or "").lower()
    phase_type = str(req_full.get("phase_type") or "").lower()
    trip = str(req_ref.get("trip_curve") or req_full.get("trip_curve") or "").upper()
    poles = req_ref.get("poles") or req_full.get("poles")
    nominal = req_ref.get("suggested_nominal_a") or req_full.get("suggested_nominal_a")
    breaking = req_ref.get("breaking_capacity_ka") or req_full.get("breaking_capacity_ka")
    display_name = str(req_full.get("display_name") or "").strip()

    cand_model = str(candidate.get("model") or "").strip()
    cand_series = str(candidate.get("series") or "").strip()
    rcd_ma = candidate.get("rcd_ma")

    if req_cls == "RCBO":
        return _compact_query_parts(
            "дополнительная защита УДТ",
            f"{rcd_ma or 30} мА",
            "конечные цепи",
            eq_class,
            ep_kind,
            display_name,
            "RCBO",
            trip,
            f"{nominal}A" if nominal is not None else "",
            f"{poles}P" if poles is not None else "",
            "защита от поражения током",
            "автоматическое отключение питания",
        )

    if req_cls == "MPCB":
        return _compact_query_parts(
            "защита электродвигателя от перегрузки и короткого замыкания",
            "пускатель",
            "контактор",
            "motor starter",
            eq_class,
            ep_kind,
            display_name,
            cand_series,
            cand_model,
            f"{nominal}A" if nominal is not None else "",
            f"{poles}P" if poles is not None else "",
            f"{breaking}kA" if breaking is not None else "",
        )

    if req_cls == "MCB" and trip == "D":
        return _compact_query_parts(
            "автоматический выключатель",
            "характеристика D",
            "пусковые токи",
            eq_class,
            ep_kind,
            load_type,
            display_name,
            cand_series,
            cand_model,
            f"{nominal}A" if nominal is not None else "",
            f"{poles}P" if poles is not None else "",
            f"{breaking}kA" if breaking is not None else "",
        )

    if req_cls == "MCB":
        return _compact_query_parts(
            "защита от сверхтока",
            "автоматический выключатель",
            "перегрузка",
            "короткое замыкание",
            eq_class,
            ep_kind,
            load_type,
            phase_type,
            display_name,
            cand_series,
            cand_model,
            trip,
            f"{nominal}A" if nominal is not None else "",
            f"{poles}P" if poles is not None else "",
            f"{breaking}kA" if breaking is not None else "",
        )

    return _compact_query_parts(
        "электроустановка защита",
        eq_class,
        ep_kind,
        display_name,
        req_cls,
        f"{nominal}A" if nominal is not None else "",
        f"{poles}P" if poles is not None else "",
    )


def _simple_verdict(req_ref: dict, hits: list[dict]) -> str:
    req_cls = str(req_ref.get("device_class") or "").upper()
    titles = " ".join((h.get("doc_title") or "") for h in hits).lower()
    texts = " ".join((h.get("text") or "")[:600] for h in hits).lower()

    if req_cls == "RCBO":
        if ("30 мa" in texts or "30 ма" in texts or "30 ma" in texts or "удт" in texts or "дифференциаль" in texts):
            return "supported"
        return "supported_with_note" if hits else "no_evidence_found"

    if req_cls == "MPCB":
        if ("пускател" in texts or "электродвигател" in texts or "контактор" in texts):
            return "supported"
        return "supported_with_note" if hits else "no_evidence_found"

    if req_cls == "MCB":
        if ("сверхток" in texts or "короткого замыкания" in texts or "перегрузк" in texts):
            return "supported"
        return "supported_with_note" if hits else "no_evidence_found"

    return "supported_with_note" if hits else "no_evidence_found"


def build_normative_review(
    shortlist_rows: list[dict],
    requirements_rows: list[dict],
    corpus_path: Path,
    top_k: int = 5,
    qdrant_store: NormativeQdrantStore | None = None,
) -> list[dict]:
    corpus = load_normative_corpus(corpus_path)
    req_map = _build_requirements_map(requirements_rows)

    out: list[dict] = []

    for shortlist_row in shortlist_rows:
        tag = str(shortlist_row.get("tag") or "").strip()
        if not tag:
            continue

        top_rows = _pick_top_candidate_rows(shortlist_row, limit=5)
        req_full = req_map.get(tag, {})

        if not top_rows:
            out.append(
                {
                    "tag": tag,
                    "query": "",
                    "verdict": "no_candidate_in_shortlist",
                    "requirement_full": req_full,
                    "requirement_ref": {},
                    "candidate": {},
                    "shortlist_candidates_count": shortlist_row.get("candidates_count"),
                    "normative_hits": [],
                    "candidate_rank": None,
                    "candidate_options": [],
                }
            )
            continue

        candidate_options: list[dict] = []
        ranked_rows: list[dict] = []

        for idx, top_row in enumerate(top_rows, start=1):
            req_ref = top_row.get("requirement_ref") or {}
            candidate = top_row.get("candidate") or {}

            query = _build_query(tag, req_full, req_ref, candidate)
            hits = retrieve_normative_chunks_hybrid(
                query=query,
                corpus=corpus,
                qdrant_store=qdrant_store,
                top_k=top_k,
            )

            row_out = {
                "tag": tag,
                "query": query,
                "requirement_full": req_full,
                "requirement_ref": req_ref,
                "candidate": candidate,
                "shortlist_candidates_count": shortlist_row.get("candidates_count"),
                "normative_hits": hits,
                "candidate_rank": idx,
            }
            finalized = finalize_normative_row(row_out)
            ranked_rows.append(finalized)

            candidate_options.append(
                {
                    "rank": idx,
                    "vendor": candidate.get("vendor"),
                    "series": candidate.get("series"),
                    "model": candidate.get("model"),
                    "device_class": candidate.get("device_class"),
                    "rated_current_a": candidate.get("rated_current_a"),
                    "trip_curve": candidate.get("trip_curve"),
                    "breaking_capacity_ka": candidate.get("breaking_capacity_ka"),
                    "confidence": finalized.get("confidence"),
                    "verdict": finalized.get("verdict"),
                    "why_this_candidate": finalized.get("why_this_candidate"),
                }
            )

        def _row_sort_key(x: dict) -> tuple:
            verdict = str(x.get("verdict") or "")
            conf = float(x.get("confidence") or 0.0)

            verdict_priority = {
                "supported": 0,
                "supported_with_conditions": 1,
                "weak_evidence": 2,
                "no_evidence_found": 3,
            }.get(verdict, 9)

            return (verdict_priority, -conf)

        ranked_rows.sort(key=_row_sort_key)

        best = ranked_rows[0]
        for opt in candidate_options:
            if int(opt.get("rank") or 0) == 1:
                opt["why_not_best"] = "это лучший кандидат по текущему нормативному и техническому ранжированию"
            else:
                opt["why_not_best"] = _build_candidate_gap(best, opt)
        best["candidate_options"] = candidate_options
        out.append(best)

    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shortlist", required=True, help="Путь к shortlist.json")
    ap.add_argument("--requirements", required=True, help="Путь к requirements.json")
    ap.add_argument("--corpus", required=True, help="Путь к normative_corpus.json")
    ap.add_argument("--out_json", required=True, help="Куда сохранить normative_review.json")
    ap.add_argument("--top_k", type=int, default=5)
    ap.add_argument("--qdrant_path", default="", help="Локальная папка Qdrant store")
    ap.add_argument("--qdrant_collection", default="normative_chunks", help="Имя коллекции Qdrant")
    ap.add_argument(
        "--embedding_model",
        default="models/Frida",
        help="Embedding model for Qdrant search",
    )
    args = ap.parse_args()

    shortlist_rows = _load_json(Path(args.shortlist))
    requirements_rows = _load_json(Path(args.requirements))
    
    qdrant_store = None
    if args.qdrant_path:
        qdrant_store = NormativeQdrantStore.load(
            qdrant_path=args.qdrant_path,
            collection_name=args.qdrant_collection,
            model_name=args.embedding_model,
        )

    review = build_normative_review(
        shortlist_rows=shortlist_rows,
        requirements_rows=requirements_rows,
        corpus_path=Path(args.corpus),
        top_k=args.top_k,
        qdrant_store=qdrant_store,
    )
    _save_json(Path(args.out_json), review)

    print(f"normative_review: {len(review)}")


if __name__ == "__main__":
    main()