from __future__ import annotations

import math
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


def _phase_type(u_v: Any, phases: Any) -> str:
    u = _safe_float(u_v)
    ph = _safe_int(phases)

    if ph == 1:
        return "single_phase"
    if ph == 3:
        return "three_phase"

    if u in (220.0, 230.0):
        return "single_phase"
    if u in (380.0, 400.0):
        return "three_phase"

    return "unknown"


def _nominal_series() -> list[int]:
    return [1, 2, 3, 4, 6, 10, 16, 20, 25, 32, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 630]


def _pick_nominal_above(current_a: float | None) -> int | None:
    if current_a is None or current_a <= 0:
        return None
    for n in _nominal_series():
        if n >= current_a:
            return n
    return _nominal_series()[-1]


def _guess_breaking_capacity(ep_kind: str, load_type: str) -> int:
    """
    Пока упрощённая логика.
    Позже можно завязать на расчёт токов КЗ.
    """
    if ep_kind in {"pump", "fan", "burner"} or load_type == "motor":
        return 10
    if ep_kind in {"cabinet", "hvac"}:
        return 6
    if ep_kind in {"lighting", "heating"}:
        return 4
    return 6


def _guess_trip_curve(ep_kind: str, load_type: str, has_vfd: bool) -> str:
    if has_vfd:
        return "C"
    if ep_kind in {"pump", "fan", "burner"} or load_type == "motor":
        return "D"
    if ep_kind in {"lighting", "heating"}:
        return "C"
    return "C"


def _device_class(ep_kind: str, phase_type: str, suggested_nominal: int | None) -> str:
    if suggested_nominal is None:
        return "unknown"

    if suggested_nominal > 125:
        return "MCCB"
    if ep_kind in {"pump", "fan", "burner", "cabinet", "lighting", "heating", "hvac"}:
        return "MCB"
    return "MCB"


def _poles_from_phase_type(phase_type: str) -> int | None:
    if phase_type == "single_phase":
        return 1
    if phase_type == "three_phase":
        return 3
    return None

def _round_up_standard_nominal(value: float, standards: list[int]) -> int:
    for s in standards:
        if value <= s:
            return s
    return standards[-1]


def _pick_motor_protection_device(selection_current_a: float) -> tuple[str, int]:
    """
    Выбор класса аппарата для двигательной нагрузки:
    - до 63 А включительно: MPCB
    - выше 63 А: MCCB

    Порог 63 А выбран как практическая граница между типовыми
    мотор-автоматами малой/средней мощности и автоматами в литом корпусе.
    """
    mpcb_nominals = [1, 2, 3, 4, 6, 10, 16, 20, 25, 32, 40, 50, 63]
    mccb_nominals = [63, 80, 100, 125, 160, 200, 250, 320, 400, 630]

    if selection_current_a <= 63:
        return "MPCB", _round_up_standard_nominal(selection_current_a, mpcb_nominals)

    return "MCCB", _round_up_standard_nominal(selection_current_a, mccb_nominals)

def _acceptable_device_classes(device_class: str) -> list[str]:
    dc = str(device_class or "").strip().upper()

    if dc == "MPCB":
        return ["MPCB", "MCB"]

    if dc == "MCCB":
        return ["MCCB"]

    if dc == "RCBO":
        return ["RCBO"]

    if dc == "MCB":
        return ["MCB"]

    return [dc] if dc else ["unknown"]


