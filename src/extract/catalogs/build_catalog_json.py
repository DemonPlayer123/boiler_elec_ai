from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from src.extract.pdf_text import extract_text_pymupdf


def _norm_ws(s: str) -> str:
    s = (s or "").replace("\u00A0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _series_chunk(
    *,
    vendor: str,
    series: str,
    device_class: str,
    source_file: str,
    text: str,
    extra: dict | None = None,
) -> dict:
    payload = {
        "vendor": vendor,
        "series": series,
        "device_class": device_class,
        "source_file": source_file,
        "text": _norm_ws(text),
    }
    if extra:
        payload.update(extra)
    return payload


def _add_mcb_entries(
    out: list[dict],
    *,
    vendor: str,
    series: str,
    source_file: str,
    currents: list[int],
    poles: list[str],
    curves: list[str],
    ka_by_current: dict[int, float] | float,
) -> None:
    pole_map = {
        "1P": 1,
        "1P+N": 1,
        "2P": 2,
        "3P": 3,
        "3P+N": 3,
        "4P": 4,
    }

    for current in currents:
        ka = ka_by_current[current] if isinstance(ka_by_current, dict) else ka_by_current
        for pole in poles:
            for curve in curves:
                out.append(
                    {
                        "vendor": vendor,
                        "series": series,
                        "device_class": "MCB",
                        "model": f"{series} {pole} {curve}{current}",
                        "rated_current_a": current,
                        "poles": pole_map.get(pole),
                        "poles_label": pole,
                        "trip_curve": curve,
                        "breaking_capacity_ka": ka,
                        "article": None,
                        "source_file": source_file,
                    }
                )
                
def _add_mcb_entries_with_articles(
    out: list[dict],
    *,
    vendor: str,
    series: str,
    source_file: str,
    currents: list[int],
    poles: list[str],
    curves: list[str],
    breaking_capacity_ka: float,
    articles: dict[tuple[str, str, int], str] | None = None,
    source_quality: str | None = None,
) -> None:
    pole_map = {
        "1P": 1,
        "1P+N": 1,
        "2P": 2,
        "3P": 3,
        "3P+N": 3,
        "4P": 4,
    }

    for current in currents:
        for pole in poles:
            for curve in curves:
                article = None
                if articles:
                    article = articles.get((pole, curve, current))

                row = {
                    "vendor": vendor,
                    "series": series,
                    "device_class": "MCB",
                    "model": f"{series} {pole} {curve}{current}",
                    "rated_current_a": current,
                    "poles": pole_map.get(pole),
                    "poles_label": pole,
                    "trip_curve": curve,
                    "breaking_capacity_ka": breaking_capacity_ka,
                    "article": article,
                    "source_file": source_file,
                }
                if source_quality:
                    row["source_quality"] = source_quality

                out.append(row)


def _add_rcbo_entries(
    out: list[dict],
    *,
    vendor: str,
    series: str,
    source_file: str,
    currents: list[int],
    poles: list[str],
    curves: list[str],
    rcd_ma_values: list[int],
    breaking_capacity_ka: float,
    rcd_type: str | None = None,
) -> None:
    pole_map = {
        "1P+N": 1,
        "2P": 2,
        "3P+N": 3,
        "4P": 4,
    }

    for current in currents:
        for pole in poles:
            for curve in curves:
                for rcd_ma in rcd_ma_values:
                    out.append(
                        {
                            "vendor": vendor,
                            "series": series,
                            "device_class": "RCBO",
                            "model": f"{series} {pole} {curve}{current} {rcd_ma}mA",
                            "rated_current_a": current,
                            "poles": pole_map.get(pole),
                            "poles_label": pole,
                            "trip_curve": curve,
                            "breaking_capacity_ka": breaking_capacity_ka,
                            "rcd_ma": rcd_ma,
                            "rcd_type": rcd_type,
                            "article": None,
                            "source_file": source_file,
                        }
                    )


def _add_mpcb_entries_from_ranges(
    out: list[dict],
    *,
    vendor: str,
    series: str,
    source_file: str,
    ranges: list[tuple[float, float]],
) -> None:
    for lo, hi in ranges:
        out.append(
            {
                "vendor": vendor,
                "series": series,
                "device_class": "MPCB",
                "model": f"{series} {lo:g}-{hi:g}A",
                "rated_current_a": hi,
                "current_range_a": {"min": lo, "max": hi},
                "poles": 3,
                "poles_label": "3P",
                "trip_curve": "D",
                "breaking_capacity_ka": None,
                "article": None,
                "source_file": source_file,
            }
        )

