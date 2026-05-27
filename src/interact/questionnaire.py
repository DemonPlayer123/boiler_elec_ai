from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


FIELDS_ORDER = ["p_kw", "u_v", "i_a", "eta_pct", "phases"]


def _load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _parse_float(s: str) -> Optional[float]:
    s = s.strip()
    if s == "":
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_int(s: str) -> Optional[int]:
    s = s.strip()
    if s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _prompt_value(tag: str, field: str, current: Any) -> Any:
    """
    Возвращает значение или None (если пропустить).
    """
    if field == "u_v":
        while True:
            s = input(f"[{tag}] Введите напряжение U, В (например 220/380) или Enter чтобы пропустить: ").strip()
            if s == "":
                return None
            v = _parse_int(s)
            if v in (220, 230, 380, 400, 415):
                return v
            print("  ! Неверное U. Допустимо: 220/230/380/400/415")

    if field == "phases":
        while True:
            s = input(f"[{tag}] Введите число фаз (1 или 3) или Enter чтобы пропустить: ").strip()
            if s == "":
                return None
            v = _parse_int(s)
            if v in (1, 3):
                return v
            print("  ! Неверно. Введите 1 или 3.")

    if field == "i_a":
        while True:
            s = input(f"[{tag}] Введите номинальный ток I, А (например 11.2) или Enter чтобы пропустить: ").strip()
            if s == "":
                return None
            v = _parse_float(s)
            if v is not None and v > 0:
                return v
            print("  ! Неверный ток. Пример: 11.2")

    if field == "eta_pct":
        while True:
            s = input(f"[{tag}] Введите КПД/эффективность, % (например 78) или Enter чтобы пропустить: ").strip()
            if s == "":
                return None
            v = _parse_float(s)
            if v is not None and 1 <= v <= 100:
                return v
            print("  ! Неверный КПД. Диапазон: 1..100")

    if field == "p_kw":
        while True:
            s = input(f"[{tag}] Введите мощность P, кВт (например 4.89) или Enter чтобы пропустить: ").strip()
            if s == "":
                return None
            v = _parse_float(s)
            if v is not None and v > 0:
                return v
            print("  ! Неверная мощность. Пример: 4.89")

    # неизвестное поле
    s = input(f"[{tag}] Введите {field} (текущее={current}) или Enter чтобы пропустить: ").strip()
    return s if s else None


