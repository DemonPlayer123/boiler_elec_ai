from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from openai import OpenAI

from concurrent.futures import ThreadPoolExecutor, as_completed


OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL_NAME = "gpt-5.4-mini"

PRICE_SCHEMA = {
    "type": "object",
    "properties": {
        "price_found": {"type": "boolean"},
        "price_value": {"type": "string"},
        "price_article": {"type": "string"},
        "price_currency": {"type": "string"},
        "price_source_type": {"type": "string"},
        "price_source_name": {"type": "string"},
        "price_url_note": {"type": "string"},
        "price_comment": {"type": "string"},
        "price_designation": {"type": "string"},
        "price_title": {"type": "string"},
    },
    "required": [
        "price_found",
        "price_value",
        "price_article",
        "price_currency",
        "price_source_type",
        "price_source_name",
        "price_url_note",
        "price_comment",
        "price_designation",
        "price_title",
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


def _official_sources_for_vendor(vendor: str) -> dict[str, Any]:
    v = (vendor or "").strip().upper()
    fallback_domains = ["etm.ru", "vseinstrumenti.ru"]

    if v == "CHINT":
        return {
            "domains": ["chint.ru", "chint-electric.ru", *fallback_domains],
            "hint": (
                "Сначала ищи цену на официальных российских сайтах CHINT: "
                "chint.ru и chint-electric.ru. "
                "Если открытой цены там нет, ищи на etm.ru и vseinstrumenti.ru. "
                "Нужна цена в рублях для российского рынка."
            ),
        }

    if v == "DEKRAFT":
        return {
            "domains": ["dek.ru", "dekraft.com", *fallback_domains],
            "hint": (
                "Сначала ищи цену на официальных сайтах DEKraft: "
                "dek.ru и dekraft.com. "
                "Если открытой цены там нет, ищи на etm.ru и vseinstrumenti.ru. "
                "Нужна цена в рублях для российского рынка."
            ),
        }

    if v == "KEAZ":
        return {
            "domains": ["keaz.ru", *fallback_domains],
            "hint": (
                "Сначала ищи цену на официальном сайте KEAZ: keaz.ru. "
                "Если открытой цены нет, ищи на etm.ru и vseinstrumenti.ru. "
                "Нужна цена в рублях для российского рынка."
            ),
        }

    return {
        "domains": fallback_domains,
        "hint": (
            "Сначала ищи официальный сайт производителя. "
            "Если цены нет, ищи на etm.ru и vseinstrumenti.ru. "
            "Нужна цена в рублях."
        ),
    }


def _extract_candidate_payload(
    row: dict[str, Any],
    candidate_row: dict[str, Any],
) -> dict[str, Any]:
    requirement = row.get("requirement_ref") or {}
    official = _official_sources_for_vendor(str(candidate_row.get("vendor") or ""))

    return {
        "tag": row.get("tag"),
        "vendor": candidate_row.get("vendor"),
        "series": candidate_row.get("series"),
        "model": candidate_row.get("model"),
        "device_class": candidate_row.get("device_class"),
        "rated_current_a": candidate_row.get("rated_current_a"),
        "poles": candidate_row.get("poles"),
        "poles_label": candidate_row.get("poles_label"),
        "trip_curve": candidate_row.get("trip_curve"),
        "breaking_capacity_ka": candidate_row.get("breaking_capacity_ka"),
        "rcd_ma": candidate_row.get("rcd_ma"),
        "rcd_type": candidate_row.get("rcd_type"),
        "current_range_a": candidate_row.get("current_range_a"),
        "rank": candidate_row.get("rank"),
        "requirement_device_class": requirement.get("device_class"),
        "requirement_nominal_a": requirement.get("suggested_nominal_a"),
        "official_domains": official["domains"],
        "official_hint": official["hint"],
    }


def _generate_price_summary(
    client: OpenAI,
    payload: dict[str, Any],
    *,
    model: str,
) -> dict[str, Any]:
    response = client.responses.create(
        model=model,
        tools=[{"type": "web_search"}],
        input=[
            {
                "role": "system",
                "content": (
                    "Ты ищешь цену на конкретный аппарат защиты по открытым интернет-источникам. "
                    "Сначала строго ищи на официальных сайтах производителя, указанных во входном JSON. "
                    "Если открытая цена там отсутствует, ищи на российских сайтах продавцов etm.ru и vseinstrumenti.ru. "
                    "Ищи именно цену в рублях для российского рынка. "
                    "Не используй иностранные прайс-листы и цены в других валютах, если есть российские источники. "
                    "Если цена найдена не на официальном сайте, а у продавца, явно отметь это. "
                    "Если цена не найдена явно, честно укажи, что цена не найдена. "
                    "Не выдумывай цену. Верни только JSON."
                    "- `price_article`: только короткий артикул / SKU / код товара, если он явно выделен отдельно."
                    "- `price_designation`: полное обозначение изделия, если на сайте производителя именно оно используется как основной идентификатор товара."
                    "- `price_title`: человекочитаемое название карточки товара."
                    "- Не записывай в `price_article` единицы измерения (`шт.`, `компл.`), длинные описательные названия и маркетинговые заголовки."
                    "- Если отдельного артикула нет, оставляй `price_article` пустым, но заполняй `price_designation`."
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
                "name": "price_summary",
                "strict": True,
                "schema": PRICE_SCHEMA,
            }
        },
    )

    raw_text = response.output_text or "{}"
    parsed = json.loads(raw_text)
    
    return {
        "tag": payload.get("tag"),
        "rank": payload.get("rank"),

        "candidate_vendor": payload.get("vendor"),
        "candidate_series": payload.get("series"),
        "candidate_model": payload.get("model"),
        "candidate_device_class": payload.get("device_class"),
        "candidate_rated_current_a": payload.get("rated_current_a"),
        "candidate_poles": payload.get("poles"),
        "candidate_poles_label": payload.get("poles_label"),
        "candidate_trip_curve": payload.get("trip_curve"),
        "candidate_breaking_capacity_ka": payload.get("breaking_capacity_ka"),
        "candidate_rcd_ma": payload.get("rcd_ma"),
        "candidate_rcd_type": payload.get("rcd_type"),
        "candidate_current_range_a": payload.get("current_range_a"),

        "price_found": bool(parsed.get("price_found")),
        "price_value": str(parsed.get("price_value") or "").strip(),
        "price_article": str(parsed.get("price_article") or "").strip(),
        "price_designation": str(parsed.get("price_designation") or "").strip(),
        "price_title": str(parsed.get("price_title") or "").strip(),
        "price_currency": str(parsed.get("price_currency") or "RUB").strip(),
        "price_source_type": str(parsed.get("price_source_type") or "").strip(),
        "price_source_name": str(parsed.get("price_source_name") or "").strip(),
        "price_url_note": str(parsed.get("price_url_note") or "").strip(),
        "price_comment": str(parsed.get("price_comment") or "").strip(),
        "llm_model": model,
    }

    # return {
    #     "tag": payload.get("tag"),
    #     "rank": payload.get("rank"),
    #     "candidate_vendor": payload.get("vendor"),
    #     "candidate_series": payload.get("series"),
    #     "candidate_model": payload.get("model"),
    #     "price_found": bool(parsed.get("price_found")),
    #     "price_value": str(parsed.get("price_value") or "").strip(),
    #     "price_article": str(parsed.get("price_article") or "").strip(),
    #     "price_currency": str(parsed.get("price_currency") or "").strip(),
    #     "price_source_type": str(parsed.get("price_source_type") or "").strip(),
    #     "price_source_name": str(parsed.get("price_source_name") or "").strip(),
    #     "price_url_note": str(parsed.get("price_url_note") or "").strip(),
    #     "price_comment": str(parsed.get("price_comment") or "").strip(),
    #     "llm_model": model,
    # }


def enrich_prices(
    rows: list[dict[str, Any]],
    *,
    model: str,
    only_tags: set[str] | None = None,
    max_candidates_per_tag: int = 5,
) -> list[dict[str, Any]]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")

    client = OpenAI(
        api_key=OPENAI_API_KEY,
        timeout=120.0,
    )

    out: list[dict[str, Any]] = []

    for idx, row in enumerate(rows, start=1):
        tag = str(row.get("tag") or "").strip()

        if only_tags and tag not in only_tags:
            continue

        candidate_options = row.get("candidate_options") or []
        if not isinstance(candidate_options, list):
            candidate_options = []

        def _safe_price_for_candidate(cand: dict[str, Any]) -> dict[str, Any]:
            payload = _extract_candidate_payload(row, cand)

            try:
                return _generate_price_summary(client, payload, model=model)
            except Exception as e:
                msg = str(e)
                price_error = f"{type(e).__name__}: {e}"

                if "timed out" in msg.lower():
                    price_error = "request_timed_out"

                return {
                    "tag": payload.get("tag"),
                    "rank": payload.get("rank"),
                    "candidate_vendor": payload.get("vendor"),
                    "candidate_series": payload.get("series"),
                    "candidate_model": payload.get("model"),
                    "price_error": price_error,
                    "llm_model": model,
                }

        candidates = [
            cand
            for cand in candidate_options[:max_candidates_per_tag]
            if isinstance(cand, dict)
        ]

        prices_by_rank: dict[str, dict[str, Any]] = {}

        with ThreadPoolExecutor(max_workers=min(5, len(candidates) or 1)) as pool:
            future_map = {
                pool.submit(_safe_price_for_candidate, cand): cand
                for cand in candidates
            }

            for future in as_completed(future_map):
                price_row = future.result()
                rk = str(price_row.get("rank") or "")
                prices_by_rank[rk] = price_row

        prices = [
            prices_by_rank.get(str(cand.get("rank") or ""))
            for cand in candidates
            if prices_by_rank.get(str(cand.get("rank") or ""))
        ]

        out.append({
            "tag": tag,
            "prices": prices,
            "llm_model": model,
        })
        print(f"[{idx}/{len(rows)}] OK: {tag} -> prices={len(prices)}")

    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_json", required=True)
    parser.add_argument("--out_json", required=True)
    parser.add_argument("--model", default=MODEL_NAME)
    parser.add_argument("--tags", default="", help="Например: ГГ.1,К5,К6")
    parser.add_argument("--max_candidates_per_tag", type=int, default=5)
    args = parser.parse_args()

    rows = _load_json(args.input_json)
    if not isinstance(rows, list):
        raise ValueError("input_json must contain a list")

    only_tags = None
    if args.tags.strip():
        only_tags = {x.strip() for x in args.tags.split(",") if x.strip()}

    result = enrich_prices(
        rows,
        model=args.model,
        only_tags=only_tags,
        max_candidates_per_tag=args.max_candidates_per_tag,
    )
    _save_json(args.out_json, result)
    print(f"saved: {args.out_json}")


if __name__ == "__main__":
    main()