def _add_mccb_entries(
    out: list[dict],
    *,
    vendor: str,
    series: str,
    source_file: str,
    currents: list[int],
    poles: list[str],
    breaking_capacity_ka: float | None,
) -> None:
    pole_map = {
        "1P": 1,
        "2P": 2,
        "3P": 3,
        "4P": 4,
    }

    for current in currents:
        for pole in poles:
            out.append(
                {
                    "vendor": vendor,
                    "series": series,
                    "device_class": "MCCB",
                    "model": f"{series} {pole} {current}A",
                    "rated_current_a": current,
                    "current_range_a": None,
                    "poles": pole_map.get(pole),
                    "poles_label": pole,
                    "trip_curve": None,
                    "breaking_capacity_ka": breaking_capacity_ka,
                    "article": None,
                    "source_file": source_file,
                }
            )

def _parse_chint_distribution(text: str, source_file: str) -> tuple[list[dict], list[dict]]:
    metadata: list[dict] = []
    corpus: list[dict] = []
    txt = _norm_ws(text)

    # NB2LE-80ZT
    if "NB2LE-80ZT" in txt:
        _add_rcbo_entries(
            metadata,
            vendor="CHINT",
            series="NB2LE-80ZT",
            source_file=source_file,
            currents=[6, 10, 16, 20, 25, 32, 40, 50, 63, 80],
            poles=["1P+N", "3P+N"],
            curves=["C", "D"],
            rcd_ma_values=[10, 30, 100, 300],
            breaking_capacity_ka=6.0,
            rcd_type=None,
        )
        corpus.append(
            _series_chunk(
                vendor="CHINT",
                series="NB2LE-80ZT",
                device_class="RCBO",
                source_file=source_file,
                text="""
                NB2LE-80ZT: АВДТ, 1P+N и 3P+N, кривые C/D, номинальные токи 6..80 A,
                номинальный отключающий дифференциальный ток 10/30/100/300 мА,
                отключающая способность 6 кА.
                """,
            )
        )

    # NXBLE-63
    if "NXBLE-63" in txt:
        _add_rcbo_entries(
            metadata,
            vendor="CHINT",
            series="NXBLE-63",
            source_file=source_file,
            currents=[6, 10, 16, 20, 25, 32, 40, 50, 63],
            poles=["1P+N", "2P", "3P+N", "4P"],
            curves=["B", "C", "D"],
            rcd_ma_values=[30, 100, 300],
            breaking_capacity_ka=6.0,
            rcd_type="AC",
        )
        corpus.append(
            _series_chunk(
                vendor="CHINT",
                series="NXBLE-63",
                device_class="RCBO",
                source_file=source_file,
                text="""
                NXBLE-63: АВДТ, полюса 1P+N/2P/3P+N/4P, кривые B/C/D,
                номинальные токи 6..63 A, тип AC, ток утечки 30/100/300 мА,
                отключающая способность 6 кА.
                """,
            )
        )

    # NXBLE-125
    if "NXBLE-125" in txt:
        _add_rcbo_entries(
            metadata,
            vendor="CHINT",
            series="NXBLE-125",
            source_file=source_file,
            currents=[63, 80, 100, 125],
            poles=["1P+N", "3P+N"],
            curves=["C"],
            rcd_ma_values=[30, 100, 300],
            breaking_capacity_ka=10.0,
            rcd_type="AC",
        )
        corpus.append(
            _series_chunk(
                vendor="CHINT",
                series="NXBLE-125",
                device_class="RCBO",
                source_file=source_file,
                text="""
                NXBLE-125: АВДТ, полюса 1P+N и 3P+N, кривая C,
                номинальные токи 63/80/100/125 A, ток утечки 30/100/300 мА,
                отключающая способность 10 кА.
                """,
            )
        )

    # NB8-125R
    if "NB8-125R" in txt or "NB8-125 R" in txt:
        ka_by_current = {
            16: 25.0,
            20: 25.0,
            25: 25.0,
            32: 25.0,
            40: 25.0,
            50: 25.0,
            63: 25.0,
            80: 20.0,
            100: 20.0,
        }
        _add_mcb_entries(
            metadata,
            vendor="CHINT",
            series="NB8-125R",
            source_file=source_file,
            currents=[16, 20, 25, 32, 40, 50, 63, 80, 100],
            poles=["1P", "1P+N", "2P", "3P", "3P+N", "4P"],
            curves=["B", "C", "D"],
            ka_by_current=ka_by_current,
        )
        corpus.append(
            _series_chunk(
                vendor="CHINT",
                series="NB8-125R",
                device_class="MCB",
                source_file=source_file,
                text="""
                NB8-125R: автоматические выключатели, полюса 1P/1P+N/2P/3P/3P+N/4P,
                кривые B/C/D, номинальные токи 16..100 A.
                Отключающая способность: 25 кА для 16..63 A и 20 кА для 80..100 A.
                """,
            )
        )

    return metadata, corpus

