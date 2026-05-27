from __future__ import annotations

from typing import Any


def build_ai_entity_review(
    registry: list[dict],
    entity_links: list[dict],
) -> list[dict]:
    out: list[dict] = []

    for row in entity_links:
        tag = row.get("registry_tag")
        matched = bool(row.get("matched"))
        conf = float(row.get("match_confidence") or 0.0)
        reason = row.get("link_reason")
        registry_kind = row.get("registry_kind")

        risk_flags = []

        if not matched:
            risk_flags.append("unmatched_registry_entity")
        elif conf < 0.5:
            risk_flags.append("low_match_confidence")

        missing_model = not row.get("passport_model")

        # Не шумим на HVAC group-file и на уверенных rule/exact match
        model_missing_is_problem = (
            missing_model
            and reason not in {"hvac_group_file", "tag_exact", "pump_tag_model_file"}
            and conf < 0.9
        )
        if model_missing_is_problem:
            risk_flags.append("passport_model_missing")

        status = "ok"
        if not matched:
            status = "needs_manual_review"
        elif conf < 0.5:
            status = "weak_match"
        elif model_missing_is_problem:
            status = "weak_match"

        out.append({
            "tag": tag,
            "status": status,
            "match_confidence": conf,
            "link_reason": reason,
            "registry_kind": registry_kind,
            "registry_name": row.get("registry_base_name"),
            "passport_tag": row.get("passport_tag"),
            "passport_model": row.get("passport_model"),
            "passport_display_name": row.get("passport_display_name"),
            "risk_flags": risk_flags,
        })

    return out