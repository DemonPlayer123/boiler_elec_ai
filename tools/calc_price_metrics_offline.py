import json
from pathlib import Path

RUN_DIR = Path("data/output/runs/25-05")

CANDIDATE_FILES = [
    RUN_DIR / "shortlist.json",
    RUN_DIR / "candidates.json",
    RUN_DIR / "candidate_options.json",
]

out_path = RUN_DIR / "price_coverage_metrics.json"


def load_json(path: Path):
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def flatten_rows(obj):
    if obj is None:
        return []

    if isinstance(obj, list):
        rows = []
        for item in obj:
            if isinstance(item, dict):
                rows.append(item)
            elif isinstance(item, list):
                rows.extend(flatten_rows(item))
        return rows

    if isinstance(obj, dict):
        # частый вариант: {"К6": [..], "К5": [..]}
        rows = []
        for key, value in obj.items():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        row = dict(item)
                        row.setdefault("tag", key)
                        rows.append(row)
            elif isinstance(value, dict):
                row = dict(value)
                row.setdefault("tag", key)
                rows.append(row)
        return rows

    return []


def first_existing_candidate_file():
    for path in CANDIDATE_FILES:
        if path.exists():
            return path
    return None


def has_price(row: dict) -> bool:
    price_found = row.get("price_found")
    price_rub = row.get("price_rub")
    price_value = row.get("price_value")

    if price_found is True:
        return True

    if price_rub not in (None, "", 0, "0"):
        return True

    if price_value not in (None, "", 0, "0"):
        return True

    return False


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


def get_rank(row: dict):
    value = row.get("rank")
    try:
        return int(value)
    except Exception:
        return None


def pct(part: int, total: int) -> float:
    return round(part * 100 / total, 1) if total else 0.0


candidate_file = first_existing_candidate_file()
if candidate_file is None:
    raise FileNotFoundError(
        "Не найден ни один файл кандидатов: "
        + ", ".join(str(p) for p in CANDIDATE_FILES)
    )

raw = load_json(candidate_file)
rows = flatten_rows(raw)

# Оставляем строки, похожие на кандидатов
candidate_rows = []
for row in rows:
    if not isinstance(row, dict):
        continue

    has_candidate_identity = any(
        row.get(k) is not None
        for k in [
            "vendor",
            "candidate_vendor",
            "series",
            "candidate_series",
            "model",
            "candidate_model",
        ]
    )

    has_price_identity = any(
        row.get(k) is not None
        for k in [
            "price_found",
            "price_rub",
            "price_value",
            "price_article",
            "price_match_type",
        ]
    )

    if has_candidate_identity or has_price_identity:
        candidate_rows.append(row)

# Если есть rank, считаем top-5 по каждому тегу
by_tag = {}
for row in candidate_rows:
    tag = row.get("tag") or row.get("_tag") or row.get("ep_tag")
    if not tag:
        continue
    by_tag.setdefault(tag, []).append(row)

top5_rows = []
top1_rows = []

if by_tag:
    for tag, tag_rows in by_tag.items():
        ranked = sorted(
            tag_rows,
            key=lambda r: get_rank(r) if get_rank(r) is not None else 10**9,
        )

        top5 = ranked[:5]
        top5_rows.extend(top5)

        if top5:
            top1_rows.append(top5[0])
else:
    # fallback, если тегов нет: считаем весь файл как общий набор
    top5_rows = candidate_rows
    top1_rows = [r for r in candidate_rows if get_rank(r) == 1]

priced_top5 = [r for r in top5_rows if has_price(r)]
unpriced_top5 = [r for r in top5_rows if not has_price(r)]
priced_top1 = [r for r in top1_rows if has_price(r)]

official_prices = [r for r in priced_top5 if is_official(r)]
seller_fallback_prices = [r for r in priced_top5 if is_seller_fallback(r)]

match_type_counts = {}
source_type_counts = {}

for row in priced_top5:
    mt = row.get("price_match_type") or "unknown"
    st = row.get("price_source_type") or "unknown"
    match_type_counts[mt] = match_type_counts.get(mt, 0) + 1
    source_type_counts[st] = source_type_counts.get(st, 0) + 1

metrics = {
    "source_file": str(candidate_file),
    "tags_total": len(by_tag),
    "total_candidates_top5": len(top5_rows),
    "priced_candidates_top5": len(priced_top5),
    "unpriced_candidates_top5": len(unpriced_top5),
    "price_coverage_candidates_top5_pct": pct(len(priced_top5), len(top5_rows)),
    "total_top1_candidates": len(top1_rows),
    "priced_top1_candidates": len(priced_top1),
    "price_coverage_top1_pct": pct(len(priced_top1), len(top1_rows)),
    "official_price_candidates": len(official_prices),
    "official_price_share_of_priced_pct": pct(len(official_prices), len(priced_top5)),
    "seller_fallback_price_candidates": len(seller_fallback_prices),
    "seller_fallback_share_of_priced_pct": pct(len(seller_fallback_prices), len(priced_top5)),
    "unpriced_candidates_share_pct": pct(len(unpriced_top5), len(top5_rows)),
    "match_type_counts": match_type_counts,
    "source_type_counts": source_type_counts,
}

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(metrics, f, ensure_ascii=False, indent=2)

print("\nМетрики для таблицы 3.8")
print("-" * 60)
print(f"Источник данных: {candidate_file}")
print(f"Количество тегов: {len(by_tag)}")
print(f"Доля кандидатов с найденной ценой: {len(priced_top5)} из {len(top5_rows)} ({pct(len(priced_top5), len(top5_rows))} %)")
print(f"Доля top-1 кандидатов с найденной ценой: {len(priced_top1)} из {len(top1_rows)} ({pct(len(priced_top1), len(top1_rows))} %)")
print(f"Доля цен из официальных источников: {len(official_prices)} из {len(priced_top5)} ({pct(len(official_prices), len(priced_top5))} %)")
print(f"Доля цен из seller fallback: {len(seller_fallback_prices)} из {len(priced_top5)} ({pct(len(seller_fallback_prices), len(priced_top5))} %)")
print(f"Доля кандидатов без цены: {len(unpriced_top5)} из {len(top5_rows)} ({pct(len(unpriced_top5), len(top5_rows))} %)")
print("-" * 60)

print("\nТипы сопоставления:")
for k, v in sorted(match_type_counts.items()):
    print(f"  {k}: {v}")

print("\nТипы источников:")
for k, v in sorted(source_type_counts.items()):
    print(f"  {k}: {v}")

print(f"\nФайл сохранён: {out_path}")