def _parse_chint_nxb63h(text: str, source_file: str) -> tuple[list[dict], list[dict]]:
    metadata: list[dict] = []
    corpus: list[dict] = []
    txt = _norm_ws(text)

    if "NXB-63H" not in txt and "NXB-63 (H)" not in txt and "NXB-63HАвтоматические" not in txt:
        return metadata, corpus

    # По каталогу:
    # 1P/2P/3P/4P, B/C/D, 1/2/3/4/6/10/16/20/25/32/40/50/63 A, 10 кА
    currents = [1, 2, 3, 4, 6, 10, 16, 20, 25, 32, 40, 50, 63]
    poles = ["1P", "2P", "3P", "4P"]
    curves = ["B", "C", "D"]

    _add_mcb_entries_with_articles(
        metadata,
        vendor="CHINT",
        series="NXB-63H",
        source_file=source_file,
        currents=currents,
        poles=poles,
        curves=curves,
        breaking_capacity_ka=10.0,
    )

    corpus.append(
        _series_chunk(
            vendor="CHINT",
            series="NXB-63H",
            device_class="MCB",
            source_file=source_file,
            text="""
            NXB-63H: модульные автоматические выключатели,
            1P/2P/3P/4P, характеристики B/C/D,
            номинальные токи 1/2/3/4/6/10/16/20/25/32/40/50/63 A,
            отключающая способность 10 кА.
            Применяются в сетях 230/400 В для защиты от перегрузки и короткого замыкания.
            """,
        )
    )

    return metadata, corpus

def _parse_chint_secondary_distribution(text: str, source_file: str) -> tuple[list[dict], list[dict]]:
    metadata: list[dict] = []
    corpus: list[dict] = []
    txt = _norm_ws(text)

    if "NM8N" not in txt and "NXM" not in txt and "NXMS" not in txt:
        return metadata, corpus

    # CHINT NM8N — MCCB / силовые автоматы в литом корпусе
    if "NM8N" in txt:
        _add_mccb_entries(
            metadata,
            vendor="CHINT",
            series="NM8N",
            source_file=source_file,
            currents=[16, 20, 25, 32, 40, 50, 63, 80, 100, 125, 160, 180, 200, 225, 250, 315, 350, 400, 500, 630, 700, 800],
            poles=["3P", "4P"],
            breaking_capacity_ka=36.0,
        )

        corpus.append(
            _series_chunk(
                vendor="CHINT",
                series="NM8N",
                device_class="MCCB",
                source_file=source_file,
                text="""
                NM8N: автоматические выключатели CHINT в литом корпусе.
                Номинальные токи от 16 до 1600 А, исполнения 3P/4P.
                Применяются для защиты распределительных сетей и силовых нагрузок.
                В каталоге также указаны исполнения с расцепителями для защиты двигателей.
                """,
            )
        )

    # CHINT NXM / NXMS — тоже силовая MCCB-линейка
    if "NXM" in txt or "NXMS" in txt:
        _add_mccb_entries(
            metadata,
            vendor="CHINT",
            series="NXM",
            source_file=source_file,
            currents=[63, 100, 125, 160, 180, 200, 225, 250, 315, 400, 500, 630],
            poles=["3P", "4P"],
            breaking_capacity_ka=25.0,
        )

        _add_mccb_entries(
            metadata,
            vendor="CHINT",
            series="NXMS",
            source_file=source_file,
            currents=[63, 100, 125, 160, 180, 200, 225, 250, 315, 400, 500, 630],
            poles=["3P", "4P"],
            breaking_capacity_ka=25.0,
        )

        corpus.append(
            _series_chunk(
                vendor="CHINT",
                series="NXM/NXMS",
                device_class="MCCB",
                source_file=source_file,
                text="""
                NXM и NXMS: автоматические выключатели CHINT в литом корпусе
                для вторичного распределения. Используются как MCCB для силовых
                трехфазных нагрузок и распределительных линий.
                """,
            )
        )

    return metadata, corpus

