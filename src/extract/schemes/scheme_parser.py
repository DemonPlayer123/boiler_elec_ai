from __future__ import annotations

from pathlib import Path
import re
from typing import List, Dict, Optional, Tuple, Any

from src.extract.schemes.pdf_layout import extract_lines_by_blocks
from src.utils.normalize import norm_tag


# Пример: "К5.1-К5.3" / "K5.1–K5.3"
RANGE_RE = re.compile(r"(?i)\b([КK]\s*\d{1,3})\s*\.\s*(\d{1,2})\s*[-–—]\s*\1\s*\.\s*(\d{1,2})\b")

BURNER_RANGE_RE = re.compile(
    r"(?i)\b(ГГ)\.?\s*(\d{1,3})\s*[-–—]\s*\1\.?\s*(\d{1,3})\b"
)
# Одиночные позиции: К9, К6.2, ГГ.1
POS_RE = re.compile(r"(?i)\b([КK]\s*\d{1,3}(?:\.\d{1,2})?|ГГ\.?\s*\d{1,3})\b")

_EO_GROUP_RE = re.compile(
    r"\bГр\.\s*([123])\s*[-–—]\s*([0-9]+(?:[.,][0-9]+)?)\s*[-–—]\s*([0-9]+(?:[.,][0-9]+)?)\s*[-–—]\s*([0-9]+(?:[.,][0-9]+)?)\b",
    re.IGNORECASE
)

# ============================================================
# ДОБАВЛЕНО: ОВ-теги (А/П/В) — диапазоны и одиночные позиции
# ============================================================
# Примеры: "А1-А4", "П1–П4", "В1"
# Важно: НЕ ловим двухбуквенные позиции типа "ПЕ1", "ВЕ2" (жалюзи/дефлектор) —
# поэтому regex строго "одна буква + цифры".
OV_RANGE_RE = re.compile(r"\b([АAПPВB])\s*(\d{1,3})\s*[-–—]\s*\1\s*(\d{1,3})\b")
OV_POS_RE = re.compile(r"\b([АAПPВB])\s*(\d{1,3})\b")

def _norm_ov_tag(prefix: str, num: int) -> str:
    """
    Нормализация ОВ-тегов:
    A->А, P->П, B->В (на случай, если в PDF латиница).
    """
    p = (prefix or "").upper()
    p = p.replace("A", "А").replace("P", "П").replace("B", "В")
    return f"{p}{int(num)}"

VFD_RE = re.compile(r"(?i)\b(чрп|пч|vfd|inverter|преобразовател)\b")

# Классификация
CLS_RULES = [
    ("burner", re.compile(r"(?i)\bгорелк|burner\b")),
    ("pump", re.compile(r"(?i)\bнасос\b")),
    ("hvac", re.compile(r"(?i)\bвоздушно|отопительн|агрегат|вентилятор|дефлектор|решетк")),
    ("cabinet", re.compile(r"(?i)\bшкаф|щу\b|шау\b|шук\b")),
]


# Разбор примечаний по ролям (важно: “сухой резерв” отдельно)
DRY_RE_1 = re.compile(r"(?i)(\d+)\s*[-–—]\s*(?:сух(?:ой)?\s*)?резерв")
DRY_RE_2 = re.compile(r"(?i)(\d+)\s*сух(?:ой)?\s*резерв")
RES_RE_1 = re.compile(r"(?i)(\d+)\s*[-–—]\s*резерв")
RES_RE_2 = re.compile(r"(?i)(\d+)\s*резервн")
WORK_RE_1 = re.compile(r"(?i)(\d+)\s*[-–—]\s*раб")
WORK_RE_2 = re.compile(r"(?i)(\d+)\s*рабоч")

# Частый случай: "2-рабочих, 1 резервный"
PAIR_RE = re.compile(r"(?i)(\d+)\s*[-–—]?\s*рабоч\w*\s*[,; ]+\s*(\d+)\s*[-–—]?\s*резерв\w*")
# Частый случай: "4-рабочих, 1-сухой резерв"
PAIR_DRY_RE = re.compile(r"(?i)(\d+)\s*[-–—]?\s*рабоч\w*\s*[,; ]+\s*(\d+)\s*[-–—]?\s*(?:сух(?:ой)?\s*)?резерв")


