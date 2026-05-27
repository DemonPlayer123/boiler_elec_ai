from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
import math
import re


# Какие типы ЭП калибруем (моторные).
# Горелки и прочее НЕ трогаем (на будущее).
CALIBRATABLE_KINDS = {"pump", "hvac", "burner"}


def _tag_base(tag: str) -> str:
    """
    К6.2 -> К6
    K6.2 -> К6
    """
    if not tag:
        return tag
    t = str(tag).strip().replace("K", "К").replace("k", "К")
    return re.sub(r"\.\d+$", "", t)


def _norm_model(model: Optional[str]) -> str:
    if not model:
        return ""
    s = str(model).lower()
    s = s.replace(" ", "")
    s = s.replace("\u00A0", "")
    s = s.replace("-", "")
    s = s.replace("_", "")
    s = s.replace("–", "")
    s = s.replace("—", "")
    return s


def _calc_current_a(*, p_kw: float, u_v: float, phases: int, eta: float, cos_phi: float) -> float:
    """
    p_kw: кВт
    u_v: В
    phases: 1 или 3
    eta, cos_phi: доли (0..1)
    """
    p_w = p_kw * 1000.0
    denom = None

    # Защита от деления на ноль
    eta = max(1e-6, float(eta))
    cos_phi = max(1e-6, float(cos_phi))

    if phases == 1:
        denom = u_v * eta * cos_phi
    else:
        # 3-фазное
        denom = math.sqrt(3.0) * u_v * eta * cos_phi

    return p_w / denom


@dataclass(frozen=True)
class CalibKey:
    model_norm: str
    p_kw: float
    u_v: float
    phases: int
    ep_kind: str


@dataclass
class CalibResult:
    eta_pct: float
    cos_phi: float
    i_calc_a: float
    status: str  # ok / closest / no_inom / skip_kind
    error_pct: Optional[float] = None


def _grid_search(
    *,
    p_kw: float,
    u_v: float,
    phases: int,
    i_nom_a: float,
    eta_min_pct: float,
    eta_max_pct: float,
    cos_min: float,
    cos_max: float,
    eta_step_pct: float = 1.0,
    cos_step: float = 0.01,
    tolerance_pct: float = 3.0,
) -> CalibResult:
    """
    Общая калибровка для насосов/HVAC:
    - минимизировать abs(Icalc - Inom)
    - при равенстве предпочесть более высокие eta и cos
    """
    best: Optional[Tuple[float, float, float]] = None
    best_eta = None
    best_cos = None
    best_i = None

    eta = eta_min_pct
    while eta <= eta_max_pct + 1e-9:
        eta_d = eta / 100.0
        cos = cos_min
        while cos <= cos_max + 1e-12:
            i_calc = _calc_current_a(
                p_kw=p_kw,
                u_v=u_v,
                phases=phases,
                eta=eta_d,
                cos_phi=cos,
            )
            abs_err = abs(i_calc - i_nom_a)

            key = (abs_err, -eta, -cos)

            if best is None or key < best:
                best = key
                best_eta = eta
                best_cos = cos
                best_i = i_calc

            cos = round(cos + cos_step, 10)
        eta = round(eta + eta_step_pct, 10)

    assert best_eta is not None and best_cos is not None and best_i is not None

    err_pct = abs(best_i - i_nom_a) / max(1e-9, i_nom_a) * 100.0
    status = "ok" if err_pct <= tolerance_pct else "closest"

    return CalibResult(
        eta_pct=float(best_eta),
        cos_phi=float(best_cos),
        i_calc_a=float(best_i),
        status=status,
        error_pct=float(err_pct),
    )

