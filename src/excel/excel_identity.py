import re
from typing import Any


def _tail_int(tag: str) -> int | None:
    m = re.search(r"(\d+)$", str(tag or ""))
    return int(m.group(1)) if m else None


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip()).lower()


def _is_pump_item(it: dict) -> bool:
    if str(it.get("equipment_class") or "").lower() == "pump":
        return True
    if str(it.get("ep_kind") or "").lower() == "pump":
        return True

    tag = str(it.get("tag") or "").strip().upper()
    if re.fullmatch(r"К\d+(?:\.\d+)?", tag):
        return True

    name = _norm(it.get("display_name"))
    return "насос" in name


def _is_burner_item(it: dict) -> bool:
    if str(it.get("equipment_class") or "").lower() == "burner":
        return True
    tag = str(it.get("tag") or "")
    return tag.upper().startswith("ГГ")


def _is_hvac_aggregate(it: dict) -> bool:
    tag = str(it.get("tag") or "")
    if re.fullmatch(r"[АA]\d+", tag, flags=re.IGNORECASE):
        return True
    name = _norm(it.get("display_name"))
    return "агрегат" in name or "тепловентилятор" in name


def _is_hvac_fan(it: dict) -> bool:
    tag = str(it.get("tag") or "").strip()

    # Вытяжку сюда не пускаем
    if re.fullmatch(r"[ВV]\d+", tag, flags=re.IGNORECASE):
        return False

    if re.fullmatch(r"[ПP]\d+", tag, flags=re.IGNORECASE):
        return True

    name = _norm(it.get("display_name"))
    return "вентилятор" in name and "вытяж" not in name


def _is_exhaust_fan(it: dict) -> bool:
    tag = str(it.get("tag") or "").strip()
    if re.fullmatch(r"[ВV]\d+", tag, flags=re.IGNORECASE):
        return True

    name = _norm(it.get("display_name"))
    return "вытяж" in name


def _is_lighting_group(it: dict) -> bool:
    tag = str(it.get("tag") or "")
    return tag.lower().startswith("гр.")


def _is_heating_trace(it: dict) -> bool:
    tag = str(it.get("tag") or "")
    return tag.upper().startswith(("EK", "ЕК"))


def _pump_sort_key(it: dict) -> tuple:
    """
    Порядок для насосов должен быть воспроизводим на аналогичных котельных.
    Сначала пытаемся использовать исходный tag К4..К9, затем display_name.
    """
    tag = str(it.get("tag") or "")
    n = _tail_int(tag)
    return (
        0 if n is not None else 1,
        n if n is not None else 9999,
        _norm(it.get("display_name")),
        _norm(it.get("model")),
    )


def build_excel_identity(items: list[dict]) -> list[dict]:
    """
    Добавляет:
      - excel_tag_canonical
      - template_match_key
      - template_group
    """
    items = [dict(x) for x in items]

    # # 1. Насосы нумеруем детерминированно: М1, М2, ...
    # pump_items = [it for it in items if _is_pump_item(it)]
    # pump_items_sorted = sorted(pump_items, key=_pump_sort_key)

    # pump_tag_map: dict[str, str] = {}
    # for idx, it in enumerate(pump_items_sorted, start=1):
    #     tag = str(it.get("tag") or "")
    #     if tag:
    #         pump_tag_map[tag] = f"М{idx}"

    for it in items:
        tag = str(it.get("tag") or "")
        display_name = str(it.get("display_name") or "")
        equipment_class = str(it.get("equipment_class") or "")
        excel_tag = None
        template_group = None

        # Горелки: ГГ.1 -> Г1
        if _is_burner_item(it):
            n = _tail_int(tag)
            if n is not None:
                excel_tag = f"Г{n}"
                template_group = "burner"

        # Насосы: К4.. -> М1..
        elif _is_pump_item(it):
            # excel_tag = pump_tag_map.get(tag)
            excel_tag = None
            template_group = "pump"

                # Агрегаты: А1..
        elif _is_hvac_aggregate(it):
            n = _tail_int(tag)
            if n is not None:
                excel_tag = f"А{n}"
                template_group = "hvac_aggregate"

        # Вытяжка: В1..
        elif _is_exhaust_fan(it):
            n = _tail_int(tag)
            if n is not None:
                excel_tag = f"В{n}"
                template_group = "exhaust_fan"

        # Вентиляторы: П1..
        elif _is_hvac_fan(it):
            n = _tail_int(tag)
            if n is not None:
                excel_tag = f"П{n}"
                template_group = "hvac_fan"

        # Освещение
        elif _is_lighting_group(it):
            n = _tail_int(tag)
            if n is not None:
                excel_tag = f"Гр.{n}"
                template_group = "lighting"

        # Электрообогрев
        elif _is_heating_trace(it):
            n = _tail_int(tag)
            if n is not None:
                excel_tag = f"ЕК{n}"
                template_group = "heating_trace"

        # Шкафы и спецпозиции
        elif tag in {"ШУК", "ЩУТ", "ЩУГ", "ШСС", "ХВО", "ПЭСПЗ", "ВН"}:
            excel_tag = tag
            template_group = "fixed_tag"

        elif tag.upper().startswith("ШК"):
            n = _tail_int(tag)
            if n is not None:
                excel_tag = f"ШК{n}"
                template_group = "boiler_cabinet"

        # fallback
        if excel_tag is None and tag and template_group != "pump":
            excel_tag = tag
            template_group = template_group or "raw_tag"
        
        it["excel_tag_canonical"] = excel_tag
        it["template_match_key"] = tag if template_group == "pump" else (excel_tag or display_name or tag)
        it["template_group"] = template_group or equipment_class or "unknown"

    return items