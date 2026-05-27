from __future__ import annotations

from typing import Any


def _norm_text(value: Any) -> str:
    return str(value or "").strip().lower()


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


def _curve_rank(curve: str) -> int:
    curve = _norm_text(curve)
    if curve == "b":
        return 1
    if curve == "c":
        return 2
    if curve == "d":
        return 3
    return 99


def _curve_is_acceptable(required: str, actual: str) -> bool:
    """
    Строгая проектная логика:
    - если нужен C -> только C
    - если нужен D -> только D
    - если нужен B -> только B
    - если требование не задано -> не фильтруем
    """
    req = _norm_text(required)
    act = _norm_text(actual)

    if not req or req == "unknown":
        return True
    if not act:
        return False

    return req == act

def _distance_to_range(value: float, rng_min: float, rng_max: float) -> float:
    if value < rng_min:
        return rng_min - value
    if value > rng_max:
        return value - rng_max
    return 0.0

def _score_candidate_match(req: dict, cat: dict) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    req_device_class = _norm_text(req.get("device_class"))
    cat_device_class = _norm_text(cat.get("device_class"))
    req_nominal = _safe_float(req.get("suggested_nominal_a"))
    req_poles = _safe_int(req.get("poles"))
    req_curve = req.get("trip_curve")
    req_breaking = _safe_float(req.get("breaking_capacity_ka"))

    cat_nominal = _safe_float(cat.get("rated_current_a"))
    cat_poles = _safe_int(cat.get("poles"))
    cat_curve = cat.get("trip_curve")
    cat_breaking = _safe_float(cat.get("breaking_capacity_ka"))
    
    # 0. Приоритет основного класса аппарата
    if req_device_class and cat_device_class:
        if req_device_class == cat_device_class:
            score += 4.0
            reasons.append(f"совпадает целевой класс аппарата: {cat_device_class.upper()}")
        elif req_device_class == "mpcb" and cat_device_class == "mcb":
            score -= 4.0
            reasons.append("MCB рассматривается только как fallback относительно требуемого MPCB")

    # 1. Близость номинала
    if req_nominal is not None:
        if req_device_class == "mpcb":
            rng = cat.get("current_range_a") or {}
            rng_min = _safe_float(rng.get("min"))
            rng_max = _safe_float(rng.get("max"))

            if rng_min is not None and rng_max is not None:
                dist = _distance_to_range(req_nominal, rng_min, rng_max)

                if dist == 0:
                    mid = (rng_min + rng_max) / 2.0
                    gap = abs(mid - req_nominal)
                    score += max(0.0, 8.0 - gap)
                    reasons.append(f"ток {req_nominal} А попадает в диапазон {rng_min}-{rng_max} А")
                else:
                    # мягкий fallback для shortlist/UI:
                    # если диапазон близок к требуемому току, не отбрасываем совсем
                    score += max(0.0, 3.0 - dist * 0.5)
                    reasons.append(
                        f"требуемый ток {req_nominal} А не входит в диапазон {rng_min}-{rng_max} А, "
                        f"но кандидат близок по диапазону"
                    )
            elif cat_nominal is not None:
                gap = abs(cat_nominal - req_nominal)
                score += max(0.0, 6.0 - gap * 0.25)
                reasons.append(f"номинал {cat_nominal} А сопоставлен с требованием {req_nominal} А")
        else:
            if cat_nominal is not None:
                gap = float(cat_nominal) - float(req_nominal)
                if gap >= 0:
                    score += max(0.0, 10.0 - gap * 0.5)
                    reasons.append(f"номинал {cat_nominal} А покрывает требование {req_nominal} А")

    # 2. Полюса
    if req_poles is not None and cat_poles is not None:
        if req_poles == cat_poles:
            score += 3.0
            reasons.append(f"совпадает число полюсов: {cat_poles}")

    # 3. Кривая
    if req_curve and cat_curve:
        if _norm_text(req_curve) == _norm_text(cat_curve):
            score += 3.0
            reasons.append(f"совпадает характеристика {cat_curve}")
        elif _curve_is_acceptable(req_curve, cat_curve):
            score += 1.0
            reasons.append(f"характеристика {cat_curve} допустима при требовании {req_curve}")

    # 4. Отключающая способность
    if req_breaking is not None and cat_breaking is not None:
        margin = float(cat_breaking) - float(req_breaking)
        if margin >= 0:
            score += max(0.5, 2.0 - margin * 0.1)
            reasons.append(
                f"отключающая способность {cat_breaking} кА покрывает требование {req_breaking} кА"
            )

    # 5. Небольшой бонус за более содержательные серии
    vendor = _norm_text(cat.get("vendor"))
    series = _norm_text(cat.get("series"))

    if vendor in {"chint", "dekraft", "keaz"}:
        score += 0.25

    if req_device_class == "mcb" and series in {"nxb-63h", "ва47-100", "ва47-29", "ва-103", "ва-201"}:
        score += 0.5

    if req_device_class == "rcbo" and series in {"nb2le-80zt", "диф-103", "авдт32", "ад12", "ад14"}:
        score += 0.5

    if req_device_class == "mpcb" and series in {"ва-430"}:
        score += 1.0

    return round(score, 4), reasons