def guess_class(line: str) -> Optional[str]:
    for cls, rx in CLS_RULES:
        if rx.search(line):
            return cls
    return None


def _expand_ranges(line: str) -> List[str]:
    """
    Разворачивает диапазоны К5.1-К5.3.
    Возвращает список тегов (нормализованных).
    """
    tags: List[str] = []

    # 1) диапазоны
    for m in RANGE_RE.finditer(line):
        base = m.group(1)
        a = int(m.group(2))
        b = int(m.group(3))
        base = norm_tag(base)
        if not base:
            continue
        if a > b:
            a, b = b, a
        for i in range(a, b + 1):
            tags.append(norm_tag(f"{base}.{i}"))
            
    # диапазоны горелок: ГГ.1-ГГ.4
    for m in BURNER_RANGE_RE.finditer(line):
        a = int(m.group(2))
        b = int(m.group(3))
        if a > b:
            a, b = b, a
        for i in range(a, b + 1):
            tags.append(f"ГГ.{i}")

    # 2) одиночные позиции (которые не попали в диапазон)
    for raw in POS_RE.findall(line):
        t = norm_tag(raw)
        if t:
            tags.append(t)

    # 3) ОВ диапазоны: А1-А4, П1-П4
    for m in OV_RANGE_RE.finditer(line):
        pref = m.group(1)
        a = int(m.group(2))
        b = int(m.group(3))
        if a > b:
            a, b = b, a
        for i in range(a, b + 1):
            tags.append(_norm_ov_tag(pref, i))

    # 4) ОВ одиночные: В1, А2, П3 (если не попало в диапазон)
    for m in OV_POS_RE.finditer(line):
        pref = m.group(1)
        num = int(m.group(2))
        tags.append(_norm_ov_tag(pref, num))
    
    # уникализируем с сохранением порядка
    out: List[str] = []
    seen = set()
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _split_name_and_note(line: str) -> Tuple[str, str]:
    """
    Упрощённо: отделяем примечание после точки с запятой/после 'Примечание' если было.
    В PDF строки часто приходят “склеенными”, поэтому делаем максимально мягко.
    """
    s = re.sub(r"\s+", " ", line).strip()
    # пробуем отделить после ';'
    if ";" in s:
        left, right = s.split(";", 1)
        return left.strip(), right.strip()
    return s, ""


def _parse_duty_counts(note: str, total: int) -> Tuple[int, int, int]:
    """
    Возвращает (work_cnt, reserve_cnt, dry_cnt)
    """
    if not note:
        return (0, 0, 0)

    n = note

    # Сначала пары “раб + сухой резерв”
    m = PAIR_DRY_RE.search(n)
    if m:
        w = int(m.group(1))
        d = int(m.group(2))
        r = max(0, total - w - d)
        return (w, r, d)

    # Потом пары “раб + резерв”
    m = PAIR_RE.search(n)
    if m:
        w = int(m.group(1))
        r = int(m.group(2))
        d = 0
        return (w, r, d)

    # Иначе по отдельным паттернам
    dry = 0
    for rx in (DRY_RE_1, DRY_RE_2):
        mm = rx.search(n)
        if mm:
            dry = int(mm.group(1))
            break

    reserve = 0
    for rx in (RES_RE_1, RES_RE_2):
        mm = rx.search(n)
        if mm:
            reserve = int(mm.group(1))
            break

    work = 0
    for rx in (WORK_RE_1, WORK_RE_2):
        mm = rx.search(n)
        if mm:
            work = int(mm.group(1))
            break

    # Доводим до total, если частично указано
    specified = work + reserve + dry
    if total > 0:
        if specified == 0:
            # ничего не сказано
            return (0, 0, 0)

        if specified < total:
            # чаще всего не указали work
            if work == 0:
                work = total - reserve - dry
            else:
                # если work уже есть, недостающее запишем в work
                work = min(total, work + (total - specified))
        elif specified > total:
            # на всякий случай подрежем
            # приоритет: work, потом reserve, потом dry
            over = specified - total
            while over > 0 and work > 0:
                work -= 1
                over -= 1
            while over > 0 and reserve > 0:
                reserve -= 1
                over -= 1
            while over > 0 and dry > 0:
                dry -= 1
                over -= 1

    return (work, reserve, dry)