def _parse_chint_motor_protection(text: str, source_file: str) -> tuple[list[dict], list[dict]]:
    metadata: list[dict] = []
    corpus: list[dict] = []
    txt = _norm_ws(text)

    if "NS2" not in txt and "NS8" not in txt:
        return metadata, corpus

    # CHINT NS2
    if "NS2" in txt:
        _add_mpcb_entries_from_ranges(
            metadata,
            vendor="CHINT",
            series="NS2",
            source_file=source_file,
            ranges=[
                (0.1, 0.16),
                (0.16, 0.25),
                (0.25, 0.4),
                (0.4, 0.63),
                (0.63, 1.0),
                (1.0, 1.6),
                (1.6, 2.5),
                (2.5, 4.0),
                (4.0, 6.3),
                (6.0, 10.0),
                (9.0, 14.0),
                (13.0, 18.0),
                (17.0, 23.0),
                (20.0, 25.0),
                (24.0, 32.0),
                (16.0, 25.0),
                (25.0, 40.0),
                (30.0, 40.0),
                (37.0, 50.0),
                (40.0, 63.0),
                (48.0, 65.0),
                (56.0, 80.0),
                (63.0, 80.0),
            ],
        )

        # Уточняем отключающую способность по типовым диапазонам
        for row in metadata:
            if row["vendor"] == "CHINT" and row["series"] == "NS2":
                hi = row["current_range_a"]["max"]
                if hi <= 10:
                    row["breaking_capacity_ka"] = 100.0
                elif hi <= 25:
                    row["breaking_capacity_ka"] = 15.0
                elif hi <= 32:
                    row["breaking_capacity_ka"] = 10.0
                else:
                    row["breaking_capacity_ka"] = 50.0

        corpus.append(
            _series_chunk(
                vendor="CHINT",
                series="NS2",
                device_class="MPCB",
                source_file=source_file,
                text="""
                NS2: автоматические выключатели для защиты и управления электродвигателями.
                3 полюса, диапазоны регулирования тока от 0.1 до 80 А.
                Используются для защиты двигателя от перегрузки, обрыва фазы и короткого замыкания.
                """,
            )
        )

    # CHINT NS8
    if "NS8" in txt:
        _add_mpcb_entries_from_ranges(
            metadata,
            vendor="CHINT",
            series="NS8",
            source_file=source_file,
            ranges=[
                (0.16, 0.25),
                (0.25, 0.4),
                (0.4, 0.63),
                (0.63, 1.0),
                (1.0, 1.6),
                (1.6, 2.5),
                (2.5, 4.0),
                (4.0, 6.3),
                (6.3, 10.0),
                (9.0, 14.0),
                (13.0, 18.0),
                (17.0, 23.0),
                (20.0, 25.0),
                (24.0, 32.0),
                (40.0, 50.0),
                (50.0, 64.0),
                (64.0, 72.0),
                (72.0, 80.0),
            ],
        )

        for row in metadata:
            if row["vendor"] == "CHINT" and row["series"] == "NS8":
                hi = row["current_range_a"]["max"]
                if hi <= 32:
                    row["breaking_capacity_ka"] = 100.0
                else:
                    row["breaking_capacity_ka"] = 50.0

        corpus.append(
            _series_chunk(
                vendor="CHINT",
                series="NS8",
                device_class="MPCB",
                source_file=source_file,
                text="""
                NS8: автоматические выключатели для защиты и управления электродвигателями,
                применяются в шкафах управления электродвигателями (MCC).
                3 полюса, диапазоны тока до 80 А.
                """,
            )
        )

    return metadata, corpus

