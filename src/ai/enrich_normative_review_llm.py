# from __future__ import annotations

# import argparse
# import json
# import os
# import time
# from pathlib import Path
# from typing import Any

# from openai import OpenAI


# EXPLANATION_SCHEMA = {
#     "type": "object",
#     "properties": {
#         "readable_explanation": {"type": "string"},
#         "alternative_summary": {"type": "string"},
#         "manual_review_note": {"type": "string"},
#     },
#     "required": [
#         "readable_explanation",
#         "alternative_summary",
#         "manual_review_note",
#     ],
#     "additionalProperties": False,
# }


# def _load_json(path: str | Path) -> Any:
#     with open(path, "r", encoding="utf-8") as f:
#         return json.load(f)


# def _save_json(path: str | Path, data: Any) -> None:
#     Path(path).parent.mkdir(parents=True, exist_ok=True)
#     with open(path, "w", encoding="utf-8") as f:
#         json.dump(data, f, ensure_ascii=False, indent=2)


# def _build_payload(row: dict[str, Any]) -> dict[str, Any]:
#     """
#     В модель отправляем только фактическую, уже рассчитанную информацию.
#     Никакого сырого мусора и лишних полей.
#     """
#     return {
#         "tag": row.get("tag"),
#         "requirement_ref": row.get("requirement_ref"),
#         "candidate": row.get("candidate"),
#         "verdict": row.get("verdict"),
#         "confidence": row.get("confidence"),
#         "summary_bullets": row.get("summary_bullets"),
#         "engineering_checks": row.get("engineering_checks"),
#         "candidate_options": row.get("candidate_options"),
#     }


# def _generate_llm_explanation(
#     client: OpenAI,
#     row: dict[str, Any],
#     *,
#     model: str,
# ) -> dict[str, str]:
#     payload = _build_payload(row)

#     response = client.responses.create(
#         model=model,
#         input=[
#             {
#                 "role": "system",
#                 "content": (
#                     "Ты формируешь инженерное объяснение выбора аппарата защиты. "
#                     "Используй только данные из входного JSON. "
#                     "Не придумывай новые факты, нормативные пункты, токи, модели, отзывы или выводы. "
#                     "Если во входе есть manual_review, обязательно отрази это как условие ручной проверки. "
#                     "Если есть альтернативы, кратко и предметно объясни, почему они не были выбраны. "
#                     "Пиши на русском, деловым техническим стилем. "
#                     "Не пересказывай JSON механически, а делай связный текст."
#                 ),
#             },
#             {
#                 "role": "user",
#                 "content": json.dumps(payload, ensure_ascii=False, indent=2),
#             },
#         ],
#         text={
#             "format": {
#                 "type": "json_schema",
#                 "name": "normative_explanation",
#                 "strict": True,
#                 "schema": EXPLANATION_SCHEMA,
#             }
#         },
#     )

#     parsed = response.output_parsed
#     if not isinstance(parsed, dict):
#         raise ValueError("LLM returned unexpected structured output")
#     return {
#         "readable_explanation": str(parsed.get("readable_explanation") or "").strip(),
#         "alternative_summary": str(parsed.get("alternative_summary") or "").strip(),
#         "manual_review_note": str(parsed.get("manual_review_note") or "").strip(),
#     }


# def enrich_normative_review(
#     rows: list[dict[str, Any]],
#     *,
#     model: str,
#     sleep_sec: float = 0.0,
#     only_tags: set[str] | None = None,
# ) -> list[dict[str, Any]]:
#     client = OpenAI(api_key="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")
#     out: list[dict[str, Any]] = []

#     for idx, row in enumerate(rows, start=1):
#         tag = str(row.get("tag") or "").strip()

#         if only_tags and tag not in only_tags:
#             out.append(row)
#             continue

#         try:
#             llm = _generate_llm_explanation(client, row, model=model)
#             enriched = dict(row)
#             enriched["llm_readable_explanation"] = llm["readable_explanation"]
#             enriched["llm_alternative_summary"] = llm["alternative_summary"]
#             enriched["llm_manual_review_note"] = llm["manual_review_note"]
#             enriched["llm_model"] = model
#             out.append(enriched)
#             print(f"[{idx}/{len(rows)}] OK: {tag}")
#         except Exception as e:
#             enriched = dict(row)
#             enriched["llm_error"] = f"{type(e).__name__}: {e}"
#             out.append(enriched)
#             print(f"[{idx}/{len(rows)}] ERROR: {tag} -> {e}")

