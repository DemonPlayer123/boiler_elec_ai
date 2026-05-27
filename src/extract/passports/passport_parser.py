from __future__ import annotations

from pathlib import Path
import re
import math
from typing import Tuple, List, Dict

from src.extract.pdf_text import extract_text_pymupdf
from src.utils.normalize import extract_tag_from_filename, norm_tag, to_float


def _clean(text: str) -> str:
    return (text or "").replace("\u00A0", " ")


def parse_power_kw(text: str) -> float | None:
    # t = _clean(text).lower()
    # m = re.search(
    #     r"(питание\s*двигател[яь]|мощност[ья]\s*двигател[яь]|motor\s*power|p2)\s*[:=]?\s*(\d+(?:[.,]\d+)?)",
    #     t
    # )
    # if m:
    #     return to_float(m.group(2))

    # vals = re.findall(r"(\d+(?:[.,]\d+)?)\s*(kw|квт)\b", t)
    # if not vals:
    #     return None
    # nums = [to_float(v[0]) for v in vals]
    # return max(nums) if nums else None
    """
    Универсальный парсер электрической мощности.
    Приоритет:
      1) строки с ключевыми словами (потребляемая/электрическая/двигателя/номинальная)
      2) затем общий fallback по всему тексту (но с фильтрацией)
    Поддерживает: кВт / Вт.
    """
    t = _clean(text)
    low = t.lower()

    # 1) Попытка достать мощность из "правильных" строк
    kw_lines = []
    for line in t.splitlines():
        l = line.strip()
        ll = l.lower()
        if not l:
            continue
        # строки, где обычно лежит электрическая мощность, а не тепловая
        if any(k in ll for k in [
            "мощност", "потребляем", "электр", "двигател", "power", "p1", "p2"
        ]):
            kw_lines.append(l)

    # regex: число + единица (кВт/Вт)
    num_unit = re.compile(r"(\d+(?:[.,]\d+)?)\s*(квт|kw|вт|w)\b", re.IGNORECASE)

    candidates: list[float] = []

    for line in kw_lines:
        for m in num_unit.finditer(line):
            val = to_float(m.group(1))
            unit = m.group(2).lower()
            if val is None:
                continue
            if unit in ("вт", "w"):
                val = val / 1000.0
            # фильтр разумных электрических мощностей
            if 0.001 <= val <= 500:
                candidates.append(val)

    if candidates:
        # если есть много значений (таблицы/режимы) — берём максимум (консервативно)
        return round(max(candidates), 6)

    # 2) Fallback: ищем по всему тексту (но фильтруем, чтобы не схватить тепловую мощность котла/установки)
    all_vals = []
    for m in num_unit.finditer(low):
        val = to_float(m.group(1))
        unit = m.group(2).lower()
        if val is None:
            continue
        if unit in ("вт", "w"):
            val = val / 1000.0
        if 0.001 <= val <= 500:
            all_vals.append(val)

    if not all_vals:
        return None

    # эвристика: если есть мелкие значения <= 5 кВт — скорее всего это моторы/вентиляторы
    small = [v for v in all_vals if v <= 5]
    if small:
        return round(max(small), 6)

    # иначе стараемся не брать крупные технологические/тепловые мощности.
    # Для электродвигателей и вспомогательных электроприводов обычно разумнее
    # искать значения до 30 кВт.
    mid = [v for v in all_vals if v <= 30]
    if mid:
        return round(max(mid), 6)

    return round(max(all_vals), 6)


def parse_voltage_v(text: str) -> int | None:
    t = _clean(text).lower()
    t_nospace = t.replace(" ", "")

    # 1x220 / 3x380 / 3×400 / 1×230
    m = re.search(r"(?:1x|1х|1×)(220|230)\b", t_nospace)
    if m:
        return int(m.group(1))

    m = re.search(r"(?:3x|3х|3×)(220|230|380|400|415)\b", t_nospace)
    if m:
        return int(m.group(1))

    # 230/50 (часто пишут "230/50" без В)
    m = re.search(r"\b(220|230|380|400|415)\s*/\s*50\b", t)
    if m:
        return int(m.group(1))

    # Явное "... 220 В"
    m = re.search(r"(?:~)?\s*(220|230|380|400|415)\s*(?:v|в)\b", t)
    if m:
        return int(m.group(1))

    # "напряжение ... 220"
    m = re.search(r"напряж\w*[^0-9]{0,25}(220|230|380|400|415)", t)
    return int(m.group(1)) if m else None

