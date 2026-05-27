import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai import OpenAI
from pydantic import BaseModel, Field


class CriticIssue(BaseModel):
    type: str = Field(
        description="Тип проблемы: setting_range_mismatch, price_inconsistency, rag_issue, llm_hallucination, missing_data, other"
    )
    severity: str = Field(description="low, medium или high")
    message: str = Field(description="Краткое описание проблемы")
    evidence: str = Field(description="На какие входные данные опирается замечание")


class CriticResult(BaseModel):
    critic_verdict: str = Field(
        description="accepted, accepted_with_conditions, manual_review_required или reject_or_recalculate"
    )
    critic_score: float = Field(ge=0, le=1, description="Оценка согласованности результата от 0 до 1")
    risk_level: str = Field(description="low, medium или high")
    summary: str = Field(description="Краткий итог проверки")
    issues: List[CriticIssue]
    recommendation: str = Field(description="Что делать дальше: принять, проверить вручную, пересчитать")


def compact(value: Any, max_str: int = 1800, max_list: int = 5) -> Any:
    """Урезает огромные строки/списки, чтобы не сжигать токены."""
    if isinstance(value, str):
        return value[:max_str]
    if isinstance(value, list):
        return [compact(v, max_str=max_str, max_list=max_list) for v in value[:max_list]]
    if isinstance(value, dict):
        return {k: compact(v, max_str=max_str, max_list=max_list) for k, v in value.items()}
    return value


def pick_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Достаёт из API/JSON-ответа только то, что нужно критику.
    Поддерживает два формата:
    1) полный API-ответ: {"ok": true, "tag": "...", "result": {...}}
    2) плоский JSON: {"tag": "...", "candidate": {...}}
    """
    root = raw.get("result") if isinstance(raw.get("result"), dict) else raw

    candidate_options = (
        root.get("candidate_options")
        or root.get("shortlist")
        or root.get("candidates")
        or []
    )

    normative_hits = (
        root.get("normative_hits")
        or root.get("rag_hits")
        or root.get("normative")
        or []
    )

    payload = {
        "tag": root.get("tag") or raw.get("tag"),

        "requirement": (
            root.get("requirement_full")
            or root.get("requirement")
            or root.get("requirements")
            or root.get("requirement_ref")
        ),

        "requirement_ref": root.get("requirement_ref"),

        "candidate": (
            root.get("candidate")
            or root.get("selected_candidate")
        ),

        "candidate_options_top5": candidate_options[:5],

        "verdict": root.get("verdict"),
        "confidence": root.get("confidence"),

        "engineering_checks": (
            root.get("engineering_checks")
            or root.get("checks")
            or root.get("manual_review")
        ),

        "price_data": (
            root.get("price_data")
            or root.get("price")
            or {
                "candidate_price_found": (root.get("candidate") or {}).get("price_found"),
                "candidate_price_rub": (root.get("candidate") or {}).get("price_rub"),
                "candidate_price_article": (root.get("candidate") or {}).get("price_article"),
                "candidate_price_source_type": (root.get("candidate") or {}).get("price_source_type"),
                "candidate_price_source_domain": (root.get("candidate") or {}).get("price_source_domain"),
            }
        ),

        "normative_hits_top3": normative_hits[:3],

        "llm_explanation": (
            root.get("llm_readable_explanation")
            or root.get("readable_explanation")
            or root.get("llm_explanation")
            or root.get("explanation")
            or root.get("answer")
            or root.get("llm_text")
        ),

        "llm_manual_review_note": root.get("llm_manual_review_note"),
        "why_this_candidate": root.get("why_this_candidate"),
    }

    return compact(payload)


def call_grok_critic(payload: Dict[str, Any], model: str) -> CriticResult:
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise RuntimeError("Не задан XAI_API_KEY. Установи переменную окружения.")

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.x.ai/v1",
    )

    system_prompt = """
Ты независимый LLM-критик результата инженерной системы подбора аппаратов защиты.

Твоя задача — НЕ выбирать новый аппарат и НЕ исправлять candidate.
Твоя задача — проверить согласованность уже сформированного результата.

Проверяй только данные из входного JSON:
1. соответствует ли candidate требованию;
2. совпадает ли класс аппарата с типом нагрузки;
3. попадает ли рабочий/расчётный ток в номинал или диапазон уставки;
4. корректны ли полюса;
5. достаточна ли отключающая способность;
6. не противоречит ли LLM-объяснение JSON/API;
7. не расходятся ли цена, артикул и источник;
8. относятся ли normative_hits к выбранному решению.

Если есть спорный инженерный случай, выставляй manual_review_required.
Если всё в целом согласовано, но есть ограничения, выставляй accepted_with_conditions.
Все текстовые поля ответа — summary, issues.message, issues.evidence и recommendation — пиши на русском языке.
Технические enum-значения critic_verdict, risk_level, severity и type оставляй в заданном формате.
Ответ должен быть только структурированным JSON по схеме.
""".strip()

    user_prompt = json.dumps(
        {
            "task": "Проверь результат подбора аппарата защиты и LLM-объяснение.",
            "input": payload,
        },
        ensure_ascii=False,
    )

    # OpenAI-compatible Responses API у xAI.
    response = client.responses.parse(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        text_format=CriticResult,
    )

    parsed: Optional[CriticResult] = response.output_parsed
    if parsed is None:
        raise RuntimeError(f"Не удалось получить parsed JSON. Raw output: {response.output_text}")

    return parsed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Путь к API/JSON-ответу по тегу")
    parser.add_argument("--out", required=True, help="Куда сохранить critic JSON")
    parser.add_argument("--model", default="grok-4.3")
    args = parser.parse_args()

    raw_path = Path(args.input)
    out_path = Path(args.out)

    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    payload = pick_payload(raw)

    result = call_grok_critic(payload, model=args.model)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8",
    )

    print("OK")
    print(f"saved: {out_path}")
    print(f"verdict: {result.critic_verdict}")
    print(f"score: {result.critic_score}")
    print(f"risk: {result.risk_level}")
    if result.issues:
        print("issues:")
        for issue in result.issues:
            print(f"  - [{issue.severity}] {issue.type}: {issue.message}")


if __name__ == "__main__":
    main()