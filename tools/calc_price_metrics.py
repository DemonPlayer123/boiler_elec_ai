import json
import urllib.parse
import urllib.request
from pathlib import Path

RUN_DIR = Path("data/output/runs/25-05")
BASE_URL = "http://127.0.0.1:8000/api/tag"

items_path = RUN_DIR / "items_final.json"
out_path = RUN_DIR / "price_coverage_metrics.json"

items = json.load(open(items_path, encoding="utf-8"))

tags = []
for item in items:
    tag = item.get("tag")
    if tag and tag not in tags:
        tags.append(tag)

all_candidates = []
top1_candidates = []
failed_tags = []

for tag in tags:
    url = f"{BASE_URL}/{urllib.parse.quote(tag)}"
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = json.load(response)
    except Exception as exc:
        failed_tags.append({"tag": tag, "error": str(exc)})
        continue

    result = payload.get("result", {})
    options = result.get("candidate_options") or []

    for option in options:
        row = dict(option)
        row["_tag"] = tag
        all_candidates.append(row)

    if options:
        row = dict(options[0])
        row["_tag"] = tag
        top1_candidates.append(row)
    elif result.get("candidate"):
        row = dict(result["candidate"])
        row["_tag"] = tag
        top1_candidates.append(row)


def has_price(row: dict) -> bool:
    return bool(row.get("price_found")) and row.get("price_rub") not in (None, "", 0)


def is_official(row: dict) -> bool:
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


def is_seller_fallback(row: dict) -> bool:
    source_type = str(row.get("price_source_type") or "").lower()
    source_name = str(row.get("price_source_name") or "").lower()
    match_type = str(row.get("price_match_type") or "").lower()

    return (
        "seller" in source_type
        or "продав" in source_type
        or "vseinstrumenti" in source_name
        or "etm" in source_name
        or match_type == "openai_fallback"
    )


def pct(part: int, total: int) -> float:
    return round(part * 100 / total, 1) if total else 0.0


total_candidates = len(all_candidates)
priced_candidates = [c for c in all_candidates if has_price(c)]
unpriced_candidates = [c for c in all_candidates if not has_price(c)]

total_top1 = len(top1_candidates)
priced_top1 = [c for c in top1_candidates if has_price(c)]

official_prices = [c for c in priced_candidates if is_official(c)]
seller_fallback_prices = [c for c in priced_candidates if is_seller_fallback(c)]

match_type_counts = {}
source_type_counts = {}

for c in priced_candidates:
    mt = c.get("price_match_type") or "unknown"
    st = c.get("price_source_type") or "unknown"
    match_type_counts[mt] = match_type_counts.get(mt, 0) + 1
    source_type_counts[st] = source_type_counts.get(st, 0) + 1

metrics = {
    "tags_total": len(tags),
    "tags_processed": total_top1,
    "failed_tags": failed_tags,

    "total_candidates_top5": total_candidates,
    "priced_candidates_top5": len(priced_candidates),
    "unpriced_candidates_top5": len(unpriced_candidates),
    "price_coverage_candidates_top5_pct": pct(len(priced_candidates), total_candidates),

    "total_top1_candidates": total_top1,
    "priced_top1_candidates": len(priced_top1),
    "price_coverage_top1_pct": pct(len(priced_top1), total_top1),

    "official_price_candidates": len(official_prices),
    "official_price_share_of_priced_pct": pct(len(official_prices), len(priced_candidates)),

    "seller_fallback_price_candidates": len(seller_fallback_prices),
    "seller_fallback_share_of_priced_pct": pct(len(seller_fallback_prices), len(priced_candidates)),

    "unpriced_candidates_share_pct": pct(len(unpriced_candidates), total_candidates),

    "match_type_counts": match_type_counts,
    "source_type_counts": source_type_counts,
}

json.dump(metrics, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("\nМетрики для таблицы 3.8")
print("-" * 60)
print(f"Доля кандидатов с найденной ценой: {len(priced_candidates)} из {total_candidates} ({pct(len(priced_candidates), total_candidates)} %)")
print(f"Доля top-1 кандидатов с найденной ценой: {len(priced_top1)} из {total_top1} ({pct(len(priced_top1), total_top1)} %)")
print(f"Доля цен из официальных источников: {len(official_prices)} из {len(priced_candidates)} ({pct(len(official_prices), len(priced_candidates))} %)")
print(f"Доля цен из seller fallback: {len(seller_fallback_prices)} из {len(priced_candidates)} ({pct(len(seller_fallback_prices), len(priced_candidates))} %)")
print(f"Доля кандидатов без цены: {len(unpriced_candidates)} из {total_candidates} ({pct(len(unpriced_candidates), total_candidates)} %)")
print("-" * 60)
print(f"Файл сохранён: {out_path}")
print("\nТипы сопоставления:")
for k, v in sorted(match_type_counts.items()):
    print(f"  {k}: {v}")
print("\nТипы источников:")
for k, v in sorted(source_type_counts.items()):
    print(f"  {k}: {v}")

if failed_tags:
    print("\nТеги с ошибками:")
    for row in failed_tags:
        print(f"  {row['tag']}: {row['error']}")