def build_missing_report(passports_parsed: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Возвращает список:
      [{tag, missing_fields, source_file, extracted:{...}}]
    """
    report = []
    for d in passports_parsed:
        tag = d.get("tag")
        if not tag:
            continue
        missing = d.get("missing_fields") or []
        report.append({
            "tag": tag,
            "source_file": d.get("source_file"),
            "missing_fields": missing,
            "extracted": {
                "p_kw": d.get("p_kw"),
                "u_v": d.get("u_v"),
                "i_a": d.get("i_a"),
                "eta_pct": d.get("eta_pct"),
                "phases": d.get("phases"),
            }
        })
    # сначала те, у кого больше дыр
    report.sort(key=lambda x: (-len(x["missing_fields"]), x["tag"]))
    return report

def build_missing_report_from_items(items_final: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Аналог отчёта, но по items_final.json (для ЭП, которые пришли из схем и не имеют паспорта).
    Берём только те, у кого явно не хватает базовых полей для расчёта.
    """
    report = []
    for d in items_final:
        tag = d.get("tag")
        if not tag:
            continue

        # Важный триггер: либо статус missing_p_u, либо реально пустые поля
        status = str(d.get("calibration_status") or "").lower()
        missing = []
        if d.get("p_kw") is None:
            missing.append("p_kw")
        if d.get("u_v") is None:
            missing.append("u_v")
        if d.get("phases") is None:
            missing.append("phases")
        # ток и кпд тоже можно спрашивать (для калибровки), но они не критичны для первичного расчёта
        if d.get("i_a") is None:
            missing.append("i_a")
        if d.get("eta_pct") is None:
            missing.append("eta_pct")

        if not missing:
            continue

        if "missing_p_u" not in status and ("p_kw" not in missing and "u_v" not in missing and "phases" not in missing):
            # если не missing_p_u и базовые поля есть — пропускаем
            continue

        report.append({
            "tag": tag,
            "source_file": d.get("source_file") or "",
            "missing_fields": missing,
            "extracted": {
                "p_kw": d.get("p_kw"),
                "u_v": d.get("u_v"),
                "i_a": d.get("i_a"),
                "eta_pct": d.get("eta_pct"),
                "phases": d.get("phases"),
            }
        })

    report.sort(key=lambda x: (-len(x["missing_fields"]), x["tag"]))
    return report

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--passports_parsed", type=str, required=True, help="Путь к passports_parsed.json")
    ap.add_argument("--items_final", type=str, default="", help="(опционально) Путь к items_final.json, чтобы спрашивать missing_p_u по схемным ЭП")
    ap.add_argument("--out_user_inputs", type=str, required=True, help="Куда сохранить user_inputs.json")
    ap.add_argument("--only_missing", action="store_true", help="Спрашивать только по отсутствующим полям (по умолчанию да)")
    ap.add_argument("--tags", type=str, default="", help="Ограничить опрос: например 'К4,К6,К9'")
    args = ap.parse_args()

    passports_path = Path(args.passports_parsed)
    out_path = Path(args.out_user_inputs)

    passports_parsed = _load_json(passports_path)
    if not isinstance(passports_parsed, list):
        raise ValueError("passports_parsed.json должен быть списком объектов")

    tags_filter = set()
    if args.tags.strip():
        tags_filter = {t.strip() for t in args.tags.split(",") if t.strip()}

    report = build_missing_report(passports_parsed)
    
    # (опционально) добираем missing из items_final.json (схемные ЭП без паспорта)
    if args.items_final.strip():
        items_path = Path(args.items_final)
        items_final = _load_json(items_path)
        if not isinstance(items_final, list):
            raise ValueError("items_final.json должен быть списком объектов")
        report_items = build_missing_report_from_items(items_final)

        # склеиваем, но не дублируем теги: приоритет у паспортов (они точнее)
        existing_tags = {x["tag"] for x in report}
        for r in report_items:
            if r["tag"] not in existing_tags:
                report.append(r)

        report.sort(key=lambda x: (-len(x["missing_fields"]), x["tag"]))

    user_inputs: Dict[str, Dict[str, Any]] = {}

    print("\n=== Опрос недостающих параметров ===\n")
    for item in report:
        tag = item["tag"]
        if tags_filter and tag not in tags_filter:
            continue

        missing = item["missing_fields"]
        extracted = item["extracted"]
        src = item["source_file"]

        if args.only_missing and not missing:
            continue

        print(f"\n--- {tag} (паспорт: {src}) ---")
        print("Извлечено:", extracted)
        if missing:
            print("Не хватает:", ", ".join(missing))
        else:
            print("Не хватает: (нет)")

        # какие поля спрашивать
        fields_to_ask = missing if args.only_missing else FIELDS_ORDER

        for field in FIELDS_ORDER:
            if field not in fields_to_ask:
                continue

            current = extracted.get(field)
            v = _prompt_value(tag, field, current)
            if v is None:
                continue

            user_inputs.setdefault(tag, {})
            user_inputs[tag][field] = v
            
        # ===== ДОБАВЛЕНО: вопрос про освещение в шкафу =====
    print("\n=== Освещение ===\n")
    s = input("Освещение заведено в шкаф (ШО/ЩО/ШНО/ЩНО)? [y/N]: ").strip().lower()
    lighting_in_cabinet = s in ("y", "yes", "да", "д")

    user_inputs.setdefault("_meta", {})
    user_inputs["_meta"]["lighting_in_cabinet"] = lighting_in_cabinet

        # ===== ДОБАВЛЕНО: электрообогрев (метраж * 16 Вт/м) =====
    print("\n=== Электрообогрев ===\n")
    s = input("Нужен электрообогрев (дренажи/крыша/желоба/Т96 и т.п.)? [y/N]: ").strip().lower()
    need_heating = s in ("y", "yes", "да", "д")

    user_inputs.setdefault("_meta", {})
    user_inputs["_meta"]["heating_needed"] = need_heating

    if need_heating:
        heating = {"linear_w_per_m": 16.0, "items": [], "roof": None}

        print("\nВвод по точкам обогрева (метры кабеля).")
        print("Формат: <название>;<метры>. Пример: дренаж;10")
        print("КРЫШУ сюда не вводи — расчет крыши будет отдельным вопросом ниже.")
        print("Когда закончишь — просто Enter.\n")

        while True:
            line = input("> ").strip()
            if not line:
                break
            if ";" not in line:
                print("Нужно в формате: название;метры (например: дренаж;10)")
                continue
            name, m_str = [x.strip() for x in line.split(";", 1)]
            try:
                meters = float(m_str.replace(",", "."))
            except Exception:
                print("Метры должны быть числом.")
                continue
            if meters <= 0:
                print("Метры должны быть > 0.")
                continue
            heating["items"].append({"name": name, "meters": meters})

        print("\nЭлектрообогрев крыши (если есть).")
        s2 = input("Нужен обогрев крыши? [y/N]: ").strip().lower()
        if s2 in ("y", "yes", "да", "д"):
            l_str = input("Длина одной стороны крыши (м): ").strip()
            try:
                roof_len = float(l_str.replace(",", "."))
            except Exception:
                roof_len = None
            if roof_len and roof_len > 0:
                # по твоему алгоритму: L_total = L*4 с одной стороны и L*4 с другой => 8*L
                heating["roof"] = {"roof_len_m": roof_len, "multiplier": 8.0}

        user_inputs["_meta"]["heating"] = heating
    
    print("\n=== Шкафы ===\n")
    print("Ввод: <тип шкафа>;<кВт>. Пример: ШУТ;0.5")
    print("Когда закончишь — просто Enter.\n")

    cabs = []
    while True:
        line = input("> ").strip()
        if not line:
            break
        if ";" not in line:
            print("Нужно в формате: тип;кВт (например: ШУТ;0.5)")
            continue
        tag, p_str = [x.strip() for x in line.split(";", 1)]
        try:
            p_kw = float(p_str.replace(",", "."))
        except Exception:
            print("кВт должны быть числом.")
            continue
        if p_kw <= 0:
            print("кВт должны быть > 0.")
            continue
        cabs.append({"tag": tag, "p_kw": p_kw})

    user_inputs.setdefault("_meta", {})
    user_inputs["_meta"]["cabinets"] = cabs
    
    _save_json(out_path, user_inputs)

    print("\nСохранено:", out_path)
    if not user_inputs:
        print("user_inputs.json пустой — значит все поля уже были или ты всё пропустил.")


if __name__ == "__main__":
    main()
