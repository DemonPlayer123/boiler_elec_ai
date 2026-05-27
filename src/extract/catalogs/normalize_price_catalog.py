from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


INPUT_DEFAULT = Path("data/output/runs/25-05/price_catalog.json")
OUTPUT_DEFAULT = Path("data/output/runs/25-05/price_catalog_normalized.json")


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
    return re.sub(r"\s+", " ", text).strip()


def _to_float(text: str | None) -> float | None:
    if not text:
        return None
    s = str(text).replace(",", ".").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _detect_device_class(title: str) -> str | None:
    t = title.lower()

    # Motor protection circuit breaker / автомат защиты двигателя
    if (
        "защиты двигателя" in t
        or "для защиты эл. двигателя" in t
        or "защ. двиг" in t
        or "защ двиг" in t
        or "защита двигателя" in t
        or "зашиты двигателя" in t
        or "ва-430" in t
        or "ва-431" in t
        or "ва-432" in t
        or "ns2" in t
        or "ns8" in t
    ):
        return "MPCB"

    # CHINT / силовые серии
    if "nxm" in t or "nm8" in t or "nxms" in t:
        return "MCCB"
    
    if (
        "авдт" in t
        or "диф-103" in t
        or "дифференциальный автомат" in t
        or "differential" in t
        or "rcbo" in t
        or "nxble" in t
        or "nb2le" in t
        or "avdt" in t
    ):
        return "RCBO"
    
    # KEAZ molded-case / block circuit breakers
    if (
        "ва51-35" in t
        or "ва57-35" in t
        or "ва04-36" in t
    ):
        return "MCCB"

    # KEAZ / DEKRAFT / модульные автоматы
    if (
        "ва47-29" in t
        or "ва47-100" in t
        or "optidin bm63" in t
        or "optidin bm125" in t
        or "ва-103" in t
        or "ва-105" in t
        or "ва-201" in t
        or "nb" in t
        or "авт. выкл." in t
        or "автоматический выключатель" in t
    ):
        return "MCB"

    return None


def _extract_series(title: str) -> str | None:
    patterns = [
        # KEAZ
        r"\b(ВА47-100)\b",
        r"\b(ВА47-29)\b",
        r"\b(OPTIDIN\s+BM125)\b",
        r"\b(OPTIDIN\s+BM63)\b",
        r"\b(ВА51-35)\b",
        r"\b(ВА57-35)\b",
        r"\b(ВА04-36)\b",

        # DEKRAFT
        r"\b(ВА-430)\b",
        r"\b(ВА-43[0-2])\b",
        r"\b(ВА-201)\b",
        r"\b(ВА-105)\b",
        r"\b(ВА-103)\b",
        r"\b(ДИФ-103)\b",

        # CHINT
        r"\b(NXB-63H)\b",
        r"\b(NXB-\d+[A-Z]*)\b",
        r"\b(NB2-80ZT)\b",
        r"\b(NB2-40ZT)\b",
        r"\b(NB8-125R)\b",
        r"\b(NB1-63H)\b",
        r"\b(NS2-80BG)\b",
        r"\b(NS2-32G)\b",
        r"\b(NS2-25G)\b",
        r"\b(NS2-32H)\b",
        r"\b(NS2-25)\b",
        r"\b(NS8-80X)\b",
        r"\b(NS8-32X)\b",
        r"\b(NXM-63S)\b",
        r"\b(NXM-63H)\b",
        r"\b(NXM-125S)\b",
        r"\b(NXM-125H)\b",
        r"\b(NXMS-\d+[A-Z]?)\b",
        r"\b(NM8N-\d+[A-Z]?(?:\s*TM)?)\b",
        r"\b(NXM-\d+[A-Z]?)\b",
        r"\b(NS2-\d+[A-Z]*)\b",
        r"\b(NS8-\d+[A-Z]*)\b",
        r"\b(NB\d+-[A-Z0-9]+)\b",
        r"\b(NXBLE-63)\b",
        r"\b(NB2LE-80ZT)\b",
    ]

    for pat in patterns:
        m = re.search(pat, title, re.I)
        if m:
            series = m.group(1).upper()

            # DEKRAFT motor-protection family:
            # в прайсе могут быть ВА-431 / ВА-432, а в кандидатах используется ВА-430
            if series in {"ВА-431", "ВА-432"}:
                return "ВА-430"

            return series

    return None


