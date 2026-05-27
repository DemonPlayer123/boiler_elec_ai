from __future__ import annotations

from typing import Any


def _norm_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _build_retrieved_map(retrieved_chunks: list[dict]) -> dict[str, dict]:
    out = {}
    for row in retrieved_chunks or []:
        tag = str(row.get("tag") or "").strip()
        if tag:
            out[tag] = row
    return out


def _build_shortlist_map(shortlist: list[dict]) -> dict[str, dict]:
    out = {}
    for row in shortlist or []:
        tag = str(row.get("tag") or "").strip()
        if tag:
            out[tag] = row
    return out


def _top_candidate_summary(shortlist_row: dict) -> str:
    top = (shortlist_row or {}).get("top_candidates") or []
    if not top:
        return "Подходящие кандидаты в shortlist отсутствуют."

    first = top[0].get("candidate") or {}
    vendor = first.get("vendor") or "неизвестный вендор"
    series = first.get("series") or "без серии"
    model = first.get("model") or "без модели"
    rated = first.get("rated_current_a")
    poles = first.get("poles")
    curve = first.get("trip_curve")
    ka = first.get("breaking_capacity_ka")

    return (
        f"Наиболее близкий кандидат: {vendor}, серия {series}, модель {model}, "
        f"{rated} А, {poles}P, кривая {curve}, {ka} кА."
    )


def _risk_flags(req: dict, shortlist_row: dict, retrieved_row: dict) -> list[str]:
    flags: list[str] = []

    top = (shortlist_row or {}).get("top_candidates") or []
    if not top:
        flags.append("shortlist_empty")

    if req.get("rcd_required") is True:
        flags.append("check_rcd_requirement")

    if not (retrieved_row or {}).get("chunks"):
        flags.append("no_normative_hits")

    if _norm_text(req.get("device_class")) == "unknown":
        flags.append("unknown_device_class")

    if req.get("suggested_nominal_a") is None:
        flags.append("missing_nominal_requirement")

    return flags


def build_rag_summary(
    requirements: list[dict],
    shortlist: list[dict],
    retrieved_chunks: list[dict],
) -> list[dict]:
    """
    Первая офлайн-версия RAG summary.
    Это не свободная LLM-генерация, а структурированная интерпретация:
    - берём shortlist,
    - прикладываем найденные нормы,
    - формируем объяснение и флаги риска.
    """
    retrieved_map = _build_retrieved_map(retrieved_chunks)
    shortlist_map = _build_shortlist_map(shortlist)

    summaries: list[dict] = []

    for req in requirements or []:
        tag = str(req.get("tag") or "").strip()
        if not tag:
            continue

        sl = shortlist_map.get(tag, {"tag": tag, "top_candidates": []})
        rt = retrieved_map.get(tag, {"tag": tag, "chunks": []})

        chunks = rt.get("chunks") or []
        citations = []
        for ch in chunks[:3]:
            citations.append(
                {
                    "doc_id": ch.get("doc_id"),
                    "title": ch.get("title"),
                    "section": ch.get("section"),
                    "chunk_id": ch.get("chunk_id"),
                    "score": ch.get("score"),
                }
            )

        explanation_parts: list[str] = []

        explanation_parts.append(
            f"Для объекта {tag} сформированы требования к аппарату: "
            f"{req.get('device_class')} {req.get('poles')}P, "
            f"ориентировочный номинал {req.get('suggested_nominal_a')} А, "
            f"характеристика {req.get('trip_curve')}, "
            f"отключающая способность не ниже {req.get('breaking_capacity_ka')} кА."
        )

        explanation_parts.append(_top_candidate_summary(sl))

        if citations:
            explanation_parts.append(
                "По retrieval найдены релевантные нормативные фрагменты, "
                "которые можно использовать для обоснования выбора и проверки допустимости решения."
            )
        else:
            explanation_parts.append(
                "Релевантные нормативные фрагменты не найдены; требуется расширить корпус нормативки или уточнить запрос."
            )

        flags = _risk_flags(req, sl, rt)

        recommendation = "Решение допустимо к дальнейшей инженерной проверке."
        if "shortlist_empty" in flags:
            recommendation = "Подходящие кандидаты не найдены; требуется пересмотреть требования или каталог."
        elif "no_normative_hits" in flags:
            recommendation = "Shortlist сформирован, но нормативное обоснование пока слабое; нужно расширить корпус норм."
        elif "check_rcd_requirement" in flags:
            recommendation = "Необходимо отдельно проверить требования к дифференциальной защите."

        summaries.append(
            {
                "tag": tag,
                "requirement_snapshot": {
                    "device_class": req.get("device_class"),
                    "suggested_nominal_a": req.get("suggested_nominal_a"),
                    "poles": req.get("poles"),
                    "trip_curve": req.get("trip_curve"),
                    "breaking_capacity_ka": req.get("breaking_capacity_ka"),
                    "rcd_required": req.get("rcd_required"),
                },
                "citations": citations,
                "risk_flags": flags,
                "recommendation": recommendation,
                "explanation_text": " ".join(explanation_parts),
            }
        )

    return summaries