def _grid_search_burner(
    *,
    p_kw: float,
    u_v: float,
    phases: int,
    i_nom_a: float,
    cos_min: float = 0.80,
    cos_max: float = 0.90,
    cos_step: float = 0.01,
) -> CalibResult:
    """
    Спец-алгоритм для горелок по правилу специалиста:
    - eta_pct всегда 100%
    - меняем только cos_phi в диапазоне 0.80..0.90
    - выбираем минимальный Icalc, который >= Inom
    - если такого нет, берём closest_below_nominal
    """
    eta_pct = 100.0

    best_above = None   # (delta_up, -cos)
    best_above_cos = None
    best_above_i = None

    best_below = None   # (abs_err, -cos)
    best_below_cos = None
    best_below_i = None

    cos = cos_min
    while cos <= cos_max + 1e-12:
        i_calc = _calc_current_a(
            p_kw=p_kw,
            u_v=u_v,
            phases=phases,
            eta=1.0,
            cos_phi=cos,
        )

        if i_calc >= i_nom_a:
            delta_up = i_calc - i_nom_a
            key = (delta_up, -cos)
            if best_above is None or key < best_above:
                best_above = key
                best_above_cos = cos
                best_above_i = i_calc
        else:
            abs_err = abs(i_calc - i_nom_a)
            key = (abs_err, -cos)
            if best_below is None or key < best_below:
                best_below = key
                best_below_cos = cos
                best_below_i = i_calc

        cos = round(cos + cos_step, 10)

    if best_above_cos is not None:
        err_pct = abs(best_above_i - i_nom_a) / max(1e-9, i_nom_a) * 100.0
        return CalibResult(
            eta_pct=100.0,
            cos_phi=float(best_above_cos),
            i_calc_a=float(best_above_i),
            status="ok",
            error_pct=float(err_pct),
        )

    assert best_below_cos is not None and best_below_i is not None
    err_pct = abs(best_below_i - i_nom_a) / max(1e-9, i_nom_a) * 100.0
    return CalibResult(
        eta_pct=100.0,
        cos_phi=float(best_below_cos),
        i_calc_a=float(best_below_i),
        status="closest_below_nominal",
        error_pct=float(err_pct),
    )