def _extract_poles(title: str) -> int | None:
    # обычный формат: 1P / 2P / 3P / 4P / 3P+N
    m = re.search(r"\b([1-4])P(?:\+N)?\b", title, re.I)
    if m:
        return int(m.group(1))

    # компактный KEAZ: ВА47-100-3D40-УХЛ3
    m = re.search(r"(?:^|[-\s])([1-4])[BCD]\d+(?:[-\s]|$)", title, re.I)
    if m:
        return int(m.group(1))

    return None

def _extract_poles_text(title: str) -> str | None:
    m = re.search(r"\b([1-4]P(?:\+N)?)\b", title, re.I)
    if m:
        return m.group(1).upper()

    # компактный KEAZ: 3D40 -> 3P
    m = re.search(r"(?:^|[-\s])([1-4])[BCD]\d+(?:[-\s]|$)", title, re.I)
    if m:
        return f"{m.group(1)}P"

    return None

def _extract_has_neutral(title: str) -> bool:
    return "+N" in title.upper()


def _extract_rated_current_a(title: str) -> float | None:
    # диапазон не считаем за rated_current
    if re.search(r"\d+(?:[.,]\d+)?\s*-\s*\d+(?:[.,]\d+)?\s*[АA]\b", title, re.I):
        return None

    # обычный формат: 32А / 100А
    candidates = re.findall(r"(\d+(?:[.,]\d+)?)\s*[АA]\b", title, re.I)
    values = [_to_float(x) for x in candidates]
    values = [x for x in values if x is not None]
    if values:
        return values[0]

    # компактный KEAZ: 3D40 -> 40
    m = re.search(r"(?:^|[-\s])[1-4][BCD](\d+)(?:[-\s]|$)", title, re.I)
    if m:
        return _to_float(m.group(1))

    return None


def _extract_current_range(title: str) -> tuple[float | None, float | None]:
    s = str(title or "").replace(",", ".")

    # 9-14А / 9.0-14.0A / 6.3-10 A
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*[АA]\b",
        s,
        re.I,
    )
    if m:
        return _to_float(m.group(1)), _to_float(m.group(2))

    return None, None

def _extract_rcd_ma(title: str) -> int | None:
    m = re.search(r"(\d+)\s*мА\b", title, re.I)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


def _extract_trip_curve(title: str) -> str | None:
    patterns = [
        r"хар-ка\s*([BCD])\b",
        r"х-ка\s*([BCD])\b",
        r"характеристика\s*([BCD])\b",
        r"\b([BCD])\s*[- ]?curve\b",

        # DEKRAFT: 50А C 10кА
        r"\d+(?:[.,]\d+)?\s*А\s*([BCD])\s*\d+(?:[.,]\d+)?\s*кА\b",

        # KEAZ compact: 3D40
        r"(?:^|[-\s])[1-4]([BCD])\d+(?:[-\s]|$)",
    ]

    for pat in patterns:
        m = re.search(pat, title, re.I)
        if m:
            return m.group(1).upper()

    return None


