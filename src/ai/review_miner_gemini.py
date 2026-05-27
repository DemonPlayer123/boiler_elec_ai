from __future__ import annotations

import time
import random

import argparse
import json
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types


# Временно можно держать ключ тут для отладки.
# Потом вынесем в .env / GEMINI_API_KEY.
GEMINI_API_KEY = "AIzaSyDLSStGYo1dcHRPsbJ0ZEE23MURExXy7-Y"

MODEL_NAME = "gemini-2.5-flash"

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
        "source_notes"
    ],
}


def _load_json(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str | Path, data: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _extract_candidate_payload(row: dict[str, Any]) -> dict[str, Any]:
    candidate = row.get("candidate") or {}
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
        "requirement_device_class": requirement.get("device_class"),
        "requirement_nominal_a": requirement.get("suggested_nominal_a"),
    }


def _build_prompt(payload: dict[str, Any]) -> str:
    return (
    "Сформируй осторожную сводку по открытым отзывам и обсуждениям в интернете "
    "для конкретной линейки аппарата защиты.\n"
    "Обязательно используй веб-поиск.\n"
    "Ищи только реальные открытые источники: форумы, карточки товаров с отзывами, обсуждения специалистов, обзоры.\n"
    "Не выдумывай отзывы и не обобщай без найденных источников.\n"
    "Если по конкретной модели данных мало, разрешается использовать данные по той же серии, но это нужно явно указать.\n"
    "Отдельно выдели: надежность, ложные срабатывания, нагрев, качество сборки, удобство монтажа, доступность аксессуаров.\n"
    "Если нет найденных отзывов, не делай выводы о качестве бренда в целом без веб-источников.\n"
    "Если найденных данных недостаточно, так и напиши.\n"
    "Пиши на русском.\n\n"
    f"Кандидат:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )

def _generate_with_retry(
    client: genai.Client,
    *,
    model: str,
    contents: str,
    max_attempts: int = 5,
):
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0.2,
                ),
            )
        except Exception as e:
            last_error = e
            msg = str(e)

            # Ретраим только временные ошибки capacity/unavailable
            retryable = (
                "503" in msg
                or "UNAVAILABLE" in msg
                or "high demand" in msg.lower()
                or "overloaded" in msg.lower()
            )

            if not retryable or attempt == max_attempts:
                raise

            sleep_sec = min(2 ** (attempt - 1), 20) + random.uniform(0, 0.7)
            print(f"retry {attempt}/{max_attempts} after {sleep_sec:.1f}s بسبب 503/high demand")
            time.sleep(sleep_sec)

    raise last_error

def _generate_review_summary(
    client: genai.Client,
    row: dict[str, Any],
    *,
    model: str,
) -> dict[str, Any]:
    payload = _extract_candidate_payload(row)
    prompt = _build_prompt(payload)

    response = _generate_with_retry(
        client,
        model=model,
        contents=prompt,
    )

    grounding = None
    if getattr(response, "candidates", None):
        cand0 = response.candidates[0]
        grounding = getattr(cand0, "grounding_metadata", None)

    web_queries = []
    sources = []

    if grounding:
        if getattr(grounding, "web_search_queries", None):
            web_queries = list(grounding.web_search_queries)

        if getattr(grounding, "grounding_chunks", None):
            for ch in grounding.grounding_chunks:
                web = getattr(ch, "web", None)
                if web:
                    sources.append({
                        "title": getattr(web, "title", None),
                        "uri": getattr(web, "uri", None),
                    })

    return {
        "tag": payload.get("tag"),
        "candidate_vendor": payload.get("vendor"),
        "candidate_series": payload.get("series"),
        "candidate_model": payload.get("model"),
        "raw_grounded_text": (response.text or "").strip(),
        "web_search_queries": web_queries,
        "sources": sources,
        "llm_model": model,
    }


def enrich_reviews(
    rows: list[dict[str, Any]],
    *,
    model: str,
    only_tags: set[str] | None = None,
) -> list[dict[str, Any]]:
    client = genai.Client(api_key=GEMINI_API_KEY)
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
            out.append({
                "tag": tag,
                "candidate_vendor": (row.get("candidate") or {}).get("vendor"),
                "candidate_series": (row.get("candidate") or {}).get("series"),
                "candidate_model": (row.get("candidate") or {}).get("model"),
                "review_error": f"{type(e).__name__}: {e}",
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