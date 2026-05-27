from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from openai import OpenAI


OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL_NAME = "gpt-5.4-mini"

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "review_summary": {"type": "string"},
        "positive_points": {
            "type": "array",
            "items": {"type": "string"},
        },
        "negative_points": {
            "type": "array",
            "items": {"type": "string"},
        },
        "risk_flags": {
            "type": "array",
            "items": {"type": "string"},
        },
        "manual_caution": {"type": "string"},
        "source_notes": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "review_summary",
        "positive_points",
        "negative_points",
        "risk_flags",
        "manual_caution",
        "source_notes",
    ],
    "additionalProperties": False,
}


def _load_json(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str | Path, data: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _resolve_selected_candidate(row: dict[str, Any]) -> dict[str, Any]:
    """
    Берём основной candidate, но если в candidate_options есть rank=1
    с более полными полями, дополняем candidate из него.
    """
    base = dict(row.get("candidate") or {})
    options = row.get("candidate_options") or []

    if not isinstance(options, list):
        return base

    selected_opt = None

    for opt in options:
        if not isinstance(opt, dict):
            continue
        if str(opt.get("rank") or "") == "1":
            selected_opt = opt
            break

    if selected_opt is None:
        return base

    merged = dict(base)
    for key, value in selected_opt.items():
        if value not in (None, "", []):
            merged[key] = value

    return merged

def _extract_candidate_payload(row: dict[str, Any]) -> dict[str, Any]:
    candidate = _resolve_selected_candidate(row)
    requirement = row.get("requirement_ref") or {}
    return {
        "tag": row.get("tag"),
        "vendor": candidate.get("vendor"),
        "series": candidate.get("series"),
        "model": candidate.get("model"),
        "device_class": candidate.get("device_class"),
        "rated_current_a": candidate.get("rated_current_a"),
        "poles": candidate.get("poles"),
        "trip_curve": candidate.get("trip_curve"),
        "breaking_capacity_ka": candidate.get("breaking_capacity_ka"),
        "rcd_ma": candidate.get("rcd_ma"),
        "rcd_type": candidate.get("rcd_type"),
        "current_range_a": candidate.get("current_range_a"),
        "poles_label": candidate.get("poles_label"),
        "requirement_device_class": requirement.get("device_class"),
        "requirement_nominal_a": requirement.get("suggested_nominal_a"),
    }


def _generate_review_summary(
    client: OpenAI,
    row: dict[str, Any],
    *,
    model: str,
) -> dict[str, Any]:
    payload = _extract_candidate_payload(row)

    response = client.responses.create(
        model=model,
        tools=[{"type": "web_search"}],
        input=[
            {
                "role": "system",
                "content": (
                    "Ты ищешь открытые отзывы и обсуждения по конкретной линейке аппарата защиты. "
                    "Используй веб-поиск. "
                    "Ищи реальные упоминания о надежности, ложных срабатываниях, нагреве, качестве сборки, "
                    "удобстве монтажа, доступности аксессуаров и общем опыте эксплуатации. "
                    "Если данных мало, честно напиши, что их недостаточно. "
                    "Не делай инженерный выбор вместо расчетов и нормативки. "
                    "Пиши на русском языке."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, indent=2),
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "review_summary",
                "strict": True,
                "schema": REVIEW_SCHEMA,
            }
        },
    )

    raw_text = response.output_text or "{}"

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        parsed = {
            "review_summary": raw_text.strip(),
            "positive_points": [],
            "negative_points": [],
            "risk_flags": [],
            "manual_caution": "",
            "source_notes": [],
        }

    return {
        "tag": payload.get("tag"),
        "candidate_vendor": payload.get("vendor"),
        "candidate_series": payload.get("series"),
        "candidate_model": payload.get("model"),
        "review_summary": str(parsed.get("review_summary") or "").strip(),
        "positive_points": parsed.get("positive_points") or [],
        "negative_points": parsed.get("negative_points") or [],
        "risk_flags": parsed.get("risk_flags") or [],
        "manual_caution": str(parsed.get("manual_caution") or "").strip(),
        "source_notes": parsed.get("source_notes") or [],
        "llm_model": model,
    }


def enrich_reviews(
    rows: list[dict[str, Any]],
    *,
    model: str,
    only_tags: set[str] | None = None,
) -> list[dict[str, Any]]:
    client = OpenAI(
        api_key=OPENAI_API_KEY,
        timeout=120.0,
    )
    out: list[dict[str, Any]] = []

    for idx, row in enumerate(rows, start=1):
        tag = str(row.get("tag") or "").strip()

        if only_tags and tag not in only_tags:
            continue

        try:
            review_row = _generate_review_summary(client, row, model=model)
            out.append(review_row)
            print(f"[{idx}/{len(rows)}] OK: {tag}")
        except Exception as e:
            msg = str(e)
            review_error = f"{type(e).__name__}: {e}"

            if "timed out" in msg.lower():
                review_error = "request_timed_out"

            out.append({
                "tag": tag,
                "candidate_vendor": (row.get("candidate") or {}).get("vendor"),
                "candidate_series": (row.get("candidate") or {}).get("series"),
                "candidate_model": (row.get("candidate") or {}).get("model"),
                "review_error": review_error,
                "llm_model": model,
            })
            print(f"[{idx}/{len(rows)}] ERROR: {tag} -> {e}")

    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_json", required=True)
    parser.add_argument("--out_json", required=True)
    parser.add_argument("--model", default=MODEL_NAME)
    parser.add_argument("--tags", default="", help="Например: ГГ.1,К5,К6")
    args = parser.parse_args()

    rows = _load_json(args.input_json)
    if not isinstance(rows, list):
        raise ValueError("input_json must contain a list")

    only_tags = None
    if args.tags.strip():
        only_tags = {x.strip() for x in args.tags.split(",") if x.strip()}

    result = enrich_reviews(
        rows,
        model=args.model,
        only_tags=only_tags,
    )
    _save_json(args.out_json, result)
    print(f"saved: {args.out_json}")


if __name__ == "__main__":
    main()