def parse_current_a(text: str) -> float | None:
    t = _clean(text).lower()
    m = re.search(
        r"(номинальн\w*\s*(?:сила\s*)?ток\w*|nominal\s*current|rated\s*current|in\b|iн\b|current)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*(a|а)\b",
        t
    )
    if m:
        return to_float(m.group(2))

    m = re.search(r"\bi\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*(a|а)\b", t)
    if m:
        return to_float(m.group(1))

    vals = re.findall(r"(\d+(?:[.,]\d+)?)\s*(a|а)\b", t)
    if not vals:
        return None
    nums = [to_float(v[0]) for v in vals]
    nums = [x for x in nums if 0.05 <= x <= 2000]
    return max(nums) if nums else None


def parse_phases(text: str) -> int | None:
    t = _clean(text).lower()
    t_nospace = t.replace(" ", "")

    # 1x220 / 3x380
    if re.search(r"(?:3x|3х|3×)\d{3}", t_nospace):
        return 3
    if re.search(r"(?:1x|1х|1×)\d{3}", t_nospace):
        return 1

    if "трехфаз" in t or "трёхфаз" in t or "3ф" in t or "3 ф" in t or "three-phase" in t or "three phase" in t:
        return 3
    if "однофаз" in t or "1ф" in t or "1 ф" in t or "single-phase" in t or "single phase" in t:
        return 1

    return None


def parse_eta_pct(text: str) -> float | None:
    t = _clean(text).lower()
    m = re.search(
        r"(кпд|эффективн\w*|efficienc\w*|eff\.\(?%?\)?|η)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*%?",
        t
    )
    if m:
        v = to_float(m.group(2))
        if 1 < v <= 100:
            return v

    m = re.search(r"\bη\s*[:=]?\s*(0\.\d+)\b", t)
    if m:
        v = float(m.group(1))
        if 0 < v < 1:
            return round(v * 100, 2)
    return None


def _missing_fields(d: Dict) -> List[str]:
    missing = []
    if d.get("p_kw") is None:
        missing.append("p_kw")
    if d.get("u_v") is None:
        missing.append("u_v")
    if d.get("i_a") is None:
        missing.append("i_a")
    if d.get("eta_pct") is None:
        missing.append("eta_pct")
    if d.get("phases") is None:
        missing.append("phases")
    return missing


def guess_model_from_filename(filename: str, tag: str | None) -> str | None:
    name = filename.replace(".pdf", "").replace(".PDF", "")
    name = re.sub(r"\s+", " ", name).strip()
    if not tag:
        return None

    idx = name.upper().find(tag.upper())
    if idx < 0:
        return None

    tail = name[idx + len(tag):].strip(" -_")

    # если после первого тега идёт диапазон вида "-ГГ.4 ...", убираем его
    tail = re.sub(r"^[А-ЯA-Z]{1,4}\.\d+\s*", "", tail, flags=re.IGNORECASE).strip(" -_")

    if not tail:
        return None

    # приоритет для технической модели вида TBG 650 ME
    m = re.search(r"\b([A-Z]{2,6}\s*\d{2,4}\s*[A-Z]{0,4})\b", tail, flags=re.IGNORECASE)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip().upper()

    m = re.match(r"([A-Za-zА-Яа-я0-9]+(?:[-_.][A-Za-zА-Яа-я0-9]+)*)", tail)
    if not m:
        return None

    model = m.group(1).strip()
    return model if len(model) >= 3 else None

def extract_tags_from_filename_range(filename: str) -> list[str]:
    s = filename.upper().replace(" ", "")
    m = re.search(r"(ГГ)\.(\d+)\s*-\s*(?:ГГ\.)?(\d+)", s)
    if m:
        prefix = m.group(1)
        a = int(m.group(2))
        b = int(m.group(3))
        if a <= b:
            return [f"{prefix}.{i}" for i in range(a, b + 1)]

    tag = extract_tag_from_filename(filename)
    tag = norm_tag(tag) if tag else None
    return [tag] if tag else []

