from __future__ import annotations


def _count_status(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows or []:
        st = str(row.get("status") or "unknown")
        out[st] = out.get(st, 0) + 1
    return out


def _collect_risk_flags(rows: list[dict]) -> dict[str, int]:
    freq: dict[str, int] = {}
    for row in rows or []:
        for flag in row.get("risk_flags") or []:
            freq[flag] = freq.get(flag, 0) + 1
    return dict(sorted(freq.items(), key=lambda x: (-x[1], x[0])))


def build_ai_project_summary(
    ai_entity_review: list[dict],
    ai_consistency_review: list[dict],
    ai_normative_review: list[dict],
    ai_catalog_review: list[dict],
) -> dict:
    entity_status = _count_status(ai_entity_review)
    consistency_status = _count_status(ai_consistency_review)
    catalog_status = _count_status(ai_catalog_review)

    entity_risks = _collect_risk_flags(ai_entity_review)
    consistency_risks = _collect_risk_flags(ai_consistency_review)
    catalog_risks = _collect_risk_flags(ai_catalog_review)

    needs_review_tags = []

    for row in ai_consistency_review or []:
        if row.get("status") != "ok":
            needs_review_tags.append(row.get("tag"))

    summary_lines = []

    summary_lines.append(
        f"Entity review: ok={entity_status.get('ok', 0)}, weak/needs_review={sum(v for k, v in entity_status.items() if k != 'ok')}."
    )
    summary_lines.append(
        f"Consistency review: ok={consistency_status.get('ok', 0)}, needs_review={consistency_status.get('needs_review', 0)}."
    )
    summary_lines.append(
        f"Catalog review: ok={catalog_status.get('ok', 0)}, needs_review={catalog_status.get('needs_review', 0)}."
    )

    if needs_review_tags:
        summary_lines.append(
            "Позиции, требующие ручной проверки: " + ", ".join(str(x) for x in needs_review_tags if x)
        )
    else:
        summary_lines.append("Все позиции прошли consistency review без замечаний.")

    return {
        "entity_review_status_counts": entity_status,
        "consistency_review_status_counts": consistency_status,
        "catalog_review_status_counts": catalog_status,
        "entity_risk_flags_top": entity_risks,
        "consistency_risk_flags_top": consistency_risks,
        "catalog_risk_flags_top": catalog_risks,
        "needs_manual_review_tags": needs_review_tags,
        "summary_lines": summary_lines,
    }