def _assign_duties(tags: List[str], note: str) -> Dict[str, Dict]:
    """
    На вход: список тегов (уже развернутых) и примечание.
    На выход: dict[tag] -> { duty, dry_reserve }
    """
    total = len(tags)
    work_cnt, reserve_cnt, dry_cnt = _parse_duty_counts(note, total)

    meta: Dict[str, Dict] = {}
    if total == 0:
        return meta

    # Если нет явного распределения — ничего не назначаем
    if work_cnt == reserve_cnt == dry_cnt == 0:
        for t in tags:
            meta[t] = {"duty": None, "dry_reserve": False}
        return meta

    # Порядок: сначала рабочие, потом резервные, потом сухой резерв
    # (ровно как у тебя в таблице К4.1–К4.5)
    idx = 0
    for _ in range(work_cnt):
        if idx >= total:
            break
        meta[tags[idx]] = {"duty": "work", "dry_reserve": False}
        idx += 1

    for _ in range(reserve_cnt):
        if idx >= total:
            break
        meta[tags[idx]] = {"duty": "reserve", "dry_reserve": False}
        idx += 1

    for _ in range(dry_cnt):
        if idx >= total:
            break
        meta[tags[idx]] = {"duty": "reserve", "dry_reserve": True}
        idx += 1

    # Остаток (если есть) — без роли
    while idx < total:
        meta[tags[idx]] = {"duty": None, "dry_reserve": False}
        idx += 1

    return meta

TAG_FAMILY_HINTS = {
    "burner": {
        "tag_prefixes": ["гг"],
        "positive_keywords": ["горелк", "газов", "baltur", "tbg"],
        "negative_keywords": ["кран", "шаровой", "кш", "клапан", "задвижка", "грязевик", "фильтр"],
    },
    "pump": {
        "tag_prefixes": ["к"],
        "positive_keywords": ["насос", "cdm", "td", "chl", "llts", "nis"],
        "negative_keywords": ["кран", "шаровой", "кш", "клапан", "задвижка"],
    },
    "fan": {
        "tag_prefixes": ["п", "в"],
        "positive_keywords": ["вентилятор", "осевой", "канальный", "vo", "вс-"],
        "negative_keywords": ["кран", "шаровой", "клапан", "насос"],
    },
    "cabinet": {
        "tag_prefixes": ["ш", "щу", "щ", "вру"],
        "positive_keywords": ["шкаф", "щит", "вру", "шу", "щит управления"],
        "negative_keywords": ["кран", "насос", "горелк", "вентилятор"],
    },
}

def _norm_text(value: str) -> str:
    return str(value or "").strip().lower().replace("ё", "е")


def _extract_tag_prefix(tag: str) -> str:
    tag = _norm_text(tag)
    out = []
    for ch in tag:
        if ch.isalpha():
            out.append(ch)
        else:
            break
    return "".join(out)


def _expected_family_by_tag(tag: str) -> str | None:
    prefix = _extract_tag_prefix(tag)
    for family, cfg in TAG_FAMILY_HINTS.items():
        if prefix in cfg["tag_prefixes"]:
            return family
    return None

def _is_bad_name_for_family(tag: str, base_name: str) -> bool:
    family = _expected_family_by_tag(tag)
    text = _norm_text(base_name)

    if not family or not text:
        return False

    cfg = TAG_FAMILY_HINTS[family]

    pos_hit = any(kw in text for kw in cfg["positive_keywords"])
    neg_hit = any(kw in text for kw in cfg["negative_keywords"])

    # если есть явные негативные признаки и нет позитивных — имя плохое
    if neg_hit and not pos_hit:
        return True

    # для burner/pump/fan/cabinet требуем хотя бы слабую смысловую совместимость
    if family in {"burner", "pump", "fan", "cabinet"} and not pos_hit and neg_hit:
        return True

    return False

