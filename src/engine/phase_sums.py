from __future__ import annotations

from typing import List, Optional, Tuple

ANCHOR_COL = "D"


def find_busbar1_row(ws, *, end_row: int = 5000) -> Optional[int]:
    for r in range(1, end_row + 1):
        v = ws[f"{ANCHOR_COL}{r}"].value
        if v and "секция шин 1" in str(v).lower():
            return r
    return None


def compute_loads_range(ws, *, end_row: int = 5000, loads_start: int = 6) -> Tuple[int, int]:
    """
    Фактический диапазон строк нагрузок.
    Сканируем до строки "Секция шин 1" и берем последнюю реально заполненную строку (по D или G).
    Это устойчиво к вставкам строк перед секциями.
    """
    b1 = find_busbar1_row(ws, end_row=end_row)
    if not b1:
        last = loads_start
        for r in range(loads_start, end_row + 1):
            if ws[f"D{r}"].value not in (None, "") or ws[f"G{r}"].value not in (None, ""):
                last = r
        return loads_start, last

    scan_to = max(loads_start, b1 - 1)
    last = loads_start
    for r in range(loads_start, scan_to + 1):
        if ws[f"D{r}"].value not in (None, "") or ws[f"G{r}"].value not in (None, ""):
            last = r

    return loads_start, last


def _is_phase(v) -> bool:
    return str(v).strip().upper() in ("L1", "L2", "L3")


def update_single_phase_sumifs(ws, *, end_row: int = 5000, loads_start: int = 6) -> List[str]:
    """
    Обновляет формулы однофазки (EN):
      =SUMIFS($G$6:$G$N,$A$6:$A$N,E64,$F$6:$F$N,F64)
    """
    log: List[str] = []
    ls, le = compute_loads_range(ws, end_row=end_row, loads_start=loads_start)

    def make_formula(row: int) -> str:
        return (
            f"=SUMIFS($G${ls}:$G${le},"
            f"$A${ls}:$A${le},E{row},"
            f"$F${ls}:$F${le},F{row})"
        )

    for r in range(1, end_row + 1):
        e = ws[f"E{r}"].value
        f = ws[f"F{r}"].value
        if e not in (11, 12, "11", "12"):
            continue
        if not _is_phase(f):
            continue

        addr = f"G{r}"
        old = ws[addr].value
        new = make_formula(r)

        if old in (None, "") or (isinstance(old, str) and ("SUMIFS" in old.upper() or "СУММЕСЛИМН" in old.upper())):
            if old != new:
                ws[addr].value = new
                log.append(f"[PHASE_SUM] {addr}: {old} -> {new}")

    log.append(f"[PHASE_SUM_OK] loads_range=${ls}:${le}")
    return log