def build_requirements(items: list[dict], classification_report: list[dict]) -> list[dict]:
    """
    Формирует требования к защитному аппарату по каждому объекту.
    Это не выбор конкретной модели, а именно профиль требований.
    """
    class_map = {}
    for row in classification_report or []:
        tag = str(row.get("tag") or "").strip()
        if tag:
            class_map[tag] = row

    requirements = []

    for it in items:
        tag = str(it.get("tag") or "").strip()
        raw_display_name = it.get("display_name")
        display_name = str(raw_display_name or "").strip().lower()

        cls = class_map.get(tag, {})
        load_type = str(cls.get("load_type") or "unknown").strip().lower()
        has_vfd = bool(cls.get("has_vfd"))
        phase_type = str(cls.get("phase_type") or "").strip().lower()

        u_v = _safe_float(it.get("u_v"))
        phases = _safe_int(it.get("phases"))
        p_kw = _safe_float(it.get("p_kw"))

        equipment_class = str(it.get("equipment_class") or "").strip().lower()
        ep_kind = str(it.get("ep_kind") or "").strip().lower()

        # Ток берем с приоритетом: i_nom_a -> i_nom -> i_a
        i_nom = _safe_float(it.get("i_nom_a"))
        if i_nom is None:
            i_nom = _safe_float(it.get("i_nom"))
        if i_nom is None:
            i_nom = _safe_float(it.get("i_a"))

        i_calc = _safe_float(it.get("i_calc_a"))

        if not phase_type or phase_type == "unknown":
            phase_type = _phase_type(u_v, phases)

        # Базовый ток для подбора:
        # должен быть не ниже паспортного, если паспортный ток известен
        base_current = None
        if i_nom is not None and i_calc is not None:
            base_current = max(i_nom, i_calc)
        elif i_nom is not None:
            base_current = i_nom
        elif i_calc is not None:
            base_current = i_calc
        elif p_kw is not None and u_v:
            if phase_type == "single_phase" and u_v > 0:
                base_current = (p_kw * 1000) / u_v
            elif phase_type == "three_phase" and u_v > 0:
                base_current = (p_kw * 1000) / (math.sqrt(3) * u_v)

        # Запас 20% по алгоритму специалиста
        selection_current = base_current * 1.2 if base_current is not None else None

       # Инженерные правила выбора типа аппарата
        device_class = "MCB"
        acceptable_device_classes = ["MCB"]
        trip_curve = "C"
        preferred_trip_curve = "C"
        rcd_required = False
        suggested_nominal = None

        # 1. Горелки
        if equipment_class == "burner" or ep_kind == "burner":
            device_class = "MCB"
            trip_curve = "D"
            preferred_trip_curve = "D"

        # 2. Электрообогрев и ХВО
        elif ep_kind == "heating" or tag == "ХВО":
            device_class = "RCBO"
            trip_curve = "C"
            preferred_trip_curve = "C"
            rcd_required = True

        # 3. Насосы
        elif equipment_class == "pump" or ep_kind == "pump":
            if has_vfd:
                device_class = "MCB"
                acceptable_device_classes = ["MCB"]
                trip_curve = "C"
                preferred_trip_curve = "C"
            else:
                if selection_current is not None:
                    device_class, suggested_nominal = _pick_motor_protection_device(selection_current)
                else:
                    device_class = "MPCB"

                # Основной целевой класс сохраняем,
                # но для shortlist допускаем MCB как расширенную альтернативу,
                # если в каталоге нет достаточного покрытия MPCB у других брендов.
                if device_class == "MPCB":
                    acceptable_device_classes = ["MPCB", "MCB"]
                else:
                    acceptable_device_classes = [device_class]

                trip_curve = None
                preferred_trip_curve = "C"

        # 4. HVAC и остальные
        else:
            device_class = "MCB"
            trip_curve = "C"
            preferred_trip_curve = "C"

        if 'suggested_nominal' not in locals() or suggested_nominal is None:
            suggested_nominal = _pick_nominal_above(selection_current)

        # Отключающая способность
        if device_class == "MPCB":
            breaking_capacity_ka = 10
        elif device_class == "MCCB":
            breaking_capacity_ka = 18
        elif ep_kind in {"lighting", "heating"}:
            breaking_capacity_ka = 4
        elif equipment_class in {"burner", "pump"} or load_type == "motor":
            breaking_capacity_ka = 10
        else:
            breaking_capacity_ka = 6

        poles = _poles_from_phase_type(phase_type)
        
        acceptable_device_classes = _acceptable_device_classes(device_class)

        requirements.append(
            {
                "tag": tag,
                "display_name": raw_display_name,
                "ep_kind": ep_kind,
                "equipment_class": equipment_class,
                "load_type": load_type,
                "phase_type": phase_type,
                "voltage_v": u_v,
                "estimated_current_a": round(base_current, 3) if base_current is not None else None,
                "selection_current_a": round(selection_current, 3) if selection_current is not None else None,
                "suggested_nominal_a": suggested_nominal,
                "poles": poles,
                "trip_curve": trip_curve,
                "preferred_trip_curve": preferred_trip_curve,
                "breaking_capacity_ka": breaking_capacity_ka,
                "device_class": device_class,
                "acceptable_device_classes": acceptable_device_classes,
                "rcd_required": rcd_required,
                "has_vfd": has_vfd,
            }
        )

    return requirements