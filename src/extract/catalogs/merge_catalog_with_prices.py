from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


PRICE_INPUT_DEFAULT = Path("data/output/runs/25-05/price_catalog_normalized.json")
OUTPUT_DEFAULT = Path("data/output/runs/25-05/catalog_with_prices.json")


def _load_json(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str | Path, data: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _clean_text(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def _norm_vendor(v: Any) -> str:
    return _clean_text(v).upper()


def _norm_article(v: Any) -> str:
    return _clean_text(v)


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except Exception:
        s = str(v).replace(",", ".").strip()
        try:
            return float(s)
        except Exception:
            return None

def _candidate_current_range(candidate: dict[str, Any]) -> tuple[float | None, float | None]:
    """
    Поддерживает оба формата:
    - current_range_min_a/current_range_max_a
    - current_range_a: {"min": ..., "max": ...}
    """
    p1 = _to_float(candidate.get("current_range_min_a"))
    p2 = _to_float(candidate.get("current_range_max_a"))

    if p1 is not None or p2 is not None:
        return p1, p2

    rng = candidate.get("current_range_a")
    if isinstance(rng, dict):
        return _to_float(rng.get("min")), _to_float(rng.get("max"))

    return None, None

def _float_eq(a: Any, b: Any, eps: float = 1e-6) -> bool:
    fa = _to_float(a)
    fb = _to_float(b)
    if fa is None or fb is None:
        return False
    return abs(fa - fb) <= eps


def _price_payload(price_row: dict[str, Any], match_type: str) -> dict[str, Any]:
    return {
        "price_found": True,
        "price_match_type": match_type,
        "price_vendor": price_row.get("vendor"),
        "price_article": price_row.get("article"),
        "price_title": price_row.get("title"),
        "price_rub": price_row.get("price_rub"),
        "price_currency": price_row.get("currency"),
        "price_source_domain": price_row.get("source_domain"),
        "price_source_type": price_row.get("source_type"),
        "price_product_url": price_row.get("product_url"),
        "price_match_key": price_row.get("match_key"),
        "price_designation": price_row.get("designation"),
    }
def _norm_model(v: Any) -> str:
    return _clean_text(v).upper()

def _extract_short_model(v: Any) -> str:
    s = _norm_model(v)

    patterns = [
        r"\b(NXB-63H\s+[1-4]P(?:\+N)?\s+[BCD]\d+)\b",
        r"\b(NB8-125R\s+[1-4]P(?:\+N)?\s+[BCD]\d+)\b",
        r"\b(NB1-63H\s+[1-4]P(?:\+N)?\s+[BCD]\d+)\b",
        r"\b(NB2-80ZT\s+[1-4]P(?:\+N)?\s+\d+А?)\b",
        r"\b(NB2-40ZT\s+[1-4]P(?:\+N)?\s+\d+А?)\b",
        r"\b(NS2-[A-Z0-9]+\s+[\d.,-]+\s*А)\b",
        r"\b(NS8-[A-Z0-9]+\s+[\d.,-]+\s*А)\b",
        r"\b(NXM-\d+[A-Z]?\s*/?\s*[1-4]P\s+\d+А)\b",
        r"\b(ВА\d+(?:-\d+)?\s+[1-4]P(?:\+N)?\s+[BCD]?\d+А?)\b",
    ]

    for pat in patterns:
        m = re.search(pat, s, re.I)
        if m:
            return _clean_text(m.group(1)).upper()

    return s

def _extract_poles_from_model(model: Any) -> int | None:
    s = _clean_text(model).upper()
    m = re.search(r"\b([1-4])P(?:\+N)?\b", s)
    if m:
        return int(m.group(1))
    return None

def _extract_candidate_poles_text(candidate: dict[str, Any], row_context: dict[str, Any] | None = None) -> str | None:
    poles = candidate.get("poles")
    if poles in (None, "") and row_context:
        req = row_context.get("requirement_ref") or {}
        poles = req.get("poles")

    model = _clean_text(candidate.get("model")).upper()
    if "+N" in model:
        if poles in (1, 2, 3, 4):
            return f"{int(poles)}P+N"

    if poles in (1, 2, 3, 4):
        return f"{int(poles)}P"

    m = re.search(r"\b([1-4]P(?:\+N)?)\b", model)
    if m:
        return m.group(1).upper()

    return None

def _build_candidate_canonical_model_key(
    candidate: dict[str, Any],
    row_context: dict[str, Any] | None = None,
) -> str:
    series = _clean_text(candidate.get("series")).upper()
    poles_text = _extract_candidate_poles_text(candidate, row_context=row_context) or ""
    rated_current_a = _to_float(candidate.get("rated_current_a"))
    trip_curve = _clean_text(candidate.get("trip_curve")).upper()
    breaking_capacity_ka = _to_float(candidate.get("breaking_capacity_ka"))
    rcd_ma = candidate.get("rcd_ma")

    current_s = "" if rated_current_a is None else str(int(rated_current_a) if float(rated_current_a).is_integer() else rated_current_a)
    breaking_s = "" if breaking_capacity_ka is None else str(int(breaking_capacity_ka) if float(breaking_capacity_ka).is_integer() else breaking_capacity_ka)
    rcd_s = "" if rcd_ma in (None, "") else str(int(rcd_ma))

    return f"{series}|{poles_text}|{current_s}|{trip_curve}|{breaking_s}|{rcd_s}"

def _dedupe_price_items(price_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []

    for row in price_items:
        key = (
            _norm_vendor(row.get("vendor")),
            _clean_text(row.get("article")),
            _clean_text(row.get("product_url")),
            _clean_text(row.get("match_key")),
            _to_float(row.get("price_rub")),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)

    return out

def _build_price_indexes(
    price_items: list[dict[str, Any]]
) -> tuple[
    dict[tuple[str, str], dict[str, Any]],
    dict[tuple[str, str], list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
    dict[tuple[str, str], list[dict[str, Any]]],
]:
    by_article: dict[tuple[str, str], dict[str, Any]] = {}
    by_vendor_model: dict[tuple[str, str], list[dict[str, Any]]] = {}
    by_match_key: dict[str, list[dict[str, Any]]] = {}
    by_vendor_canonical: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for row in price_items:
        vendor = _norm_vendor(row.get("vendor"))
        article = _norm_article(row.get("article"))
        model = _extract_short_model(row.get("title"))
        match_key = _clean_text(row.get("match_key"))
        canonical_model_key = _clean_text(row.get("canonical_model_key"))

        if vendor and article:
            by_article[(vendor, article)] = row

        if vendor and model:
            by_vendor_model.setdefault((vendor, model), []).append(row)

        if match_key:
            by_match_key.setdefault(match_key, []).append(row)

        if vendor and canonical_model_key:
            by_vendor_canonical.setdefault((vendor, canonical_model_key), []).append(row)

    return by_article, by_vendor_model, by_match_key, by_vendor_canonical


def _series_from_candidate(c: dict[str, Any]) -> str | None:
    return _clean_text(c.get("series")).upper() or None

def _series_family(series: Any) -> str:
    s = _clean_text(series).upper()
    if not s:
        return ""

    # DEKRAFT ВА-431/432 в прайсе считаем семейством ВА-430
    if s in {"ВА-431", "ВА-432"}:
        return "ВА-430"

    family_prefixes = [
        "NS2",
        "NS8",
        "NXM",
        "NXMS",
        "NM8N",
        "ВА-430",
        "ВА-431",
        "ВА-432",
        "ВА51-35",
        "ВА57-35",
        "ВА04-36",
    ]

    for pref in family_prefixes:
        if s == pref or s.startswith(pref + "-") or s.startswith(pref + " "):
            if pref in {"ВА-431", "ВА-432"}:
                return "ВА-430"
            return pref

    return s


def _price_family_match_score(
    candidate: dict[str, Any],
    price_row: dict[str, Any],
    row_context: dict[str, Any],
) -> float | None:
    """
    Возвращает score, если price_row допустимо соответствует candidate.
    Чем меньше score, тем лучше.
    None = не подходит.
    """
    cand_vendor = _norm_vendor(candidate.get("vendor"))
    price_vendor = _norm_vendor(price_row.get("vendor"))
    if cand_vendor != price_vendor:
        return None

    cand_class = _clean_text(candidate.get("device_class")).upper()
    price_class = _clean_text(price_row.get("device_class")).upper()
    if cand_class and price_class and cand_class != price_class:
        return None

    cand_family = _series_family(candidate.get("series"))
    price_family = _series_family(price_row.get("series"))
    if not cand_family or not price_family or cand_family != price_family:
        return None

    cand_poles_text = _extract_candidate_poles_text(candidate, row_context=row_context) or ""
    price_poles_text = _clean_text(price_row.get("poles_text")).upper()
    if cand_poles_text and price_poles_text and cand_poles_text != price_poles_text:
        return None

    score = 0.0

    if cand_class == "MPCB":
        c_min, c_max = _candidate_current_range(candidate)
        p_min = _to_float(price_row.get("current_range_min_a"))
        p_max = _to_float(price_row.get("current_range_max_a"))

        if c_min is None or c_max is None or p_min is None or p_max is None:
            return None

        if not _float_eq(c_min, p_min) or not _float_eq(c_max, p_max):
            return None

        # Для MPCB Icu может быть >= требуемого.
        c_ka = _to_float(candidate.get("breaking_capacity_ka"))
        p_ka = _to_float(price_row.get("breaking_capacity_ka"))
        if c_ka is not None and p_ka is not None:
            if p_ka < c_ka:
                return None
            score += p_ka - c_ka

        return score

    # MCB/MCCB/RCBO: ток должен совпадать.
    c_i = _to_float(candidate.get("rated_current_a"))
    p_i = _to_float(price_row.get("rated_current_a"))
    if c_i is not None and p_i is not None and not _float_eq(c_i, p_i):
        return None

    c_curve = _clean_text(candidate.get("trip_curve")).upper()
    p_curve = _clean_text(price_row.get("trip_curve")).upper()
    if c_curve and p_curve and c_curve != p_curve:
        return None

    if cand_class == "RCBO":
        c_rcd = candidate.get("rcd_ma")
        p_rcd = price_row.get("rcd_ma")
        if str(c_rcd) != str(p_rcd):
            return None

    c_ka = _to_float(candidate.get("breaking_capacity_ka"))
    p_ka = _to_float(price_row.get("breaking_capacity_ka"))

    if c_ka is not None and p_ka is not None:
        if p_ka < c_ka:
            return None
        score += p_ka - c_ka

    return score


def _match_candidate_by_family(
    candidate: dict[str, Any],
    row_context: dict[str, Any],
    price_items: list[dict[str, Any]],
) -> dict[str, Any] | None:
    matches: list[tuple[float, dict[str, Any]]] = []

    for price_row in price_items:
        score = _price_family_match_score(candidate, price_row, row_context)
        if score is None:
            continue
        matches.append((score, price_row))

    if not matches:
        return None

    # сначала минимальный запас по отключающей способности,
    # потом более дешёвый вариант
    matches.sort(
        key=lambda x: (
            x[0],
            _to_float(x[1].get("price_rub")) if _to_float(x[1].get("price_rub")) is not None else 1e18,
        )
    )

    return _price_payload(matches[0][1], "family_params")

def _build_catalog_candidate_match_key(
    candidate: dict[str, Any],
    row_context: dict[str, Any] | None = None,
) -> str:
    vendor = _norm_vendor(candidate.get("vendor"))
    series = _clean_text(candidate.get("series")).upper()
    device_class = _clean_text(candidate.get("device_class")).upper()

    poles_text = _extract_candidate_poles_text(candidate, row_context=row_context) or ""

    # if device_class == "MPCB":
    #     p1 = _to_float(candidate.get("current_range_min_a"))
    #     p2 = _to_float(candidate.get("current_range_max_a"))
    #     return f"{vendor}|{series}|{device_class}|{poles_text}|{p1}-{p2}"

    if device_class == "MPCB":
        p1, p2 = _candidate_current_range(candidate)
        return f"{vendor}|{series}|{device_class}|{poles_text}|{p1}-{p2}"

    rated_current_a = _to_float(candidate.get("rated_current_a"))
    trip_curve = _clean_text(candidate.get("trip_curve")).upper()
    breaking_capacity_ka = _to_float(candidate.get("breaking_capacity_ka"))

    if device_class == "RCBO":
        rcd_ma = candidate.get("rcd_ma")
        return f"{vendor}|{series}|{device_class}|{poles_text}|{rated_current_a}|{trip_curve}|{breaking_capacity_ka}|{rcd_ma}"

    return f"{vendor}|{series}|{device_class}|{poles_text}|{rated_current_a}|{trip_curve}|{breaking_capacity_ka}"

def _match_candidate(
    candidate: dict[str, Any],
    row_context: dict[str, Any],
    by_article: dict[tuple[str, str], dict[str, Any]],
    by_vendor_model: dict[tuple[str, str], list[dict[str, Any]]],
    by_match_key: dict[str, list[dict[str, Any]]],
    by_vendor_canonical: dict[tuple[str, str], list[dict[str, Any]]],
    price_items: list[dict[str, Any]],
) -> dict[str, Any]:
    vendor = _norm_vendor(candidate.get("vendor"))
    article = _norm_article(candidate.get("article"))
    
    if vendor == "CHINT" and _clean_text(candidate.get("series")).upper() == "NXB-63H":
        dbg_candidate_canonical = _build_candidate_canonical_model_key(candidate, row_context=row_context)
        dbg_candidate_match_key = _build_catalog_candidate_match_key(candidate, row_context=row_context)
        dbg_poles_text = _extract_candidate_poles_text(candidate, row_context=row_context)

        print(
            "[DEBUG][CAND NXB]",
            {
                "model": candidate.get("model"),
                "series": candidate.get("series"),
                "vendor": vendor,
                "rated_current_a": candidate.get("rated_current_a"),
                "trip_curve": candidate.get("trip_curve"),
                "breaking_capacity_ka": candidate.get("breaking_capacity_ka"),
                "candidate_poles_text": dbg_poles_text,
                "candidate_canonical": dbg_candidate_canonical,
                "candidate_match_key": dbg_candidate_match_key,
                "canonical_exists": (vendor, dbg_candidate_canonical) in by_vendor_canonical,
                "match_key_exists": dbg_candidate_match_key in by_match_key,
            }
        )

    # 1. article
    if vendor and article:
        row = by_article.get((vendor, article))
        if row:
            return _price_payload(row, "article")

    # 2. canonical_model_key
    candidate_canonical = _build_candidate_canonical_model_key(candidate, row_context=row_context)
    if vendor and candidate_canonical:
        rows = by_vendor_canonical.get((vendor, candidate_canonical), [])
        if rows:
            return _price_payload(rows[0], "canonical_model_key")

    # 3. exact short model
    cand_model = _extract_short_model(candidate.get("model"))
    if vendor and cand_model:
        rows = by_vendor_model.get((vendor, cand_model), [])
        if rows:
            return _price_payload(rows[0], "model_exact")

    # 4. match_key
    match_key = _build_catalog_candidate_match_key(candidate, row_context=row_context)
    rows = by_match_key.get(match_key, [])
    if rows:
        return _price_payload(rows[0], "match_key")

    # 5. soft fallback
    device_class = _clean_text(candidate.get("device_class")).upper()
    if device_class in {"MCB", "MCCB", "RCBO"}:
        series = _clean_text(candidate.get("series")).upper()
        poles_text = _extract_candidate_poles_text(candidate, row_context=row_context) or ""
        rated_current_a = _to_float(candidate.get("rated_current_a"))
        trip_curve = _clean_text(candidate.get("trip_curve")).upper()
        breaking_capacity_ka = _to_float(candidate.get("breaking_capacity_ka"))
        rcd_ma = candidate.get("rcd_ma")

        candidates_soft: list[dict[str, Any]] = []
        for rows2 in by_match_key.values():
            for r in rows2:
                if _norm_vendor(r.get("vendor")) != vendor:
                    continue
                if _clean_text(r.get("series")).upper() != series:
                    continue
                if _clean_text(r.get("poles_text")).upper() != poles_text:
                    continue
                if rated_current_a is not None and not _float_eq(r.get("rated_current_a"), rated_current_a):
                    continue
                if trip_curve and _clean_text(r.get("trip_curve")).upper() != trip_curve:
                    continue
                if breaking_capacity_ka is not None and not _float_eq(r.get("breaking_capacity_ka"), breaking_capacity_ka):
                    continue
                if device_class == "RCBO":
                    row_rcd_ma = r.get("rcd_ma")
                    if str(row_rcd_ma) != str(rcd_ma):
                        continue
                candidates_soft.append(r)

        if candidates_soft:
            return _price_payload(candidates_soft[0], "soft_params")
        
    if vendor == "CHINT" and _clean_text(candidate.get("series")).upper() == "NXB-63H":
        print(
            "[DEBUG][CAND NXB][NO MATCH]",
            {
                "model": candidate.get("model"),
                "series": candidate.get("series"),
            }
        )
    
    # 6. family fallback для серий, где кандидат хранит семейство,
    # а прайс содержит конкретное исполнение.
    family_match = _match_candidate_by_family(
        candidate=candidate,
        row_context=row_context,
        price_items=price_items,
    )
    if family_match:
        return family_match

    return {"price_found": False, "price_match_type": "none"}

def _enrich_candidate_row(
    row: dict[str, Any],
    by_article: dict[tuple[str, str], dict[str, Any]],
    by_vendor_model: dict[tuple[str, str], list[dict[str, Any]]],
    by_match_key: dict[str, list[dict[str, Any]]],
    by_vendor_canonical: dict[tuple[str, str], list[dict[str, Any]]],
    price_items: list[dict[str, Any]],
) -> dict[str, Any]:
    out = dict(row)

    candidate = row.get("candidate")
    if isinstance(candidate, dict):
        price_info = _match_candidate(
            candidate,
            row_context=row,
            by_article=by_article,
            by_vendor_model=by_vendor_model,
            by_match_key=by_match_key,
            by_vendor_canonical=by_vendor_canonical,
            price_items=price_items,
        )
        merged_candidate = dict(candidate)
        merged_candidate.update(price_info)
        out["candidate"] = merged_candidate

    candidate_options = row.get("candidate_options") or []
    if not isinstance(candidate_options, list):
        candidate_options = []

    new_options = []
    for cand in candidate_options:
        if not isinstance(cand, dict):
            new_options.append(cand)
            continue

        price_info = _match_candidate(
            cand,
            row_context=row,
            by_article=by_article,
            by_vendor_model=by_vendor_model,
            by_match_key=by_match_key,
            by_vendor_canonical=by_vendor_canonical,
            price_items=price_items,
        )
        merged = dict(cand)
        merged.update(price_info)
        new_options.append(merged)

    out["candidate_options"] = new_options
    return out


def _enrich_candidate_options(
    row: dict[str, Any],
    by_article: dict[tuple[str, str], dict[str, Any]],
    by_vendor_model: dict[tuple[str, str], list[dict[str, Any]]],
    by_match_key: dict[str, list[dict[str, Any]]],
    by_vendor_canonical: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    candidate_options = row.get("candidate_options") or []
    if not isinstance(candidate_options, list):
        candidate_options = []

    new_options = []
    for cand in candidate_options:
        if not isinstance(cand, dict):
            new_options.append(cand)
            continue

        price_info = _match_candidate(
            cand,
            row_context=row,
            by_article=by_article,
            by_vendor_model=by_vendor_model,
            by_match_key=by_match_key,
            by_vendor_canonical=by_vendor_canonical,
        )
        merged = dict(cand)
        merged.update(price_info)
        new_options.append(merged)

    out = dict(row)
    out["candidate_options"] = new_options
    return out

def merge_catalog_with_prices(
    catalog_rows: list[dict[str, Any]],
    price_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    price_items = _dedupe_price_items(price_items)
    
    debug_nxb = [
        x for x in price_items
        if str(x.get("vendor", "")).upper() == "CHINT"
        and str(x.get("series", "")).upper() == "NXB-63H"
    ]

    print(f"[DEBUG] NXB-63H rows after dedupe: {len(debug_nxb)}")
    for x in debug_nxb[:20]:
        print(
            "[DEBUG][NXB]",
            {
                "article": x.get("article"),
                "title": x.get("title"),
                "series": x.get("series"),
                "poles_text": x.get("poles_text"),
                "rated_current_a": x.get("rated_current_a"),
                "trip_curve": x.get("trip_curve"),
                "breaking_capacity_ka": x.get("breaking_capacity_ka"),
                "match_key": x.get("match_key"),
                "canonical_model_key": x.get("canonical_model_key"),
            }
        )
    
    by_article, by_vendor_model, by_match_key, by_vendor_canonical = _build_price_indexes(price_items)
    
    print(
        "[DEBUG] has canonical NXB-63H 3P 40 D 10:",
        ("CHINT", "NXB-63H|3P|40|D|10") in by_vendor_canonical
    )
    print(
        "[DEBUG] has match_key NXB-63H 3P 40 D 10:",
        "CHINT|NXB-63H|MCB|3P|40.0|D|10.0" in by_match_key
    )

    out = []
    for row in catalog_rows:
        if not isinstance(row, dict):
            out.append(row)
            continue
        out.append(
            _enrich_candidate_row(
                row,
                by_article,
                by_vendor_model,
                by_match_key,
                by_vendor_canonical,
                price_items,
            )
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog_json", required=True)
    parser.add_argument("--price_json", default=str(PRICE_INPUT_DEFAULT))
    parser.add_argument("--out", default=str(OUTPUT_DEFAULT))
    args = parser.parse_args()

    catalog_rows = _load_json(args.catalog_json)
    if not isinstance(catalog_rows, list):
        raise ValueError("catalog_json must contain a top-level list")

    price_payload = _load_json(args.price_json)
    price_items = price_payload.get("items") or []
    if not isinstance(price_items, list):
        raise ValueError("price_json must contain payload['items'] as a list")

    merged = merge_catalog_with_prices(catalog_rows, price_items)
    _save_json(args.out, merged)
    print(f"saved: {args.out}")


if __name__ == "__main__":
    main()