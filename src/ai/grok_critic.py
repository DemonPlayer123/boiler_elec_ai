# src/ai/grok_critic.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI
from pydantic import BaseModel, Field


class CriticIssue(BaseModel):
    type: str = Field(description="Тип проблемы")
    severity: str = Field(description="low, medium или high")
    message: str = Field(description="Описание проблемы на русском языке")
    evidence: str = Field(description="Данные, на которые опирается замечание")


class CriticResult(BaseModel):
    critic_verdict: str = Field(
        description="accepted, accepted_with_conditions, manual_review_required или reject_or_recalculate"
    )
    critic_score: float = Field(ge=0, le=1)
    risk_level: str = Field(description="low, medium или high")
    summary: str = Field(description="Краткий итог проверки на русском языке")
    issues: list[CriticIssue]
    recommendation: str = Field(description="Рекомендация на русском языке")


def _compact(value: Any, max_str: int = 2200, max_list: int = 5) -> Any:
    if isinstance(value, str):
        return value[:max_str]
    if isinstance(value, list):
        return [_compact(v, max_str=max_str, max_list=max_list) for v in value[:max_list]]
    if isinstance(value, dict):
        return {k: _compact(v, max_str=max_str, max_list=max_list) for k, v in value.items()}
    return value


def build_critic_input(api_payload: dict[str, Any]) -> dict[str, Any]:
    """
    На вход подается payload FastAPI:
    {
      "ok": true,
      "tag": "...",
      "result": {...}
    }

    На выходе компактный JSON для Grok-критика.
    """
    root = api_payload.get("result") if isinstance(api_payload.get("result"), dict) else api_payload

    candidate = root.get("candidate") or {}
    candidate_options = root.get("candidate_options") or []
    normative_hits = root.get("normative_hits") or root.get("evidence_top") or []
    normative_refs = root.get("normative_refs") or []

    critic_input = {
        "tag": root.get("tag") or api_payload.get("tag"),

        "requirement_full": (
            root.get("requirement_full")
            or root.get("requirement")
            or root.get("requirements")
        ),
        "requirement_ref": root.get("requirement_ref"),

        "candidate": candidate,
        "candidate_options_top5": candidate_options[:5],

        "verdict": root.get("verdict"),
        "confidence": root.get("confidence"),

        "why_this_candidate": root.get("why_this_candidate"),
        "summary_bullets": root.get("summary_bullets") or [],

        "engineering_checks": root.get("engineering_checks") or [],
        "normative_refs": normative_refs[:5],
        "normative_hits_top3": normative_hits[:3],

        "llm_readable_explanation": root.get("llm_readable_explanation") or "",
        "llm_alternative_summary": root.get("llm_alternative_summary") or "",
        "llm_manual_review_note": root.get("llm_manual_review_note") or "",

        "price_data_from_candidate": {
            "price_found": candidate.get("price_found"),
            "price_rub": candidate.get("price_rub"),
            "price_currency": candidate.get("price_currency"),
            "price_article": candidate.get("price_article"),
            "price_source_type": candidate.get("price_source_type"),
            "price_source_domain": candidate.get("price_source_domain"),
            "price_product_url": candidate.get("price_product_url"),
        },
    }

    return _compact(critic_input)


def call_grok_critic(
    api_payload: dict[str, Any],
    model: str = "grok-4.3",
    reasoning_effort: str = "medium",
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise RuntimeError("Не задан XAI_API_KEY.")

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.x.ai/v1",
        timeout=timeout_seconds,
    )

    critic_input = build_critic_input(api_payload)

    system_prompt = """
Ты независимый LLM-критик результата инженерной системы подбора аппаратов защиты.

Твоя задача — НЕ выбирать новый аппарат, НЕ менять candidate и НЕ исправлять расчет.
Твоя задача — проверить согласованность уже сформированного результата.

Проверяй только входные данные:
1. соответствует ли candidate требованию;
2. совпадает ли класс аппарата с типом нагрузки;
3. попадает ли рабочий/расчетный ток в номинал или диапазон уставки;
4. корректно ли число полюсов;
5. достаточна ли отключающая способность;
6. не противоречит ли LLM-объяснение JSON/API;
7. не расходятся ли цена, артикул и источник;
8. относятся ли normative_hits и normative_refs к выбранному решению;
9. есть ли признаки, которые надо отправить в manual_review.

Если есть спорный инженерный случай, ставь manual_review_required.
Если результат в целом пригоден, но есть ограничения, ставь accepted_with_conditions.
Если результат нельзя использовать даже с ручной проверкой, ставь reject_or_recalculate.

Все текстовые поля summary, issues.message, issues.evidence и recommendation пиши на русском языке.
Технические значения critic_verdict, risk_level, severity и type оставляй короткими служебными строками.
Ответ должен быть только структурированным JSON по схеме.
""".strip()

    user_prompt = json.dumps(
        {
            "task": "Выполни внешнюю LLM-критику результата подбора аппарата защиты.",
            "input": critic_input,
        },
        ensure_ascii=False,
    )

    response = client.responses.parse(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        reasoning={"effort": reasoning_effort},
        text_format=CriticResult,
    )

    parsed: Optional[CriticResult] = response.output_parsed
    if parsed is None:
        raise RuntimeError(f"Grok не вернул parsed JSON. Raw: {response.output_text}")

    result = parsed.model_dump()

    usage = getattr(response, "usage", None)
    if usage is not None:
        try:
            result["_usage"] = usage.model_dump()
        except Exception:
            result["_usage"] = str(usage)

    result["_critic_model"] = model
    result["_reasoning_effort"] = reasoning_effort

    return result


def critic_file_path(tag: str, run_dir: str | Path = "data/output/runs/25-05") -> Path:
    tag_norm = str(tag or "").strip().upper()
    return Path(run_dir) / "critic" / f"{tag_norm}.grok_critic.json"


def save_grok_critic_result(
    tag: str,
    result: dict[str, Any],
    run_dir: str | Path = "data/output/runs/25-05",
) -> Path:
    path = critic_file_path(tag, run_dir=run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_saved_grok_critic(
    tag: str,
    run_dir: str | Path = "data/output/runs/25-05",
) -> dict[str, Any] | None:
    path = critic_file_path(tag, run_dir=run_dir)
    if not path.exists():
        return None

    return json.loads(path.read_text(encoding="utf-8"))