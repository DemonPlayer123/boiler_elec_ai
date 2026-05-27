from __future__ import annotations


def build_ai_catalog_review(
    requirements: list[dict],
    candidates: list[dict],
    shortlist: list[dict],
) -> list[dict]:
    cand_by_tag: dict[str, list[dict]] = {}
    short_by_tag: dict[str, list[dict]] = {}

    for row in candidates or []:
        tag = row.get("tag")
        if tag:
            cand_by_tag.setdefault(tag, []).append(row)

    for row in shortlist or []:
        tag = row.get("tag")
        if tag:
            short_by_tag.setdefault(tag, []).append(row)

    out: list[dict] = []

    for req in requirements or []:
        tag = req.get("tag")
        req_device = req.get("device_class")
        req_nominal = req.get("suggested_nominal_a")
        req_curve = req.get("trip_curve")
        req_poles = req.get("poles")

        tag_candidates = cand_by_tag.get(tag, [])
        tag_shortlist = short_by_tag.get(tag, [])

        risk_flags: list[str] = []
        notes: list[str] = []

        if not tag_candidates:
            risk_flags.append("catalog_candidates_missing")
            notes.append("Не найдено ни одного кандидата в каталоге.")
        else:
            notes.append(f"Найдено кандидатов: {len(tag_candidates)}.")

        if not tag_shortlist:
            risk_flags.append("catalog_shortlist_missing")
            notes.append("Shortlist не сформирован.")
        else:
            notes.append(f"В shortlist вошло позиций: {len(tag_shortlist)}.")

        # Очень мягкая диагностическая проверка по shortlist
        top = tag_shortlist[0] if tag_shortlist else None
        if top:
            top_nominal = top.get("nominal_a")
            top_curve = top.get("trip_curve")
            top_poles = top.get("poles")
            top_device = top.get("device_class")

            if req_nominal is not None and top_nominal is not None and top_nominal < req_nominal:
                risk_flags.append("shortlist_nominal_below_requirement")

            if req_curve and top_curve and str(req_curve) != str(top_curve):
                risk_flags.append("shortlist_curve_mismatch")

            if req_poles and top_poles and int(top_poles) != int(req_poles):
                risk_flags.append("shortlist_poles_mismatch")

            if req_device and top_device and str(req_device) != str(top_device):
                risk_flags.append("shortlist_device_class_mismatch")

        status = "ok" if not risk_flags else "needs_review"

        out.append(
            {
                "tag": tag,
                "status": status,
                "requirement_snapshot": {
                    "device_class": req_device,
                    "suggested_nominal_a": req_nominal,
                    "trip_curve": req_curve,
                    "poles": req_poles,
                },
                "candidates_count": len(tag_candidates),
                "shortlist_count": len(tag_shortlist),
                "top_shortlist_item": tag_shortlist[0] if tag_shortlist else None,
                "risk_flags": risk_flags,
                "notes": notes,
            }
        )

    return out