def _score_name_candidate_for_tag(tag: str, candidate_text: str, detected_class: str | None = None) -> float:
    text = _norm_text(candidate_text)
    if not text:
        return -999.0

    score = 0.0
    family = _expected_family_by_tag(tag)

    if family:
        cfg = TAG_FAMILY_HINTS[family]

        for kw in cfg["positive_keywords"]:
            if kw in text:
                score += 3.0

        for kw in cfg["negative_keywords"]:
            if kw in text:
                score -= 5.0

    if detected_class:
        detected_class = _norm_text(detected_class)
        if family and detected_class == family:
            score += 2.0

    if len(text) < 5:
        score -= 2.0

    generic_bad = ["кран", "шаровой", "клапан", "задвижка", "трубопровод", "ду", "dn"]
    if family in {"burner", "pump", "fan", "cabinet"}:
        for bad in generic_bad:
            if bad in text:
                score -= 2.0

    return score


def _split_name_candidates(text: str) -> List[str]:
    """
    Режем очищенную строку на смысловые кандидаты имени.
    Это нужно, когда в одной строке после OCR/парсинга склеились
    несколько сущностей подряд.
    """
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if not s:
        return []

    candidates: List[str] = [s]

    # Разбиваем по ; и по крупным смысловым разделителям
    parts = re.split(r"\s*;\s*|\s{2,}", s)
    for p in parts:
        p = p.strip(" ,;-")
        if p:
            candidates.append(p)

    # Дополнительно делаем кандидаты от "позитивных" слов
    lowered = _norm_text(s)
    all_positive = set()
    for cfg in TAG_FAMILY_HINTS.values():
        for kw in cfg["positive_keywords"]:
            all_positive.add(kw)

    for kw in all_positive:
        idx = lowered.find(kw)
        if idx >= 0:
            frag = s[idx:].strip(" ,;-")
            if frag:
                candidates.append(frag)

    # Уникализируем с сохранением порядка
    out: List[str] = []
    seen = set()
    for c in candidates:
        key = _norm_text(c)
        if key and key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _select_base_name_for_tag(tag: str, cleaned_left: str, detected_class: str | None = None) -> str:
    """
    Выбирает наиболее вероятное имя оборудования для конкретного тега
    из уже очищенной строки.
    """
    candidates = _split_name_candidates(cleaned_left)
    if not candidates:
        return ""

    best_name = candidates[0]
    best_score = -9999.0

    for cand in candidates:
        cand_score = _score_name_candidate_for_tag(
            tag=tag,
            candidate_text=cand,
            detected_class=detected_class,
        )
        if cand_score > best_score:
            best_score = cand_score
            best_name = cand

    return re.sub(r"\s+", " ", best_name).strip(" ,;-")