#         if sleep_sec > 0:
#             time.sleep(sleep_sec)

#     return out


# def main() -> None:
#     parser = argparse.ArgumentParser()
#     parser.add_argument("--input_json", required=True)
#     parser.add_argument("--out_json", required=True)
#     parser.add_argument("--model", default="gpt-5.4-mini")
#     parser.add_argument(
#         "--tags",
#         default="",
#         help="Опционально: список тегов через запятую, например ГГ.1,К5,К6",
#     )
#     parser.add_argument("--sleep_sec", type=float, default=0.0)
#     args = parser.parse_args()

#     rows = _load_json(args.input_json)
#     if not isinstance(rows, list):
#         raise ValueError("input_json must contain a list")

#     only_tags = None
#     if args.tags.strip():
#         only_tags = {x.strip() for x in args.tags.split(",") if x.strip()}

#     enriched = enrich_normative_review(
#         rows,
#         model=args.model,
#         sleep_sec=args.sleep_sec,
#         only_tags=only_tags,
#     )
#     _save_json(args.out_json, enriched)
#     print(f"saved: {args.out_json}")


# if __name__ == "__main__":
#     main()


from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types


# ВРЕМЕННО ДЛЯ ПРОВЕРКИ.
# После теста лучше вынести в .env или переменную окружения GEMINI_API_KEY.
GEMINI_API_KEY = "AIzaSyDLSStGYo1dcHRPsbJ0ZEE23MURExXy7-Y"

MODEL_NAME = "gemini-2.5-flash"

EXPLANATION_SCHEMA = {
    "type": "object",
    "properties": {
        "readable_explanation": {"type": "string"},
        "alternative_summary": {"type": "string"},
        "manual_review_note": {"type": "string"},
    },
    "required": [
        "readable_explanation",
        "alternative_summary",
        "manual_review_note",
    ],
}


def _load_json(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str | Path, data: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _build_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "tag": row.get("tag"),
        "requirement_ref": row.get("requirement_ref"),
        "candidate": row.get("candidate"),
        "verdict": row.get("verdict"),
        "confidence": row.get("confidence"),
        "summary_bullets": row.get("summary_bullets"),
        "engineering_checks": row.get("engineering_checks"),
        "candidate_options": row.get("candidate_options"),
    }


def _generate_llm_explanation(
    client: genai.Client,
    row: dict[str, Any],
    *,
    model: str,
) -> dict[str, str]:
    payload = _build_payload(row)

    prompt = (
        "Ты формируешь инженерное объяснение выбора аппарата защиты.\n"
        "Используй только данные из входного JSON.\n"
        "Не придумывай новые факты, нормативные пункты, токи, модели, отзывы или выводы.\n"
        "Если во входе есть manual_review, обязательно отрази это как условие ручной проверки.\n"
        "Если есть альтернативы, кратко и предметно объясни, почему они не были выбраны.\n"
        "Пиши на русском, деловым техническим стилем.\n\n"
        f"Входной JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=EXPLANATION_SCHEMA,
            temperature=0.2,
        ),
    )

    text = response.text or "{}"
    parsed = json.loads(text)

    return {
        "readable_explanation": str(parsed.get("readable_explanation") or "").strip(),
        "alternative_summary": str(parsed.get("alternative_summary") or "").strip(),
        "manual_review_note": str(parsed.get("manual_review_note") or "").strip(),
    }


def enrich_normative_review(
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
            out.append(row)
            continue

        try:
            llm = _generate_llm_explanation(client, row, model=model)
            enriched = dict(row)
            enriched["llm_readable_explanation"] = llm["readable_explanation"]
            enriched["llm_alternative_summary"] = llm["alternative_summary"]
            enriched["llm_manual_review_note"] = llm["manual_review_note"]
            enriched["llm_model"] = model
            out.append(enriched)
            print(f"[{idx}/{len(rows)}] OK: {tag}")
        except Exception as e:
            enriched = dict(row)
            enriched["llm_error"] = f"{type(e).__name__}: {e}"
            out.append(enriched)
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

    enriched = enrich_normative_review(
        rows,
        model=args.model,
        only_tags=only_tags,
    )
    _save_json(args.out_json, enriched)
    print(f"saved: {args.out_json}")


if __name__ == "__main__":
    main()