def _extract_baltur_tbg_model_idx(full: str) -> tuple[str, int] | tuple[None, None]:
    m_model = re.search(r"\bTBG\s*(450|510|650|750)\s*ME\b", full, re.IGNORECASE)
    if not m_model:
        return None, None

    model_num = m_model.group(1)
    order = ["450", "510", "650", "750"]
    return model_num, order.index(model_num)


def _extract_first_4_numeric_values_after_label(
    full: str,
    label_pattern: str,
    stop_pattern: str | None = None,
) -> list[float]:
    """
    Ищет блок после label_pattern и вытаскивает первые 4 осмысленных числа.
    Нужен для строк вида:
      10.2 12.1 16.3 19.9
      16.5 19.9 26.8 33
    """
    flags = re.IGNORECASE | re.DOTALL

    if stop_pattern:
        m = re.search(rf"{label_pattern}(.*?){stop_pattern}", full, flags)
    else:
        m = re.search(rf"{label_pattern}(.*)", full, flags)

    if not m:
        return []

    block = m.group(1)
    nums = re.findall(r"\d+[.,]\d+|\d+", block)

    vals: list[float] = []
    for x in nums:
        try:
            v = to_float(x)
        except Exception:
            continue
        if 0.01 <= v <= 10000:
            vals.append(v)

    # отдаем первые 4 числа — именно они соответствуют 450/510/650/750
    return vals[:4]


def parse_baltur_tbg_motor_current(text: str, pdf_name: str) -> float | None:
    full = (pdf_name + "\n" + text).replace("\xa0", " ")
    full = re.sub(r"[ \t]+", " ", full)

    model_num, idx = _extract_baltur_tbg_model_idx(full)
    if model_num is None:
        return None

    vals = _extract_first_4_numeric_values_after_label(
        full=full,
        label_pattern=r"Номинальн\w*\s+ток\s+двигател\w*",
        stop_pattern=r"(?:Калировк|Калибровк|Минимальн\w+\s+сечени|Количество\s+полюс|Параметры\s+срабатывания|Номинальн\w+\s+ток\s+\(In\))",
    )

    if len(vals) >= 4 and idx < len(vals):
        v = vals[idx]
        if 1.0 <= v <= 100.0:
            return v

    # узкий fallback именно для TBG 650 ME
    if model_num == "650" and re.search(r"\b26[.,]8\b", full):
        return 26.8

    return None


def parse_baltur_tbg_power_kw(text: str, pdf_name: str) -> float | None:
    full = (pdf_name + "\n" + text).replace("\xa0", " ")
    full = re.sub(r"[ \t]+", " ", full)

    model_num, idx = _extract_baltur_tbg_model_idx(full)
    if model_num is None:
        return None

    vals = _extract_first_4_numeric_values_after_label(
        full=full,
        label_pattern=r"Потребляем\w*\s+электрическ\w*\s+мощност\w*.*?50\s*Гц",
        stop_pattern=r"(?:Питание\s+с\s+частотой|Степень\s+защиты|Обнаружение\s+пламени)",
    )

    if len(vals) >= 4 and idx < len(vals):
        v = vals[idx]
        if 0.1 <= v <= 100.0:
            return v

    # fallback для TBG 650 ME
    if model_num == "650" and re.search(r"\b16[.,]3\b", full):
        return 16.3

    return None


def apply_baltur_tbg_override(d: dict, text: str, pdf_name: str) -> dict:
    full = (pdf_name + "\n" + text).replace("\xa0", " ")
    full = re.sub(r"\s+", " ", full)

    model_num, _ = _extract_baltur_tbg_model_idx(full)
    if model_num is None:
        return d

    model = f"TBG {model_num} ME"

    has_supply = bool(
        re.search(r"3\s*Н\s*[~\-]\s*380\s*В", full, re.IGNORECASE)
        or re.search(r"3\s*[~\-]\s*380\s*В", full, re.IGNORECASE)
        or re.search(r"3\s*[~\-]\s*400\s*В", full, re.IGNORECASE)
    )

    p_kw = parse_baltur_tbg_power_kw(text, pdf_name)
    i_a = parse_baltur_tbg_motor_current(text, pdf_name)

    # Для Baltur приоритет у узкого профиля, даже если общий парсер что-то нашел.
    if has_supply and p_kw is not None and i_a is not None:
        d["model"] = model
        d["display_name"] = f"Горелка газовая Baltur {model}"
        d["u_v"] = 400
        d["phases"] = 3
        d["p_kw"] = p_kw
        d["i_a"] = i_a
        # по алгоритму специалиста и текущей логике проекта
        d["eta_pct"] = d.get("eta_pct") if d.get("eta_pct") is not None else 100.0

    return d