def parse_schemes_to_registry(schemes_dir: Path) -> List[Dict]:
    """
    Делает registry из схем (ТМ/ОВ/ГСВ).
    Главная цель для нас сейчас — корректно собрать насосы по ТМ, включая:
    - диапазоны Кx.1-Кx.n
    - роли (раб/рез/сухой резерв)
    """
    dedup: dict[str, Dict] = {}

    for pdf in schemes_dir.glob("*.pdf"):
        lines = extract_lines_by_blocks(pdf)
        last_good_name_by_family: Dict[str, str] = {}

        for line in lines:
            if not line or len(line) < 3:
                continue
            tags = _expand_ranges(line)
            if not tags:
                continue

            cls = guess_class(line)

            # если класс не распознан, но в строке есть горелочные теги — считаем это burner
            if cls is None and any(t.startswith("ГГ.") for t in tags):
                cls = "burner"

            if cls is None:
                continue

            left, note = _split_name_and_note(line)

            # cleaned_left: убираем ВСЕ теги/диапазоны из левой части строки,
            # чтобы потом выбрать имя отдельно для каждого тега
            cleaned_left = left
            cleaned_left = RANGE_RE.sub("", cleaned_left)
            cleaned_left = BURNER_RANGE_RE.sub("", cleaned_left)
            cleaned_left = POS_RE.sub("", cleaned_left)
            cleaned_left = OV_RANGE_RE.sub("", cleaned_left)
            cleaned_left = OV_POS_RE.sub("", cleaned_left)
            cleaned_left = re.sub(r"\s+", " ", cleaned_left).strip(" ;,-")

            has_vfd = bool(VFD_RE.search(line))

            duties = _assign_duties(tags, note)

            for tg in tags:
                dmeta = duties.get(tg, {"duty": None, "dry_reserve": False})
                family = _expected_family_by_tag(tg)

                base_name = _select_base_name_for_tag(
                    tag=tg,
                    cleaned_left=cleaned_left,
                    detected_class=cls,
                )

                # 1) локальная страховка: не даём burner-семейству брать арматуру,
                # если в текущей строке есть явный горелочный фрагмент
                if family == "burner" and _is_bad_name_for_family(tg, base_name):
                    alts = _split_name_candidates(cleaned_left)
                    burner_alts = [
                        a for a in alts
                        if any(k in _norm_text(a) for k in ["горелк", "газов", "baltur", "tbg"])
                    ]
                    if burner_alts:
                        base_name = burner_alts[0]

                # 2) контекстный fallback:
                # если текущее имя плохое, а раньше уже встречалось хорошее имя того же семейства,
                # используем его
                if family and _is_bad_name_for_family(tg, base_name):
                    prev_good = last_good_name_by_family.get(family)
                    if prev_good:
                        base_name = prev_good

                # 3) если имя хорошее — запоминаем как последнее валидное имя семейства
                if family and base_name and not _is_bad_name_for_family(tg, base_name):
                    last_good_name_by_family[family] = base_name

                rec = {
                    "tag": tg,
                    "base_name": base_name,
                    "equip_class": cls,
                    "has_vfd": has_vfd,
                    "duty": dmeta["duty"],
                    "dry_reserve": bool(dmeta["dry_reserve"]),
                    "source_file": pdf.name,
                    "note": note,
                }



                # дедуп: выбираем запись с более “богатой” информацией
                if tg not in dedup:
                    dedup[tg] = rec
                else:
                    cur = dedup[tg]
                    # приоритет: где есть duty/dry_reserve, и где base_name короче
                    score_new = (1 if rec["duty"] else 0) + (2 if rec["dry_reserve"] else 0)
                    score_cur = (1 if cur.get("duty") else 0) + (2 if cur.get("dry_reserve") else 0)
                    if score_new > score_cur:
                        dedup[tg] = rec
                    elif score_new == score_cur and len(rec["base_name"]) < len(cur.get("base_name", "")):
                        dedup[tg] = rec

    return list(dedup.values())


# ============================================================
# ДОБАВЛЕНО: ЭО — парсинг 3 групп освещения (не влияет на TM/ОВ)
# ============================================================

# В ЭО встречается формат вида:
#   QF10Гр.1-0,32-0,95-1,53
#   QF11Гр.2-0,25-1,0-1,14
#   QF46Гр.3-0,04-0,95-0,1
# где: Гр.N — P(кВт) — cosφ — I(A)
EO_GROUP_RE = re.compile(
    r"(?i)\b(?:QF\s*\d+\s*)?Гр\.?\s*([123])\s*[-–—]\s*([0-9]+(?:[.,][0-9]+)?)\s*[-–—]\s*([0-9]+(?:[.,][0-9]+)?)\s*[-–—]\s*([0-9]+(?:[.,][0-9]+)?)"
)

PHASE_RE = re.compile(r"(?i)\b(L1|L2|L3)\b")

LIGHTING_GROUP_NAMES = {
    1: "Рабочее освещение котельного зала",
    2: "Ремонтное освещение (12 В)",
    3: "Рабочее освещение санузла",
}