def _parse_dekraft_dif103(text: str, source_file: str) -> tuple[list[dict], list[dict]]:
    metadata: list[dict] = []
    corpus: list[dict] = []
    txt = _norm_ws(text)

    if "ДИФ-103" not in txt and "ДИФ103" not in txt:
        return metadata, corpus

    _add_rcbo_entries(
        metadata,
        vendor="Dekraft",
        series="ДИФ-103",
        source_file=source_file,
        currents=[6, 10, 16, 20, 25, 32, 40, 50, 63],
        poles=["1P+N", "3P+N"],
        curves=["C"],
        rcd_ma_values=[10, 30, 100, 300],
        breaking_capacity_ka=4.5,
        rcd_type="AC",
    )

    corpus.append(
        _series_chunk(
            vendor="Dekraft",
            series="ДИФ-103",
            device_class="RCBO",
            source_file=source_file,
            text="""
            ДИФ-103: АВДТ Dekraft, отключающая способность 4.5 кА,
            номинальные токи 6..63 A, ток утечки 10/30/100/300 мА,
            структура обозначения включает полюсность, ток, ток утечки, тип AC и кривую C.
            """,
        )
    )

    return metadata, corpus


def _parse_dekraft_ba430(text: str, source_file: str) -> tuple[list[dict], list[dict]]:
    metadata: list[dict] = []
    corpus: list[dict] = []
    txt = _norm_ws(text)

    if "ВА-430" not in txt and "ВА430" not in txt:
        return metadata, corpus

    ranges: list[tuple[float, float]] = []
    seen: set[tuple[float, float]] = set()

    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*-\s*(\d+(?:[.,]\d+)?)\s*А", txt):
        lo = float(m.group(1).replace(",", "."))
        hi = float(m.group(2).replace(",", "."))

        # отсеиваем мусорные артефакты OCR/парсинга
        if lo <= 0 or hi <= 0:
            continue
        if lo >= hi:
            continue
        if lo > 100 or hi > 100:
            continue

        pair = (lo, hi)
        if pair not in seen:
            seen.add(pair)
            ranges.append(pair)

    ranges.sort(key=lambda x: (x[0], x[1]))

    # Icu по таблице каталога ВА-430.
    # Берем первую колонку Icu как базовую metadata-оценку для селектора.
    icu_map = {
        (0.1, 0.16): 100.0,
        (0.16, 0.25): 100.0,
        (0.25, 0.4): 100.0,
        (0.4, 0.63): 100.0,
        (0.63, 1.0): 100.0,
        (1.0, 1.6): 100.0,
        (1.6, 2.5): 100.0,
        (2.5, 4.0): 100.0,
        (4.0, 6.3): 100.0,
        (6.0, 10.0): 100.0,
        (9.0, 14.0): 15.0,
        (13.0, 18.0): 15.0,
        (17.0, 23.0): 15.0,
        (20.0, 25.0): 15.0,
        (24.0, 32.0): 10.0,
        (25.0, 40.0): 30.0,
        (40.0, 63.0): 30.0,
        (63.0, 80.0): 35.0,
    }

    for lo, hi in ranges:
        metadata.append(
            {
                "vendor": "Dekraft",
                "series": "ВА-430",
                "device_class": "MPCB",
                "model": f"ВА-430 {lo:g}-{hi:g}A",
                "rated_current_a": hi,
                "current_range_a": {"min": lo, "max": hi},
                "poles": 3,
                "poles_label": "3P",
                "trip_curve": "D",
                "breaking_capacity_ka": icu_map.get((lo, hi)),
                "article": None,
                "source_file": source_file,
            }
        )

    corpus.append(
        _series_chunk(
            vendor="Dekraft",
            series="ВА-430",
            device_class="MPCB",
            source_file=source_file,
            text="""
            ВА-430: автоматические выключатели защиты двигателя Dekraft.
            Предназначены для управления и защиты трехфазных асинхронных электродвигателей
            от короткого замыкания, перегрузки и выпадения фазы.
            В корпусе совмещены автоматический выключатель с характеристикой D
            и тепловое реле перегрузки.
            """,
            extra={"current_ranges_found": ranges},
        )
    )

    return metadata, corpus

