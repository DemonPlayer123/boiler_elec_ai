import json
from pathlib import Path

RUN_DIR = Path("data/output/runs/25-05")
OUT_PATH = RUN_DIR / "price_coverage_metrics.json"


def load_json(path):
    return json.load(open(path, encoding="utf-8"))


def get_result(payload):
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


def has_price(row):
    if row.get("price_found") is True:
        return True

    value = row.get("price_rub") or row.get("price_value")
    if value in (None, "", 0, "0", "-", "—"):
        return False

    return True


def is_official(row):
    source_type = str(row.get("price_source_type") or "").lower()
    source_domain = str(row.get("price_source_domain") or "").lower()
    source_name = str(row.get("price_source_name") or "").lower()

    return (
        "official" in source_type
        or "официаль" in source_type
        or source_domain in {"chint.ru", "keaz.ru", "dek.ru"}
        or "chint" in source_name
        or "keaz" in source_name
        or "dekraft" in source_name
        or "dek.ru" in source_name
    )


def is_seller_fallback(row):
    source_type = str(row.get("price_source_type") or "").lower()
    source_name = str(row.get("price_source_name") or "").lower()
    source_domain = str(row.get("price_source_domain") or "").lower()

    # Если источник официальный, это не seller fallback,
    # даже если match_type равен openai_fallback.
    if (
        "official" in source_type
        or "официаль" in source_type
        or source_domain in {"chint.ru", "keaz.ru", "dek.ru"}
        or "chint" in source_name
        or "keaz" in source_name
        or "dekraft" in source_name
        or "dek.ru" in source_name
    ):
        return False

    return (
        "seller" in source_type
        or "продав" in source_type
        or "vseinstrumenti" in source_name
        or "etm" in source_name
    )

def pct(a, b):
    return round(a * 100 / b, 1) if b else 0.0


rows_by_tag = {}

# 1. Берём сохранённые API-ответы: api_K6.json, api_ГГ.1.json и т. д.
for path in RUN_DIR.glob("api_*.json"):
    payload = load_json(path)
    result = get_result(payload)

    tag = result.get("tag") or path.stem.replace("api_", "")
    options = result.get("candidate_options") or []

    if options:
        rows_by_tag[tag] = options[:5]

# 2. Дополнительно пробуем взять агрегированный файл, если в нём есть candidate_options.
agg_path = RUN_DIR / "vkr_metrics_by_tag.json"
if agg_path.exists():
    agg = load_json(agg_path)

    if isinstance(agg, list):
        iterable = agg
    elif isinstance(agg, dict):
        iterable = agg.values()
    else:
        iterable = []

    for item in iterable:
        if not isinstance(item, dict):
            continue

        result = get_result(item)
        tag = result.get("tag")
        options = result.get("candidate_options") or []

        if tag and options:
            rows_by_tag[tag] = options[:5]

all_top5 = []
top1 = []

for tag, options in rows_by_tag.items():
    for row in options:
        row["_tag"] = tag
    all_top5.extend(options)
    if options:
        top1.append(options[0])

priced_top5 = [r for r in all_top5 if has_price(r)]
unpriced_top5 = [r for r in all_top5 if not has_price(r)]
priced_top1 = [r for r in top1 if has_price(r)]
official = [r for r in priced_top5 if is_official(r)]
seller = [r for r in priced_top5 if is_seller_fallback(r)]

match_type_counts = {}
source_type_counts = {}

for row in priced_top5:
    mt = row.get("price_match_type") or "unknown"
    st = row.get("price_source_type") or row.get("price_source_domain") or "unknown"
    match_type_counts[mt] = match_type_counts.get(mt, 0) + 1
    source_type_counts[st] = source_type_counts.get(st, 0) + 1

metrics = {
    "source": "saved api_*.json / vkr_metrics_by_tag.json",
    "tags_total": len(rows_by_tag),
    "total_candidates_top5": len(all_top5),
    "priced_candidates_top5": len(priced_top5),
    "unpriced_candidates_top5": len(unpriced_top5),
    "price_coverage_candidates_top5_pct": pct(len(priced_top5), len(all_top5)),
    "total_top1_candidates": len(top1),
    "priced_top1_candidates": len(priced_top1),
    "price_coverage_top1_pct": pct(len(priced_top1), len(top1)),
    "official_price_candidates": len(official),
    "official_price_share_of_priced_pct": pct(len(official), len(priced_top5)),
    "seller_fallback_price_candidates": len(seller),
    "seller_fallback_share_of_priced_pct": pct(len(seller), len(priced_top5)),
    "unpriced_candidates_share_pct": pct(len(unpriced_top5), len(all_top5)),
    "match_type_counts": match_type_counts,
    "source_type_counts": source_type_counts,
    "tags": sorted(rows_by_tag.keys()),
}

OUT_PATH.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

print("Метрики для таблицы 3.8")
print("-" * 60)
print(f"Источник данных: {metrics['source']}")
print(f"Количество тегов: {metrics['tags_total']}")
print(f"Доля кандидатов с найденной ценой: {len(priced_top5)} из {len(all_top5)} ({metrics['price_coverage_candidates_top5_pct']} %)")
print(f"Доля top-1 кандидатов с найденной ценой: {len(priced_top1)} из {len(top1)} ({metrics['price_coverage_top1_pct']} %)")
print(f"Доля цен из официальных источников: {len(official)} из {len(priced_top5)} ({metrics['official_price_share_of_priced_pct']} %)")
print(f"Доля цен из seller fallback: {len(seller)} из {len(priced_top5)} ({metrics['seller_fallback_share_of_priced_pct']} %)")
print(f"Доля кандидатов без цены: {len(unpriced_top5)} из {len(all_top5)} ({metrics['unpriced_candidates_share_pct']} %)")
print("-" * 60)

print("\nТипы сопоставления:")
for k, v in sorted(match_type_counts.items()):
    print(f"{k}: {v}")

print("\nТипы источников:")
for k, v in sorted(source_type_counts.items()):
    print(f"{k}: {v}")

print(f"\nФайл сохранён: {OUT_PATH}")