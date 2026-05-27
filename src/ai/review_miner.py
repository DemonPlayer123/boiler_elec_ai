from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI


_REVIEW_SCHEMA = {
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
        "source_notes",
    ],
    "additionalProperties": False,
}


def generate_review_summary(
    candidate: dict[str, Any],
    *,
    model: str = "gpt-5.4-mini",
) -> dict[str, Any]:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    payload = {
        "vendor": candidate.get("vendor"),
        "series": candidate.get("series"),
        "model": candidate.get("model"),
        "device_class": candidate.get("device_class"),
    }

    response = client.responses.create(
        model=model,
        tools=[{"type": "web_search"}],
        input=[
            {
                "role": "system",
                "content": (
                    "Ты ищешь открытые отзывы и обсуждения по конкретной линейке аппарата защиты. "
                    "Найди упоминания о надежности, ложных срабатываниях, нагреве, качестве сборки, "
                    "доступности аксессуаров и общем пользовательском опыте. "
                    "Не делай инженерный выбор на основе отзывов. "
                    "Сформируй только осторожную сводку как дополнительный advisory-блок. "
                    "Если данных мало или они спорные, так и скажи."
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
                "schema": _REVIEW_SCHEMA,
            }
        },
    )

    return response.output_parsed