def _parse_dekraft_ba103_fallback(text: str, source_file: str) -> tuple[list[dict], list[dict]]:
    metadata: list[dict] = []
    corpus: list[dict] = []

    # OCR у PDF плохой, поэтому делаем контролируемый fallback.
    # Серия нужна как дополнительное покрытие малых MCB.
    currents = [1, 2, 3, 4, 6, 10, 16, 20, 25, 32, 40, 50, 63]
    poles = ["1P", "2P", "3P", "4P"]
    curves = ["B", "C", "D"]

    _add_mcb_entries_with_articles(
        metadata,
        vendor="Dekraft",
        series="ВА-103",
        source_file=source_file,
        currents=currents,
        poles=poles,
        curves=curves,
        breaking_capacity_ka=6.0,
        source_quality="fallback_low_confidence",
    )

    corpus.append(
        _series_chunk(
            vendor="Dekraft",
            series="ВА-103",
            device_class="MCB",
            source_file=source_file,
            text="""
            ВА-103: fallback-слой metadata для модульных автоматических выключателей Dekraft.
            PDF распарсен с низким качеством, поэтому серия добавлена как низкоуверенное покрытие
            для малых MCB и альтернатив CHINT.
            """,
            extra={"source_quality": "fallback_low_confidence"},
        )
    )

    return metadata, corpus


def _parse_dekraft_ba201_fallback(text: str, source_file: str) -> tuple[list[dict], list[dict]]:
    metadata: list[dict] = []
    corpus: list[dict] = []

    currents = [1, 2, 3, 4, 6, 10, 16, 20, 25, 32, 40, 50, 63]
    poles = ["1P", "2P", "3P", "4P"]
    curves = ["B", "C", "D"]

    _add_mcb_entries_with_articles(
        metadata,
        vendor="Dekraft",
        series="ВА-201",
        source_file=source_file,
        currents=currents,
        poles=poles,
        curves=curves,
        breaking_capacity_ka=6.0,
        source_quality="fallback_low_confidence",
    )

    corpus.append(
        _series_chunk(
            vendor="Dekraft",
            series="ВА-201",
            device_class="MCB",
            source_file=source_file,
            text="""
            ВА-201: fallback-слой metadata для модульных автоматических выключателей Dekraft.
            PDF распарсен с низким качеством, поэтому серия добавлена как низкоуверенное покрытие
            для малых MCB и альтернатив CHINT.
            """,
            extra={"source_quality": "fallback_low_confidence"},
        )
    )

    return metadata, corpus

def _add_keaz_va47_entries(
    out: list[dict],
    *,
    source_file: str,
) -> None:
    # По каталогу КЭАЗ:
    # ВА47-29 -> 4.5 кА, токи 1..63 А
    # ВА47-100 -> 10 кА, токи 10..125 А
    _add_mcb_entries_with_articles(
        out,
        vendor="KEAZ",
        series="ВА47-29",
        source_file=source_file,
        currents=[1, 2, 3, 4, 5, 6, 8, 10, 16, 20, 25, 32, 40, 50, 63],
        poles=["1P", "2P", "3P", "4P"],
        curves=["B", "C", "D"],
        breaking_capacity_ka=4.5,
    )

    _add_mcb_entries_with_articles(
        out,
        vendor="KEAZ",
        series="ВА47-100",
        source_file=source_file,
        currents=[10, 16, 20, 25, 32, 40, 50, 63, 80, 100, 125],
        poles=["1P", "2P", "3P", "4P"],
        curves=["B", "C", "D"],
        breaking_capacity_ka=10.0,
    )


