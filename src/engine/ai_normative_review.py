from __future__ import annotations


def build_ai_normative_review(
    rag_summary: list[dict],
    requirements: list[dict],
) -> list[dict]:
    req_by_tag = {x.get("tag"): x for x in requirements}
    out = []

    for row in rag_summary:
        tag = row.get("tag")
        req = req_by_tag.get(tag, {})

        out.append({
            "tag": tag,
            "device_snapshot": row.get("requirement_snapshot") or {},
            "requirement_snapshot": {
                "device_class": req.get("device_class"),
                "trip_curve": req.get("trip_curve"),
                "poles": req.get("poles"),
                "breaking_capacity_ka": req.get("breaking_capacity_ka"),
                "suggested_nominal_a": req.get("suggested_nominal_a"),
            },
            "citations": row.get("citations") or [],
            "risk_flags": row.get("risk_flags") or [],
            "recommendation": row.get("recommendation"),
            "explanation_text": row.get("explanation_text"),
        })

    return out