def select_catalog_candidates(
    requirements: list[dict],
    catalog_items: list[dict],
) -> list[dict]:
    """
    Для каждого requirements-объекта выбирает все формально подходящие
    позиции из каталога.
    """
    out: list[dict] = []

    for req in requirements or []:
        tag = req.get("tag")
        req_device_class = _norm_text(req.get("device_class"))
        req_device_classes_raw = req.get("acceptable_device_classes") or []
        req_device_classes = {
            _norm_text(x) for x in req_device_classes_raw if _norm_text(x)
        }
        if not req_device_classes and req_device_class:
            req_device_classes = {req_device_class}
        req_nominal = _safe_float(req.get("suggested_nominal_a"))
        req_poles = _safe_int(req.get("poles"))
        req_curve = req.get("trip_curve")
        req_preferred_curve = req.get("preferred_trip_curve")
        req_breaking = _safe_float(req.get("breaking_capacity_ka"))

        for cat in catalog_items or []:
            cat_device_class = _norm_text(cat.get("device_class"))
            cat_nominal = _safe_float(cat.get("rated_current_a"))
            cat_poles = _safe_int(cat.get("poles"))
            cat_curve = cat.get("trip_curve")
            cat_breaking = _safe_float(cat.get("breaking_capacity_ka"))

            # 1. класс аппарата
            if req_device_classes and "unknown" not in req_device_classes:
                if cat_device_class not in req_device_classes:
                    continue

            # 2. номинал / диапазон уставки
            if req_nominal is not None:
                if req_device_class == "mpcb":
                    rng = cat.get("current_range_a") or {}
                    rng_min = _safe_float(rng.get("min"))
                    rng_max = _safe_float(rng.get("max"))

                    # Для MPCB:
                    # 1) если ток попадает в диапазон — идеальный матч
                    # 2) если не попадает, но диапазон близок — оставляем как расширенную альтернативу
                    # 3) совсем далекие варианты отбрасываем
                    if rng_min is not None and rng_max is not None:
                        dist = _distance_to_range(req_nominal, rng_min, rng_max)

                        # если далеко от диапазона, не тащим мусор
                        if dist > 6.0:
                            continue
                    else:
                        # fallback, если диапазон в каталоге не указан
                        if cat_nominal is None:
                            continue

                        # для MPCB допускаем умеренный переразмер как расширенную альтернативу
                        if cat_nominal < req_nominal:
                            continue
                        if cat_nominal > req_nominal * 2.0:
                            continue

            # 3. полюса
            if req_poles is not None:
                if cat_poles is None or cat_poles != req_poles:
                    continue

            # 4. характеристика
            effective_req_curve = req_curve

            # Для насосов без ЧРП и других случаев, где основной класс не MCB,
            # но MCB допускается как fallback, используем preferred_trip_curve.
            if not effective_req_curve and cat_device_class == "mcb":
                effective_req_curve = req_preferred_curve

            if not _curve_is_acceptable(effective_req_curve, cat_curve):
                continue

            # 5. отключающая способность
            if req_breaking is not None:
                if req_device_class == "mpcb":
                    # Для MPCB на первом этапе допускаем отсутствие breaking_capacity_ka
                    # в metadata, чтобы не отбрасывать валидные кандидаты ВА-430 по диапазону уставки.
                    if cat_breaking is not None and cat_breaking < req_breaking:
                        continue
                else:
                    if cat_breaking is None or cat_breaking < req_breaking:
                        continue

            match_score, match_reasons = _score_candidate_match(req, cat)

            out.append(
                {
                    "tag": tag,
                    "requirement_ref": {
                        "device_class": req.get("device_class"),
                        "acceptable_device_classes": req.get("acceptable_device_classes"),
                        "suggested_nominal_a": req.get("suggested_nominal_a"),
                        "poles": req.get("poles"),
                        "trip_curve": req.get("trip_curve"),
                        "breaking_capacity_ka": req.get("breaking_capacity_ka"),
                    },
                    "candidate": {
                        "vendor": cat.get("vendor"),
                        "series": cat.get("series"),
                        "model": cat.get("model"),
                        "device_class": cat.get("device_class"),
                        "rated_current_a": cat.get("rated_current_a"),
                        "current_range_a": cat.get("current_range_a"),
                        "poles": cat.get("poles"),
                        "trip_curve": cat.get("trip_curve"),
                        "breaking_capacity_ka": cat.get("breaking_capacity_ka"),
                        "rcd_ma": cat.get("rcd_ma"),
                    },
                    "selector_score": match_score,
                    "selector_reasons": match_reasons,
                }
            )

    return out