def _add_keaz_rcbo_entries(
    out: list[dict],
    *,
    source_file: str,
) -> None:
    # АВДТ32: 2P, C, 6..40 A, 30/100 mA, тип A/AC, 4.5 или 6 кА
    _add_rcbo_entries(
        out,
        vendor="KEAZ",
        series="АВДТ32",
        source_file=source_file,
        currents=[6, 10, 16, 20, 25, 32, 40],
        poles=["2P"],
        curves=["C"],
        rcd_ma_values=[30, 100],
        breaking_capacity_ka=4.5,
        rcd_type="AC",
    )
    _add_rcbo_entries(
        out,
        vendor="KEAZ",
        series="АВДТ32",
        source_file=source_file,
        currents=[6, 10, 16, 20, 25, 32, 40],
        poles=["2P"],
        curves=["C"],
        rcd_ma_values=[30, 100],
        breaking_capacity_ka=4.5,
        rcd_type="A",
    )

    # АД12: 2P, C, 6..63 A, 10/30/100/300 mA, тип AC
    _add_rcbo_entries(
        out,
        vendor="KEAZ",
        series="АД12",
        source_file=source_file,
        currents=[6, 10, 16, 20, 25, 32, 40, 50, 63],
        poles=["2P"],
        curves=["C"],
        rcd_ma_values=[10, 30, 100, 300],
        breaking_capacity_ka=4.5,
        rcd_type="AC",
    )

    # АД14: 4P, C, 6..63 A, 10/30/100/300 mA, тип AC
    _add_rcbo_entries(
        out,
        vendor="KEAZ",
        series="АД14",
        source_file=source_file,
        currents=[6, 10, 16, 20, 25, 32, 40, 50, 63],
        poles=["4P"],
        curves=["C"],
        rcd_ma_values=[10, 30, 100, 300],
        breaking_capacity_ka=4.5,
        rcd_type="AC",
    )


def _parse_keaz_catalog(text: str, source_file: str) -> tuple[list[dict], list[dict]]:
    metadata: list[dict] = []
    corpus: list[dict] = []
    txt = _norm_ws(text)

    # Жёсткая защита, чтобы не сработать на посторонний PDF
    if "КЭАЗ" not in txt and "katalog-keaz" not in source_file.lower():
        return metadata, corpus

    # MCB серии ВА47
    if "ВА47" in txt and "ГОСТ IEC 60898-1" in txt:
        _add_keaz_va47_entries(metadata, source_file=source_file)

        corpus.append(
            _series_chunk(
                vendor="KEAZ",
                series="ВА47",
                device_class="MCB",
                source_file=source_file,
                text="""
                КЭАЗ ВА47: модульные автоматические выключатели серий ВА47-29 и ВА47-100.
                Соответствуют ГОСТ IEC 60898-1.
                Поддерживаются исполнения 1P/2P/3P/4P, характеристики B/C/D.
                ВА47-29: номинальные токи 1..63 A, отключающая способность 4.5 кА.
                ВА47-100: номинальные токи 10..125 A, отключающая способность 10 кА.
                """,
            )
        )

    # RCBO серии АВДТ32 / АД12 / АД14
    if ("АВДТ32" in txt or "АД12" in txt or "АД14" in txt) and "ГОСТ IEC 61009-1" in txt:
        _add_keaz_rcbo_entries(metadata, source_file=source_file)

        corpus.append(
            _series_chunk(
                vendor="KEAZ",
                series="АВДТ32/АД12/АД14",
                device_class="RCBO",
                source_file=source_file,
                text="""
                КЭАЗ АВДТ32, АД12, АД14: автоматические выключатели дифференциального тока
                со встроенной защитой от сверхтоков.
                Соответствуют ГОСТ IEC 61009-1.
                АВДТ32: 2P, характеристика C, токи 6..40 A.
                АД12: 2P, характеристика C, токи 6..63 A.
                АД14: 4P, характеристика C, токи 6..63 A.
                Типы по дифференциальному току: A/AC или AC, уставки 10/30/100/300 мА.
                """,
            )
        )
    
        # MCCB серии КЭАЗ из силового раздела каталога
    if any(x in txt for x in ["ВА57-35", "ВА04-36", "ВА51-35", "ВА57-39", "ВА51-39"]):
        _add_mccb_entries(
            metadata,
            vendor="KEAZ",
            series="ВА57-35",
            source_file=source_file,
            currents=[160, 200, 250],
            poles=["3P"],
            breaking_capacity_ka=44.0,
        )

        _add_mccb_entries(
            metadata,
            vendor="KEAZ",
            series="ВА04-36",
            source_file=source_file,
            currents=[160, 200, 250],
            poles=["3P"],
            breaking_capacity_ka=35.0,
        )

        _add_mccb_entries(
            metadata,
            vendor="KEAZ",
            series="ВА51-35",
            source_file=source_file,
            currents=[160, 200, 250],
            poles=["3P"],
            breaking_capacity_ka=18.0,
        )

        _add_mccb_entries(
            metadata,
            vendor="KEAZ",
            series="ВА57-39",
            source_file=source_file,
            currents=[320, 400, 630],
            poles=["3P"],
            breaking_capacity_ka=40.0,
        )

        _add_mccb_entries(
            metadata,
            vendor="KEAZ",
            series="ВА51-39",
            source_file=source_file,
            currents=[320, 400, 630],
            poles=["3P"],
            breaking_capacity_ka=25.0,
        )

        corpus.append(
            _series_chunk(
                vendor="KEAZ",
                series="ВА57-35/ВА04-36/ВА51-35/ВА57-39/ВА51-39",
                device_class="MCCB",
                source_file=source_file,
                text="""
                КЭАЗ силовые автоматические выключатели в литом корпусе.
                Серии ВА57-35, ВА04-36, ВА51-35 покрывают диапазон 160..250 А.
                Серии ВА57-39 и ВА51-39 покрывают диапазон 320..630 А.
                Используются как MCCB для силовых трехфазных нагрузок.
                """,
            )
        )

    return metadata, corpus

