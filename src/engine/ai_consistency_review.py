from __future__ import annotations


def build_ai_consistency_review(
    items: list[dict],
    entity_links: list[dict],
    classification_report: list[dict],
    requirements: list[dict],
) -> list[dict]:
    cls_by_tag = {x.get("tag"): x for x in classification_report}
    req_by_tag = {x.get("tag"): x for x in requirements}
    link_by_tag = {x.get("registry_tag"): x for x in entity_links}

    out = []

    for it in items:
        tag = it.get("tag")
        cls = cls_by_tag.get(tag, {})
        req = req_by_tag.get(tag, {})
        link = link_by_tag.get(tag, {})

        item_equipment_class = str(it.get("equipment_class") or "").strip().lower()
        req_equipment_class = str(req.get("equipment_class") or "").strip().lower()
        cls_equipment_class = str(cls.get("equipment_class") or "").strip().lower()

        effective_equipment_class = (
            cls_equipment_class
            or item_equipment_class
            or req_equipment_class
            or None
        )

        effective_ep_kind = (
            str(it.get("ep_kind") or "").strip().lower()
            or str(req.get("ep_kind") or "").strip().lower()
            or None
        )

        if not effective_equipment_class and effective_ep_kind in {
            "pump",
            "burner",
            "hvac",
            "cabinet",
            "lighting",
            "heating",
            "water_treatment",
        }:
            effective_equipment_class = effective_ep_kind

        risk_flags = []

        i_nom = it.get("i_nom_a")
        i_calc = it.get("i_calc_a")

        # fallback для случаев, когда объект не проходил калибровку,
        # но паспортный ток уже есть в item-слое (например, горелки)
        if i_nom is None:
            i_nom = it.get("i_a")

        if i_calc is None:
            i_calc = it.get("i_a")
        
        if i_nom is not None and i_calc is not None and i_calc < i_nom:
            risk_flags.append("calc_below_passport_nominal")

        if not link.get("matched", True):
            risk_flags.append("entity_link_missing")

        if not effective_equipment_class:
            risk_flags.append("classification_missing")

        if not req:
            risk_flags.append("requirements_missing")

        status = "ok" if not risk_flags else "needs_review"

        out.append(
            {
                "tag": tag,
                "status": status,
                "equipment_class": effective_equipment_class,
                "passport_model": it.get("model"),
                "display_name": it.get("display_name"),
                "i_nom_a": i_nom,
                "i_calc_a": i_calc,
                "suggested_nominal_a": req.get("suggested_nominal_a"),
                "device_class": req.get("device_class"),
                "trip_curve": req.get("trip_curve"),
                "risk_flags": risk_flags,
            }
        )

    return out