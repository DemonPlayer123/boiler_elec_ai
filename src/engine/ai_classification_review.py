from __future__ import annotations


def build_ai_classification_review(classification_report: list[dict]) -> list[dict]:
    out = []

    for row in classification_report:
        eq_class = row.get("equipment_class")
        load_type = row.get("load_type")
        phase_type = row.get("phase_type")
        has_vfd = bool(row.get("has_vfd"))
        safety = bool(row.get("safety_critical"))

        downstream_effects = []

        if eq_class == "burner":
            downstream_effects.append("trip_curve_D_expected")
        if eq_class == "pump":
            downstream_effects.append("motor_protection_logic")
        if has_vfd:
            downstream_effects.append("vfd_branch_logic")
        if phase_type == "three_phase":
            downstream_effects.append("3P_device_expected")
        if safety:
            downstream_effects.append("manual_review_priority")

        out.append({
            "tag": row.get("tag"),
            "equipment_class": eq_class,
            "load_type": load_type,
            "phase_type": phase_type,
            "has_vfd": has_vfd,
            "safety_critical": safety,
            "reasons": row.get("classification_reasons") or [],
            "downstream_effects": downstream_effects,
        })

    return out