def parse_catalogs_to_json(catalogs_dir: Path) -> tuple[list[dict], list[dict]]:
    metadata: list[dict] = []
    corpus: list[dict] = []

    for pdf_path in sorted(catalogs_dir.glob("*.pdf")):
        text = extract_text_pymupdf(pdf_path)
        if not text:
            continue

        file_name = pdf_path.name

        if "Оборудование конечного распределения" in file_name:
            md, cp = _parse_chint_distribution(text, file_name)
            metadata.extend(md)
            corpus.extend(cp)

            md, cp = _parse_chint_nxb63h(text, file_name)
            metadata.extend(md)
            corpus.extend(cp)

        elif "Оборудование вторичного распределения" in file_name:
            md, cp = _parse_chint_secondary_distribution(text, file_name)
            metadata.extend(md)
            corpus.extend(cp)

        elif "Оборудование для защиты и управления" in file_name:
            md, cp = _parse_chint_motor_protection(text, file_name)
            metadata.extend(md)
            corpus.extend(cp)

        elif "ДИФ103" in file_name.upper():
            md, cp = _parse_dekraft_dif103(text, file_name)
            metadata.extend(md)
            corpus.extend(cp)

        elif "ВА430" in file_name.upper():
            md, cp = _parse_dekraft_ba430(text, file_name)
            metadata.extend(md)
            corpus.extend(cp)
        
        elif "ВА103" in file_name.upper():
            md, cp = _parse_dekraft_ba103_fallback(text, file_name)
            metadata.extend(md)
            corpus.extend(cp)
        
        elif "ВА201" in file_name.upper():
            md, cp = _parse_dekraft_ba201_fallback(text, file_name)
            metadata.extend(md)
            corpus.extend(cp)
            
        elif "КЭАЗ" in file_name.upper() or "KEAZ" in file_name.upper():
            md, cp = _parse_keaz_catalog(text, file_name)
            metadata.extend(md)
            corpus.extend(cp)

        else:
            # Пока остальные каталоги просто кладём в corpus как raw-source,
            # чтобы не терять их для RAG даже без полной metadata-нормализации.
            corpus.append(
                {
                    "vendor": "unknown",
                    "series": pdf_path.stem,
                    "device_class": "unknown",
                    "source_file": file_name,
                    "text": _norm_ws(text[:12000]),
                }
            )

    return metadata, corpus


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalogs_dir", required=True, help="Папка с PDF-каталогами")
    ap.add_argument("--out_metadata", required=True, help="Куда сохранить catalog_metadata.json")
    ap.add_argument("--out_corpus", required=True, help="Куда сохранить catalog_corpus.json")
    args = ap.parse_args()

    catalogs_dir = Path(args.catalogs_dir)
    metadata, corpus = parse_catalogs_to_json(catalogs_dir)

    _save_json(Path(args.out_metadata), metadata)
    _save_json(Path(args.out_corpus), corpus)

    print(f"catalog_metadata: {len(metadata)}")
    print(f"catalog_corpus: {len(corpus)}")


if __name__ == "__main__":
    main()