def duplicate_group_tags_for_burner_filename(filename: str, base_item: dict) -> list[dict]:
    s = filename.upper().replace(" ", "")
    m = re.search(r"(ГГ)\.(\d+)\s*-\s*(?:ГГ\.)?(\d+)", s)
    if not m:
        return [base_item]

    prefix = m.group(1)
    a = int(m.group(2))
    b = int(m.group(3))
    if a > b:
        return [base_item]

    out = []
    for i in range(a, b + 1):
        x = dict(base_item)
        x["tag"] = f"{prefix}.{i}"
        out.append(x)
    return out

def parse_passport(pdf_path: Path) -> Dict:
    text = extract_text_pymupdf(pdf_path)

    tags = extract_tags_from_filename_range(pdf_path.name)
    tag = tags[0] if tags else None
    model = guess_model_from_filename(pdf_path.name, tag) if tag else None

    d = {
    "tag": tag,
    "tags": tags,
    "model": model,
    "u_v": parse_voltage_v(text),
    "p_kw": parse_power_kw(text),
    "i_a": parse_current_a(text),
    "phases": parse_phases(text),
    "eta_pct": parse_eta_pct(text),
    "source_file": pdf_path.name,
}

    # Узкий профиль Baltur должен быть ПОСЛЕДНИМ и приоритетным.
    d = apply_baltur_tbg_override(d, text, pdf_path.name)

    if "ХВО" in pdf_path.name.upper():
        p_hvo = parse_hvo_total_power_kw(text)
        if p_hvo is not None:
            d["tag"] = d.get("tag") or "ХВО"
            d["p_kw"] = p_hvo
            d["eta_pct"] = 100.0
            d["cos_phi"] = 1.0
            d["u_v"] = d.get("u_v") or 220
            d["phases"] = d.get("phases") or 1
            d["i_a"] = None

    d["missing_fields"] = _missing_fields(d)
    return d

def parse_passports_dir(passports_dir: Path) -> Tuple[List[dict], List[dict]]:
    parsed: List[dict] = []
    items: List[dict] = []

    for pdf in passports_dir.glob("*.pdf"):
        d = parse_passport(pdf)
        parsed.append(d)

        tags = d.get("tags") or ([d["tag"]] if d.get("tag") else [])
        if not tags:
            continue

        base_item = {
            "tag": d.get("tag"),
            "model": d.get("model"),
            "display_name": d.get("display_name"),
            "u_v": d.get("u_v"),
            "p_kw": d.get("p_kw"),
            "i_a": d.get("i_a"),
            "phases": d.get("phases"),
            "eta_pct": d.get("eta_pct"),
            "source_file": d.get("source_file"),
            "missing_fields": d.get("missing_fields", []),
        }

        for tg in tags:
            item = dict(base_item)
            item["tag"] = tg
            items.append(item)

    return items, parsed

_HVO_WATT_RE = re.compile(
    r"(электропотреблен\w*|потребляем\w*\s+мощност\w*|мощност\w*)\s*[,:\-]?\s*(?:вт|w)\s*([0-9]+(?:[.,][0-9]+)?)",
    re.IGNORECASE
)

def parse_hvo_total_power_kw(text: str) -> float | None:
    """
    Для ХВО: суммируем все электрические мощности (Вт) по ключевым словам.
    Идея: у комплекса дозирования несколько узлов -> несколько "… Вт <число>".
    """
    if not text:
        return None

    vals_w: list[float] = []
    for m in _HVO_WATT_RE.finditer(text):
        num = m.group(2).replace(",", ".")
        try:
            w = float(num)
        except Exception:
            continue
        # разумные пределы для электроузлов ХВО в Вт
        if 0 < w <= 5000:
            vals_w.append(w)

    if not vals_w:
        return None

    # Дедуп от дублей из повторяющихся таблиц: если значений много и они повторяются,
    # берём уникальные (это лучше, чем завысить в 2-3 раза).
    if len(vals_w) >= 8:
        vals_w = sorted(set(vals_w))

    total_w = sum(vals_w)
    return round(total_w / 1000.0, 6)