def _to_float_ru(x: str) -> float:
    return float(x.replace(",", ".").strip())


def parse_lighting_from_eo(schemes_dir: Path) -> List[Dict]:
    """
    ДОБАВЛЕНО: парсит *-ЭО.pdf и возвращает найденные группы освещения (1..3).

    Возвращаемый формат:
      [
        {
          "group": 1,
          "tag": "Гр.1",
          "display_name": "Рабочее освещение котельного зала",
          "p_kw": 0.32,
          "cos_phi": 0.95,
          "i_a": 1.53,
          "phase": "L3",      # если найдётся в строке, иначе None
          "u_v": 220,
          "phases": 1,
          "kpd": 100,
          "ep_kind": "lighting",
          "source_file": "25-05-ЭО.pdf",
        },
        ...
      ]

    ВАЖНО:
    - Не вызывается автоматически.
    - Ничего не меняет в parse_schemes_to_registry.
    """
    # ищем все ЭО в папке
    pdfs = list(schemes_dir.glob("*ЭО*.pdf")) + list(schemes_dir.glob("*-ЭО.pdf")) + list(schemes_dir.glob("*_ЭО.pdf"))
    if not pdfs:
        return []

    found: Dict[int, Dict] = {}

    for pdf in pdfs:
        lines = extract_lines_by_blocks(pdf)

        for line in lines:
            if not line:
                continue
            s = str(line)

            m = EO_GROUP_RE.search(s)
            if not m:
                continue

            g = int(m.group(1))
            p_kw = _to_float_ru(m.group(2))
            cos_phi = _to_float_ru(m.group(3))
            i_a = _to_float_ru(m.group(4))

            phase = None
            pm = PHASE_RE.search(s)
            if pm:
                phase = pm.group(1).upper()

            rec = {
                "group": g,
                "tag": f"Гр.{g}",
                "display_name": LIGHTING_GROUP_NAMES[g],
                "p_kw": p_kw,
                "cos_phi": cos_phi,
                "i_a": i_a,
                "phase": phase,
                "u_v": 220,
                "phases": 1,
                "kpd": 100,
                "ep_kind": "lighting",
                "source_file": pdf.name,
            }

            # дедуп по группе: берём запись с фазой, если она появилась
            if g not in found:
                found[g] = rec
            else:
                if found[g].get("phase") is None and phase is not None:
                    found[g] = rec

    # отдаём только найденные, но в порядке 1..3
    return [found[g] for g in (1, 2, 3) if g in found]

def parse_lighting_from_eo_text(eo_text: str) -> List[Dict[str, Any]]:
    """
    Универсально парсит ЭО по маркировкам вида:
      'Гр.1-0,32-0,95-1,53'  -> group=1, p_kw=0.32, cos=0.95, i=1.53
    Возвращает 3 позиции (если найдены), каждая как item-подобный dict.
    """
    def to_f(s: str) -> float:
        return float(s.replace(",", ".").strip())

    found: Dict[int, Dict[str, Any]] = {}
    for m in _EO_GROUP_RE.finditer(eo_text or ""):
        g = int(m.group(1))
        p_kw = to_f(m.group(2))
        cos = to_f(m.group(3))
        i_a = to_f(m.group(4))
        found[g] = {"group": g, "p_kw": p_kw, "cos_phi": cos, "i_a": i_a}

    out: List[Dict[str, Any]] = []
    for g in (1, 2, 3):
        if g not in found:
            continue
        d = found[g]
        out.append({
            "tag": f"Гр.{g}",
            "ep_kind": "lighting",
            "display_name": {
                1: "Рабочее освещение котельного зала",
                2: "Ремонтное освещение",
                3: "Рабочее освещение санузла",
            }[g],
            "p_kw": d["p_kw"],
            "u_v": 230,          # лучше вынести в конфиг, но дефолт годится
            "phases": 1,
            "cos_phi": d["cos_phi"],
            "eta_pct": 100.0,
            "i_a": d["i_a"],     # можно оставить как контроль/ином
            "source": "eo",
        })
    return out