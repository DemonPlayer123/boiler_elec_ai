from __future__ import annotations

from typing import Optional

from src.engine.phase_sums import compute_loads_range

ANCHOR_COL = "D"


def _find_section_rows(ws, *, end_row: int = 5000) -> list[int]:
    rows = []
    for r in range(1, end_row + 1):
        v = ws[f"{ANCHOR_COL}{r}"].value
        if not v:
            continue
        s = str(v).lower()
        if "секция шин 1" in s or "секция шин 2" in s:
            rows.append(r)
    return rows


def _section_number_from_row_text(ws, r: int) -> int | None:
    s = str(ws[f"{ANCHOR_COL}{r}"].value).lower()
    if "секция шин 1" in s:
        return 11
    if "секция шин 2" in s:
        return 12
    return None


def _include_reserve_from_row_text(ws, r: int) -> bool:
    """
    Определяет, должна ли строка итогов секции учитывать резервные ЭП.
    Важно: строка "без учета резервных ЭП" тоже содержит слово "резерв",
    поэтому простая проверка "резерв in text" некорректна.
    """
    s = str(ws[f"{ANCHOR_COL}{r}"].value or "").strip().lower()

    # Явные маркеры
    if ("без учета" in s or "без учёта" in s) and "резерв" in s:
        return False
    if ("с учетом" in s or "с учётом" in s) and "резерв" in s:
        return True

    # Если встретилась просто фраза "нормальный режим" — это обычно без резерва
    if "нормальный режим" in s:
        return False

    # Дефолт: безопаснее НЕ включать резерв, чем включить лишнее
    return False


def _sumifs(col: str, ls: int, le: int, sec: int, phase: str, *, include_reserve: bool) -> str:
    """
    SUMIFS по col с фильтрами:
      A = секция
      F = фаза (L1/L2/L3 или L1, L2, L3)
    Доп. фильтры по D:
      - "сухой резерв" исключаем всегда
      - если include_reserve=False: исключаем "(рез.)" и строку "Резерв"
    """
    base = (
        f"SUMIFS($%s${ls}:$%s${le},"
        f"$A${ls}:$A${le},{sec},"
        f"$F${ls}:$F${le},\"{phase}\""
    ) % (col, col)

    # всегда исключаем сухой резерв
    crit = f",$D${ls}:$D${le},\"<>*сух*резерв*\""

    if include_reserve:
        return f"={base}{crit})"

    # без учета резервных ЭП: выкидываем "(рез.)" и "Резерв"
    crit2 = f",$D${ls}:$D${le},\"<>*(рез*\""
    crit3 = f",$D${ls}:$D${le},\"<>Резерв*\""
    return f"={base}{crit}{crit2}{crit3})"


def _section_sum_formula(col: str, ls: int, le: int, sec: int, *, include_reserve: bool) -> str:
    sum_3ph = _sumifs(col, ls, le, sec, "L1, L2, L3", include_reserve=include_reserve).lstrip("=")
    s1 = _sumifs(col, ls, le, sec, "L1", include_reserve=include_reserve).lstrip("=")
    s2 = _sumifs(col, ls, le, sec, "L2", include_reserve=include_reserve).lstrip("=")
    s3 = _sumifs(col, ls, le, sec, "L3", include_reserve=include_reserve).lstrip("=")
    return f"=({sum_3ph})+3*MAX(({s1}),({s2}),({s3}))"


def rebuild_busbar_formulas(ws, *, end_row: int = 5000, loads_start: int = 6) -> list[str]:
    log: list[str] = []
    ls, le = compute_loads_range(ws, end_row=end_row, loads_start=loads_start)

    section_rows = _find_section_rows(ws, end_row=end_row)
    if not section_rows:
        log.append("[BUSBAR_WARN] Не найдены строки секций шин.")
        return log

    for r in section_rows:
        sec = _section_number_from_row_text(ws, r)
        if sec not in (11, 12):
            continue
        include_reserve = _include_reserve_from_row_text(ws, r)

        # E/F
        if ws[f"E{r}"].value in (None, "") or (isinstance(ws[f"E{r}"].value, str) and str(ws[f"E{r}"].value).startswith("=")):
            ws[f"E{r}"].value = 380
        if ws[f"F{r}"].value in (None, "") or (isinstance(ws[f"F{r}"].value, str) and str(ws[f"F{r}"].value).startswith("=")):
            ws[f"F{r}"].value = "L1, L2, L3"

        if ws[f"P{r}"].value in (None, ""):
            ws[f"P{r}"].value = 1

        for col in ("G", "L", "M", "N"):
            ws[f"{col}{r}"].value = _section_sum_formula(col, ls, le, sec, include_reserve=include_reserve)

        ws[f"J{r}"].value = f"=Q{r}/S{r}"
        ws[f"K{r}"].value = f"=ROUND(SQRT(1-(J{r})^2)/J{r},2)"
        ws[f"O{r}"].value = f"=G{r}^2/N{r}"
        ws[f"Q{r}"].value = f"=P{r}*L{r}"
        ws[f"R{r}"].value = f"=M{r}*1.1"
        ws[f"S{r}"].value = f"=SQRT(Q{r}^2+R{r}^2)"
        ws[f"T{r}"].value = f"=S{r}/(0.38*SQRT(3))"

        log.append(f"[BUSBAR_OK] row={r} sec={sec} include_reserve={include_reserve} loads=${ls}:${le}")

    return log