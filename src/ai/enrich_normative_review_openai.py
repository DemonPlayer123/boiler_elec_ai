from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from openai import OpenAI


OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL_NAME = "gpt-5.4-mini"

EXPLANATION_SCHEMA = {
    "type": "object",
    "properties": {
        "readable_explanation": {"type": "string"},
        "alternative_summary": {"type": "string"},
        "manual_review_note": {"type": "string"},
        "selected_model_echo": {"type": "string"},
        "selected_vendor_echo": {"type": "string"},
        "selected_series_echo": {"type": "string"},
    },
    "required": [
        "readable_explanation",
        "alternative_summary",
        "manual_review_note",
        "selected_model_echo",
        "selected_vendor_echo",
        "selected_series_echo",
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
    base = dict(row.get("candidate") or {})
    options = row.get("candidate_options") or []
    if not isinstance(options, list):
        return base

    def _norm_text(v: Any) -> str:
        return str(v or "").strip().upper()


    def _norm_float_text(v: Any) -> str:
        if v in (None, ""):
            return ""
        try:
            f = float(v)
            return str(int(f)) if f.is_integer() else str(f)
        except Exception:
            return str(v).strip().upper()


    def _current_range_text(d: dict[str, Any]) -> str:
        rng = d.get("current_range_a") or {}
        if not isinstance(rng, dict):
            return ""

        lo = _norm_float_text(rng.get("min"))
        hi = _norm_float_text(rng.get("max"))
        if not lo and not hi:
            return ""

        return f"{lo}-{hi}"


    def _key(d: dict[str, Any]) -> tuple[str, str, str, str, str, str, str, str, str]:
        """
        Расширенный identity key аппарата.

        Нельзя матчить только по vendor/series/model:
        у MCB/RCBO/MPCB могут совпадать серия и модельная строка,
        но отличаться ток, полюса, характеристика, Icu или rcd_ma.
        """
        return (
            _norm_text(d.get("vendor")),
            _norm_text(d.get("series")),
            _norm_text(d.get("model")),
            _norm_text(d.get("device_class")),
            _norm_float_text(d.get("rated_current_a")),
            _norm_text(d.get("poles")),
            _norm_text(d.get("trip_curve")),
            _norm_float_text(d.get("breaking_capacity_ka")),
            _norm_text(d.get("rcd_ma") or _current_range_text(d)),
        )

    base_key = _key(base)

    matched = None
    for opt in options:
        if _key(opt) == base_key:
            matched = opt
            break

    if matched is None:
        for opt in options:
            if str(opt.get("rank") or "") == "1":
                matched = opt
                break

    if matched:
        merged = dict(base)
        for fld in [
            "vendor",
            "series",
            "model",
            "device_class",
            "rated_current_a",
            "poles",
            "poles_label",
            "trip_curve",
            "breaking_capacity_ka",
            "rcd_ma",
            "rcd_type",
            "current_range_a",
            "price_found",
            "price_rub",
            "price_currency",
            "price_article",
            "price_designation",
            "price_title",
            "price_source_domain",
            "price_source_type",
            "price_product_url",
            "price_match_key",
        ]:
            if matched.get(fld) not in (None, "", []):
                merged[fld] = matched.get(fld)
        return merged

    return base


def _build_payload(row: dict[str, Any]) -> dict[str, Any]:
    selected = _resolve_selected_candidate(row)
    return {
        "tag": row.get("tag"),
        "selected_candidate": {
            "vendor": selected.get("vendor"),
            "series": selected.get("series"),
            "model": selected.get("model"),
            "device_class": selected.get("device_class"),
            "rated_current_a": selected.get("rated_current_a"),
            "poles": selected.get("poles"),
            "trip_curve": selected.get("trip_curve"),
            "breaking_capacity_ka": selected.get("breaking_capacity_ka"),
            "price_found": selected.get("price_found"),
            "price_rub": selected.get("price_rub"),
            "price_currency": selected.get("price_currency"),
            "price_article": selected.get("price_article"),
        },
        "requirement_ref": row.get("requirement_ref"),
        "verdict": row.get("verdict"),
        "confidence": row.get("confidence"),
        "summary_bullets": row.get("summary_bullets"),
        "engineering_checks": row.get("engineering_checks"),
        "candidate_options": row.get("candidate_options"),
    }

def _generate_llm_explanation(
    client: OpenAI,
    row: dict[str, Any],
    *,
    model: str,
) -> dict[str, str]:
    payload = _build_payload(row)

    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    "Ты формируешь инженерное объяснение выбора аппарата защиты. "
                    "Используй только данные из входного JSON. "
                    "Основной выбранный аппарат находится только в поле `selected_candidate`. "
                    "Никогда не называй выбранным аппарат из `candidate_options`. "
                    "`candidate_options` используй только как альтернативы для краткого сравнения. "
                    "Нельзя подменять производителя, серию или модель выбранного аппарата. "
                    "Если `selected_candidate.price_found=true`, нельзя писать, что цена отсутствует. "
                    "Если `selected_candidate.price_found=false`, нельзя писать, что цена найдена. "
                    "Не придумывай новые модели, цены, артикулы, выводы и нормативные факты. "
                    "Пиши на русском языке, деловым техническим стилем. "
                    "Верни поле `selected_model_echo` со значением в точности как `selected_candidate.model`, без изменений, сокращений и перефразирования. "
                    "Верни поле `selected_vendor_echo` со значением в точности как `selected_candidate.vendor`. "
                    "Верни поле `selected_series_echo` со значением в точности как `selected_candidate.series`."
                )
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, indent=2),
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "normative_explanation",
                "strict": True,
                "schema": EXPLANATION_SCHEMA,
            }
        },
    )

    raw_text = response.output_text or "{}"
    parsed = json.loads(raw_text)

    selected = payload.get("selected_candidate") or {}
    selected_model = str(selected.get("model") or "").strip()
    selected_vendor = str(selected.get("vendor") or "").strip()
    selected_series = str(selected.get("series") or "").strip()

    if str(parsed.get("selected_model_echo") or "").strip() != selected_model:
        raise ValueError("llm_selected_model_echo_mismatch")

    if str(parsed.get("selected_vendor_echo") or "").strip() != selected_vendor:
        raise ValueError("llm_selected_vendor_echo_mismatch")

    if str(parsed.get("selected_series_echo") or "").strip() != selected_series:
        raise ValueError("llm_selected_series_echo_mismatch")
    
    full_text = "\n".join([
        str(parsed.get("readable_explanation") or ""),
        str(parsed.get("alternative_summary") or ""),
        str(parsed.get("manual_review_note") or ""),
    ]).lower()

    selected_model_l = selected_model.lower()
    selected_vendor_l = selected_vendor.lower()
    selected_series_l = selected_series.lower()

    # В тексте обязательно должен фигурировать выбранный аппарат
    if selected_model_l and selected_model_l not in full_text:
        raise ValueError("llm_selected_model_not_in_text")

    if selected_vendor_l and selected_vendor_l not in full_text:
        raise ValueError("llm_selected_vendor_not_in_text")

    if selected_series_l and selected_series_l not in full_text:
        raise ValueError("llm_selected_series_not_in_text")

    # Проверка на противоречие по цене
    price_found = bool(selected.get("price_found"))
    if price_found and ("цена не найд" in full_text or "price_found=false" in full_text):
        raise ValueError("llm_price_contradiction")

    if (not price_found) and ("цена найд" in full_text or "price_found=true" in full_text):
        raise ValueError("llm_price_contradiction")
    

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
    client = OpenAI(api_key=OPENAI_API_KEY)
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