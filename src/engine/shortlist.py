from __future__ import annotations

from typing import Any


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None

def _rcd_pref_score(value: Any) -> int:
    """
    Чем меньше, тем лучше.
    Для RCBO приоритет:
    30mA -> лучший
    10mA -> допустимо, но ниже
    100/300mA -> ещё ниже
    unknown -> самый плохой
    """
    v = _safe_int(value)
    if v == 30:
        return 0
    if v == 10:
        return 1
    if v == 100:
        return 2
    if v == 300:
        return 3
    return 9

def _selector_score_sort_value(value: Any) -> float:
    """
    Для shortlist:
    чем БОЛЬШЕ selector_score, тем лучше.
    В sort tuple нам нужно, наоборот, меньше = лучше,
    поэтому возвращаем отрицательное значение.
    Если score нет, даём нейтрально-плохое значение.
    """
    v = _safe_float(value)
    if v is None:
        return 9999.0
    return -v

def _vendor_priority(value: Any) -> int:
    """
    Чем меньше, тем лучше.
    Явный детерминированный tie-break по вендору.

    Текущая политика:
    CHINT -> 0
    KEAZ -> 1
    Dekraft -> 2
    unknown -> 9
    """
    v = str(value or "").strip().upper()
    if v == "CHINT":
        return 0
    if v == "KEAZ":
        return 1
    if v == "DEKRAFT":
        return 2
    return 9

def _score_candidate(row: dict) -> tuple:
    """
    Чем меньше score, тем лучше.

    Приоритеты:
    1. совпадение по функциональному профилю:
       - для MPCB: лучший матч по диапазону current_range_a
       - для MCB/RCBO: минимальный переразмер по току
    2. совпадение по trip_curve
    3. минимальный переразмер по кА
    4. для RCBO: предпочтение 30mA
    5. наличие серии/модели
    """
    req = row.get("requirement_ref") or {}
    cat = row.get("candidate") or {}

    req_cls = str(req.get("device_class") or "").strip().upper()
    cat_cls = str(cat.get("device_class") or "").strip().upper()

    req_nom = _safe_float(req.get("suggested_nominal_a"))
    cat_nom = _safe_float(cat.get("rated_current_a"))

    req_ka = _safe_float(req.get("breaking_capacity_ka"))
    cat_ka = _safe_float(cat.get("breaking_capacity_ka"))

    req_curve = str(req.get("trip_curve") or "").strip().upper()
    cat_curve = str(cat.get("trip_curve") or "").strip().upper()

    cat_vendor = str(cat.get("vendor") or "").strip()
    cat_model = str(cat.get("model") or "").strip()

    selector_sort = _selector_score_sort_value(row.get("selector_score"))

    # --- 1. Номинал / диапазон ---
    nominal_penalty = 9999.0

    if req_cls == "MPCB":
        rng = cat.get("current_range_a") or {}
        rng_min = _safe_float(rng.get("min"))
        rng_max = _safe_float(rng.get("max"))

        if req_nom is not None and rng_min is not None and rng_max is not None:
            if rng_min <= req_nom <= rng_max:
                # Чем ближе требуемый ток к верхней границе диапазона, тем лучше.
                nominal_penalty = rng_max - req_nom
            elif cat_nom is not None:
                nominal_penalty = abs(cat_nom - req_nom) + 1000
        elif req_nom is not None and cat_nom is not None:
            nominal_penalty = abs(cat_nom - req_nom) + 1000
    else:
        if req_nom is not None and cat_nom is not None:
            margin = cat_nom - req_nom
            # если selector пропустил неподходящее, это должно тонуть в ранжировании
            if margin < 0:
                nominal_penalty = 10000 + abs(margin)
            else:
                # усиливаем штраф за сильный переразмер
                ratio = cat_nom / req_nom if req_nom > 0 else 999
                oversize_penalty = 0.0
                if ratio > 1.25:
                    oversize_penalty += (ratio - 1.25) * 100
                nominal_penalty = margin + oversize_penalty

    # --- 2. Trip curve ---
    if req_curve:
        curve_penalty = 0 if cat_curve == req_curve else 10
    else:
        curve_penalty = 0

    # --- 3. Отключающая способность ---
    if req_ka is None:
        ka_penalty = 9999
    elif cat_ka is None:
        # Для MPCB допускаем unknown ka, но не считаем лучшим
        ka_penalty = 50 if req_cls == "MPCB" else 9999
    else:
        ka_penalty = max(cat_ka - req_ka, 0)

    # --- 4. RCD preference only for RCBO ---
    rcd_penalty = 0
    if req_cls == "RCBO" and cat_cls == "RCBO":
        rcd_penalty = _rcd_pref_score(cat.get("rcd_ma"))

    has_model_penalty = 0 if cat.get("model") else 1
    has_series_penalty = 0 if cat.get("series") else 1

    vendor_penalty = _vendor_priority(cat_vendor)
    model_penalty = cat_model or "zzz"

    return (
        selector_sort,
        nominal_penalty,
        curve_penalty,
        ka_penalty,
        rcd_penalty,
        vendor_penalty,
        has_model_penalty,
        has_series_penalty,
        model_penalty,
    )
        
def build_shortlist(candidates: list[dict], top_n: int = 8) -> list[dict]:
    """
    Группирует кандидатов по tag и формирует shortlist:
    1) лучший кандидат overall
    2) затем лучшие кандидаты других брендов
    3) затем добивка оставшимися по общему ранжированию
    """
    by_tag: dict[str, list[dict]] = {}

    for row in candidates or []:
        tag = str(row.get("tag") or "").strip()
        if not tag:
            continue
        by_tag.setdefault(tag, []).append(row)

    shortlist: list[dict] = []

    for tag, rows in by_tag.items():
        ranked = sorted(rows, key=_score_candidate)

        selected: list[dict] = []
        used_keys: set[tuple[str, str, str]] = set()
        used_vendors: set[str] = set()

        def _row_key(r: dict) -> tuple[str, str, str]:
            cat = r.get("candidate") or {}
            return (
                str(cat.get("vendor") or "").strip().upper(),
                str(cat.get("series") or "").strip().upper(),
                str(cat.get("model") or "").strip().upper(),
            )

        # 1. Лучший кандидат overall
        if ranked:
            best = ranked[0]
            selected.append(best)
            used_keys.add(_row_key(best))
            best_vendor = str((best.get("candidate") or {}).get("vendor") or "").strip().upper()
            if best_vendor:
                used_vendors.add(best_vendor)

        # 2. Лучший кандидат каждого другого бренда
        for row in ranked[1:]:
            if len(selected) >= top_n:
                break

            cat = row.get("candidate") or {}
            vendor = str(cat.get("vendor") or "").strip().upper()
            key = _row_key(row)

            if key in used_keys:
                continue

            if vendor and vendor not in used_vendors:
                selected.append(row)
                used_keys.add(key)
                used_vendors.add(vendor)

        # 3. Добивка оставшимися лучшими кандидатами
        for row in ranked[1:]:
            if len(selected) >= top_n:
                break

            key = _row_key(row)
            if key in used_keys:
                continue

            selected.append(row)
            used_keys.add(key)

        shortlist.append(
            {
                "tag": tag,
                "candidates_count": len(rows),
                "top_candidates": selected,
            }
        )

    return shortlist