def calibrate_items(
    items: List[Dict[str, Any]],
    user_inputs: Optional[Dict[str, Dict[str, Any]]] = None,
    *,
    eta_min_pct: float = 75.0,
    eta_max_pct: float = 90.0,
    cos_min: float = 0.80,
    cos_max: float = 0.90,
    eta_max_ext_pct: float = 100.0,
    cos_max_ext: float = 1.00,
    eta_step_pct: float = 1.0,
    cos_step: float = 0.01,
    tolerance_pct: float = 3.0,
) -> List[Dict[str, Any]]:
    """
    Мутирует items (in-place) и возвращает их же (для удобства).

    Правила:
    - P (p_kw) не меняем.
    - Если есть Iном -> подбираем eta_pct и cos_phi в заданных диапазонах.
    - Если Iном нет -> ставим cos_phi = min(cos_max, 0.88) и eta_pct = clamp(existing or 80).
    - Для одинаковых ЭП используем кэш по CalibKey.
    """
    user_inputs = user_inputs or {}
    cache: Dict[CalibKey, CalibResult] = {}

    for it in items:
        tag = str(it.get("tag") or "").strip()
        tag_base = _tag_base(tag)

        ep_kind = (it.get("ep_kind") or "").strip()
        # ===== Жёсткие правила (не калибруем вообще) =====
        if ep_kind in ("heating", "cabinet", "lighting"):
            it["eta_pct"] = 100.0
            it["cos_phi"] = 1.0
            # ток посчитаем, если есть P/U
            p_kw = it.get("p_kw")
            u_v = it.get("u_v")
            phases = it.get("phases") or 1
            try:
                p_kw_f = float(p_kw)
                u_v_f = float(u_v)
                phases_i = int(phases)
                it["i_calc_a"] = float(_calc_current_a(p_kw=p_kw_f, u_v=u_v_f, phases=phases_i, eta=1.0, cos_phi=1.0))
                it["calibration_status"] = "fixed"
            except Exception:
                it["calibration_status"] = "missing_p_u"
            it["calibrated"] = False
            continue

        # Моторные калибруем, прочее пропускаем
        if ep_kind and ep_kind not in CALIBRATABLE_KINDS:
            it["calibrated"] = False
            it["calibration_status"] = "skip_kind"
            continue

        # Требуемые исходники
        p_kw = it.get("p_kw")
        u_v = it.get("u_v")
        phases = it.get("phases") or 3

        try:
            p_kw_f = float(p_kw)
            u_v_f = float(u_v)
            phases_i = int(phases)
        except Exception:
            # Без этих данных калибровка невозможна
            it["calibrated"] = False
            it["calibration_status"] = "missing_p_u"
            continue

        # Целевой ток: приоритет user_inputs, затем item
        i_nom = None
        if tag_base in user_inputs and user_inputs[tag_base].get("i_a") is not None:
            i_nom = float(user_inputs[tag_base]["i_a"])
        elif it.get("i_a") is not None:
            # если i_a пришёл из паспорта как номинал — используем
            i_nom = float(it["i_a"])

        model_norm = _norm_model(it.get("model"))
        key = CalibKey(model_norm=model_norm, p_kw=round(p_kw_f, 3), u_v=round(u_v_f, 1), phases=phases_i, ep_kind=ep_kind)

        if i_nom is None:
            # Нет номинального тока -> дефолт и пометка на опрос пользователю
            eta_existing = it.get("eta_pct")
            if eta_existing is None and tag_base in user_inputs:
                eta_existing = user_inputs[tag_base].get("eta_pct")
            try:
                eta_existing = float(eta_existing) if eta_existing is not None else 80.0
            except Exception:
                eta_existing = 80.0

            eta_clamped = min(max(eta_existing, eta_min_pct), eta_max_pct)
            cos_default = min(cos_max, 0.88)

            it["eta_pct"] = float(eta_clamped)
            it["cos_phi"] = float(cos_default)
            it["i_calc_a"] = float(_calc_current_a(p_kw=p_kw_f, u_v=u_v_f, phases=phases_i, eta=eta_clamped / 100.0, cos_phi=cos_default))
            it["calibrated"] = False
            it["calibration_status"] = "no_inom"
            continue

        # Есть Iном -> кэш/подбор
                # Есть Iном -> кэш/подбор
        if key in cache:
            res = cache[key]
        else:
            # Спец-ветка для горелок:
            # eta = 100%, меняем только cos_phi 0.80..0.90
            if ep_kind == "burner":
                res = _grid_search_burner(
                    p_kw=p_kw_f,
                    u_v=u_v_f,
                    phases=phases_i,
                    i_nom_a=i_nom,
                    cos_min=0.80,
                    cos_max=0.90,
                    cos_step=cos_step,
                )
                calib_pass = "burner_cos_only"

            else:
                # 1) основной диапазон для насосов/HVAC
                res_primary = _grid_search(
                    p_kw=p_kw_f,
                    u_v=u_v_f,
                    phases=phases_i,
                    i_nom_a=i_nom,
                    eta_min_pct=eta_min_pct,
                    eta_max_pct=eta_max_pct,
                    cos_min=cos_min,
                    cos_max=cos_max,
                    eta_step_pct=eta_step_pct,
                    cos_step=cos_step,
                    tolerance_pct=tolerance_pct,
                )

                res = res_primary
                calib_pass = "primary"

                # 2) расширенный диапазон только если основной не попал в допуск
                if res_primary.status == "closest":
                    res_ext = _grid_search(
                        p_kw=p_kw_f,
                        u_v=u_v_f,
                        phases=phases_i,
                        i_nom_a=i_nom,
                        eta_min_pct=eta_min_pct,
                        eta_max_pct=eta_max_ext_pct,
                        cos_min=cos_min,
                        cos_max=cos_max_ext,
                        eta_step_pct=eta_step_pct,
                        cos_step=cos_step,
                        tolerance_pct=tolerance_pct,
                    )

                    if (res_ext.error_pct or 1e9) < (res_primary.error_pct or 1e9):
                        res = res_ext
                        calib_pass = "extended"

            cache[key] = res
            it["calibration_pass"] = calib_pass

            if calib_pass == "extended" and (
                res.cos_phi > cos_max + 1e-9 or res.eta_pct > eta_max_pct + 1e-9
            ):
                it["calibration_warning"] = "extended_limits"
        
        # применяем
        it["eta_pct"] = float(res.eta_pct)
        it["cos_phi"] = float(res.cos_phi)
        it["i_calc_a"] = float(res.i_calc_a)
        it["i_nom_a"] = float(i_nom)

        it["calibrated"] = (res.status == "ok")
        it["calibration_status"] = res.status
        if res.error_pct is not None:
            it["calibration_error_pct"] = float(res.error_pct)

    return items