def _extract_breaking_capacity_ka(title: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*кА\b", title, re.I)
    if m:
        return _to_float(m.group(1))
    return None

def _extract_designation(title: str) -> str | None:
    t = str(title or "").strip()
    if not t:
        return None

    # КЭАЗ / длинные обозначения изделий
    m = re.search(
        r"(ВА(?:51|57|04)-\d{2}[A-ZА-Я0-9\-\/\.]*КЭАЗ)",
        t,
        re.I,
    )
    if m:
        return m.group(1).strip()

    # CHINT / буквенно-цифровые обозначения серии и исполнения
    m = re.search(
        r"\b((?:NXB|NB8|NXM|NXMS|NM8N|NS2|NS8)[A-Z0-9\-\/\+ ]{3,})\b",
        t,
        re.I,
    )
    if m:
        return re.sub(r"\s{2,}", " ", m.group(1)).strip()

    # DEKraft / обозначение вида ВА-430 9-14A
    m = re.search(r"\b(ВА-\d{3}\s+[0-9.,\-]+A)\b", t, re.I)
    if m:
        return m.group(1).strip()

    return None

def _build_canonical_model_key(
    series: str | None,
    poles_text: str | None,
    rated_current_a: float | None,
    trip_curve: str | None,
    breaking_capacity_ka: float | None,
    rcd_ma: int | None = None,
) -> str:
    series_s = (series or "").upper()
    poles_s = (poles_text or "").upper()
    current_s = "" if rated_current_a is None else str(int(rated_current_a) if float(rated_current_a).is_integer() else rated_current_a)
    curve_s = (trip_curve or "").upper()
    breaking_s = "" if breaking_capacity_ka is None else str(int(breaking_capacity_ka) if float(breaking_capacity_ka).is_integer() else breaking_capacity_ka)
    rcd_s = "" if rcd_ma is None else str(int(rcd_ma))

    return f"{series_s}|{poles_s}|{current_s}|{curve_s}|{breaking_s}|{rcd_s}"


def _build_match_key(row: dict[str, Any]) -> str:
    device_class = row.get("device_class") or ""
    vendor = row.get("vendor") or ""
    series = row.get("series") or ""
    poles_text = row.get("poles_text") or ""

    if device_class == "MPCB":
        p1 = row.get("current_range_min_a")
        p2 = row.get("current_range_max_a")
        return f"{vendor}|{series}|{device_class}|{poles_text}|{p1}-{p2}"

    rated_current_a = row.get("rated_current_a")
    trip_curve = row.get("trip_curve") or ""
    breaking_capacity_ka = row.get("breaking_capacity_ka")

    if device_class == "RCBO":
        rcd_ma = row.get("rcd_ma")
        return (
            f"{vendor}|{series}|{device_class}|{poles_text}|"
            f"{rated_current_a}|{trip_curve}|{breaking_capacity_ka}|{rcd_ma}"
        )

    return (
        f"{vendor}|{series}|{device_class}|{poles_text}|"
        f"{rated_current_a}|{trip_curve}|{breaking_capacity_ka}"
    )


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    title = _clean_text(item.get("title"))
    designation = _extract_designation(title)
    vendor = _clean_text(item.get("vendor")).upper()
    article = _clean_text(item.get("article")) or None

    series = _extract_series(title)
    device_class = _detect_device_class(title)
    poles = _extract_poles(title)
    poles_text = _extract_poles_text(title)
    has_neutral = _extract_has_neutral(title)
    rated_current_a = _extract_rated_current_a(title)
    current_range_min_a, current_range_max_a = _extract_current_range(title)
    trip_curve = _extract_trip_curve(title)
    rcd_ma = _extract_rcd_ma(title)
    breaking_capacity_ka = _extract_breaking_capacity_ka(title)

    normalized = {
        "vendor": vendor,
        "article": article,
        "title": title,
        "series": series,
        "device_class": device_class,
        "poles": poles,
        "poles_text": poles_text,
        "has_neutral": has_neutral,
        "rated_current_a": rated_current_a,
        "current_range_min_a": current_range_min_a,
        "current_range_max_a": current_range_max_a,
        "trip_curve": trip_curve,
        "rcd_ma": rcd_ma,
        "breaking_capacity_ka": breaking_capacity_ka,
        "price_rub": item.get("price_rub"),
        "currency": item.get("currency"),
        "availability": item.get("availability"),
        "source_domain": item.get("source_domain"),
        "source_type": item.get("source_type"),
        "category_url": item.get("category_url"),
        "product_url": item.get("product_url"),
        "raw_price_text": item.get("raw_price_text"),
        "designation": designation,
    }

    normalized["match_key"] = _build_match_key(normalized)
    normalized["canonical_model_key"] = _build_canonical_model_key(
        series=series,
        poles_text=poles_text,
        rated_current_a=rated_current_a,
        trip_curve=trip_curve,
        rcd_ma=rcd_ma,
        breaking_capacity_ka=breaking_capacity_ka,
    )
    return normalized


def normalize_price_catalog(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    for item in rows:
        if not isinstance(item, dict):
            continue
        out.append(_normalize_item(item))

    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(INPUT_DEFAULT))
    parser.add_argument("--out", default=str(OUTPUT_DEFAULT))
    args = parser.parse_args()

    payload = _load_json(args.input)
    items = payload.get("items") or []
    if not isinstance(items, list):
        raise ValueError("price_catalog.json must contain a list in payload['items']")

    normalized_items = normalize_price_catalog(items)

    out_payload = {
        "source": payload.get("source"),
        "category_urls": payload.get("category_urls") or [],
        "items_count": len(normalized_items),
        "items": normalized_items,
    }
    _save_json(args.out, out_payload)
    print(f"saved: {args.out}")


if __name__ == "__main__":
    main()