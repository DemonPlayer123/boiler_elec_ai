from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from src.engine.normative_qdrant_store import NormativeQdrantStore


_RU_STOPWORDS = {
    "и", "в", "во", "на", "по", "для", "от", "до", "с", "со", "к", "ко", "о", "об", "при",
    "не", "или", "а", "но", "это", "как", "из", "за", "под", "над", "же", "то", "ли",
    "что", "его", "ее", "их", "бы", "быть", "так", "та", "тот", "те", "у"
}


def _norm_text(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"[^a-zа-я0-9+./\- ]+", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _tokenize(s: str) -> list[str]:
    s = _norm_text(s)
    toks = [t for t in s.split() if len(t) >= 2 and t not in _RU_STOPWORDS]
    return toks

def _expand_query_tokens(tokens: set[str]) -> set[str]:
    """
    Простое предметное расширение запроса.
    """
    expanded = set(tokens)

    motor_markers = {
        "двигатель", "двигателя", "электродвигатель", "электродвигателя",
        "пускатель", "пускателя", "пуск", "мотор", "motor"
    }
    rcd_markers = {
        "удт", "узо", "авдт", "rcd", "rcbo", "дифференциальный",
        "30ма", "30", "мa", "ma"
    }
    overcurrent_markers = {
        "сверхток", "перегрузка", "короткое", "замыкание", "кз"
    }

    if expanded & motor_markers:
        expanded.update({
            "электродвигатель", "двигатель", "пускатель", "контактор",
            "motor", "starter", "60947-4-1", "60947", "перегрузка",
            "короткого", "замыкания", "защита"
        })

    if expanded & rcd_markers:
        expanded.update({
            "удт", "узо", "авдт", "дифференциального", "30ма",
            "30", "мa", "ma", "61009", "60364-4-41", "дополнительная", "защита"
        })

    if expanded & overcurrent_markers:
        expanded.update({
            "сверхтока", "перегрузки", "короткого", "замыкания",
            "60364-4-43", "60947-2", "60898"
        })

    return expanded


def _is_noise_chunk(chunk: NormChunk) -> bool:
    txt = _norm_text(f"{chunk.section_hint} {chunk.text[:500]}")
    noise_markers = {
        "предисловие",
        "содержание",
        "библиография",
        "приложение да",
        "перечень замечаний от некоторых стран",
        "окончание таблицы",
        "продолжение таблицы",
        "редактор",
        "компьютерная верстка",
    }
    return any(m in txt for m in noise_markers)

def _is_weak_evidence_chunk(chunk: NormChunk) -> bool:
    txt = _norm_text(f"{chunk.section_hint} {chunk.text[:1200]}")

    weak_markers = {
        "рисунок",
        "таблица",
        "испытательная цепь",
        "калибровочной записи",
        "типовая схема",
        "датчики тока",
        "датчики напряжения",
        "примечание — методика испытаний",
        "электротехническая библиотека",
        "термины и определения",
        "holding power",
        "pick-up power",
        "position of rest",
        "окончание таблицы",
        "продолжение таблицы",
    }

    hit_count = sum(1 for m in weak_markers if m in txt)
    return hit_count >= 2

def _doc_type_boost(query_tokens: set[str], chunk: NormChunk) -> float:
    q = _norm_text(" ".join(sorted(query_tokens)))
    title = _norm_text(chunk.doc_title)
    source = _norm_text(chunk.source_file)
    sec = _norm_text(chunk.section_hint)
    text_head = _norm_text(chunk.text[:1600])

    score = 0.0

    is_motor_query = any(x in q for x in [
        "двигатель", "электродвигатель", "пускатель", "контактор", "motor", "starter"
    ])
    is_rcbo_query = any(x in q for x in [
        "удт", "узо", "авдт", "rcbo", "rcd", "30ма", "30 ма", "30 ma"
    ])
    is_mcb_query = any(x in q for x in [
        "автоматический", "выключатель", "сверхток", "перегрузка", "короткого", "замыкания"
    ])
    is_curve_d_query = any(x in q for x in [
        "характеристика d", "пусковые", "d40", "d32", "d25", "d16", "d10"
    ])

    # --- MOTOR / MPCB ---
    if is_motor_query:
        if "60947-4-1" in title or "60947-4-1" in source:
            score += 8.0
        if "пускатель" in text_head or "контактор" in text_head or "электродвигател" in text_head:
            score += 5.0
        if "защит" in text_head and "перегруз" in text_head:
            score += 3.0
        if "термины и определения" in text_head:
            score -= 3.5
        if "рисунок" in text_head and "таблица" in text_head:
            score -= 3.0

    # --- RCBO / RCD ---
    if is_rcbo_query:
        if "60364-4-41" in title or "60364-4-41" in source:
            score += 8.0
        if "61009" in title or "61009" in source:
            score += 5.0
        if "удт" in text_head or "дифференциаль" in text_head or "30 ма" in text_head or "30 мa" in text_head:
            score += 4.0
        if "испытан" in text_head or "импульс" in text_head or "8/20" in text_head:
            score -= 4.0
        if "термины и определения" in text_head:
            score -= 2.5

    # --- MCB general ---
    if is_mcb_query:
        if "60364-4-43" in title or "60364-4-43" in source:
            score += 7.0
        if "60898" in title or "60898" in source:
            score += 6.0
        if "перегруз" in text_head or "короткого замыкания" in text_head or "сверхток" in text_head:
            score += 3.0

    # --- MCB with D curve / inrush loads ---
    if is_curve_d_query:
        if "60947-4-1" in title or "60947-4-1" in source:
            score += 1.5
        if "пускател" in text_head and "двигател" in text_head:
            score += 1.5
        # но не даём моторной теме полностью задавить обычный MCB-кейс
        if "60364-4-43" in title or "60364-4-43" in source:
            score += 3.5
        if "60898" in title or "60898" in source:
            score += 4.0

    return score

@dataclass
class NormChunk:
    chunk_id: str
    doc_title: str
    source_file: str
    section_hint: str
    text: str


def load_normative_corpus(path: Path) -> list[NormChunk]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: list[NormChunk] = []

    for row in raw:
        out.append(
            NormChunk(
                chunk_id=str(row.get("chunk_id") or ""),
                doc_title=str(row.get("doc_title") or ""),
                source_file=str(row.get("source_file") or ""),
                section_hint=str(row.get("section_hint") or ""),
                text=str(row.get("text") or ""),
            )
        )
    return out

def _row_to_chunk(row: dict) -> NormChunk:
    return NormChunk(
        chunk_id=str(row.get("chunk_id") or ""),
        doc_title=str(row.get("doc_title") or ""),
        source_file=str(row.get("source_file") or ""),
        section_hint=str(row.get("section_hint") or ""),
        text=str(row.get("text") or ""),
    )

def retrieve_normative_chunks_hybrid(
    query: str,
    corpus: list[NormChunk],
    qdrant_store: NormativeQdrantStore | None = None,
    top_k: int = 5,
    vector_k: int = 12,
) -> list[dict]:
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return []

    merged: dict[str, dict] = {}

    # lexical branch
    for chunk in corpus:
        s = _score_chunk(q_tokens, chunk)
        if s <= 0:
            continue
        merged[chunk.chunk_id] = {
            "chunk": chunk,
            "lexical_score": s,
            "vector_score": 0.0,
        }

    # vector branch
    if qdrant_store is not None:
        for vh in qdrant_store.search(query, top_k=vector_k):
            chunk = _row_to_chunk(vh.row)
            rec = merged.get(chunk.chunk_id)
            if rec is None:
                rec = {
                    "chunk": chunk,
                    "lexical_score": 0.0,
                    "vector_score": 0.0,
                }
                merged[chunk.chunk_id] = rec
            rec["vector_score"] = max(rec["vector_score"], vh.score)

    scored: list[tuple[float, NormChunk]] = []
    for rec in merged.values():
        chunk = rec["chunk"]
        lexical = float(rec["lexical_score"])
        vector = float(rec["vector_score"])

        score = lexical + vector * 8.0
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)

    out: list[dict] = []
    for score, chunk in scored[:top_k]:
        out.append(
            {
                "chunk_id": chunk.chunk_id,
                "doc_title": chunk.doc_title,
                "source_file": chunk.source_file,
                "section_hint": chunk.section_hint,
                "score": round(score, 4),
                "text": chunk.text,
            }
        )
    return out

def _score_chunk(query_tokens: set[str], chunk: NormChunk) -> float:
    if _is_noise_chunk(chunk):
        return 0.0
    if _is_weak_evidence_chunk(chunk):
        return 0.0

    expanded_q = _expand_query_tokens(query_tokens)

    text_tokens = set(_tokenize(chunk.text))
    hint_tokens = set(_tokenize(chunk.section_hint))
    title_tokens = set(_tokenize(chunk.doc_title))
    file_tokens = set(_tokenize(chunk.source_file))

    if not text_tokens and not hint_tokens and not title_tokens and not file_tokens:
        return 0.0

    text_overlap = len(expanded_q & text_tokens)
    hint_overlap = len(expanded_q & hint_tokens)
    title_overlap = len(expanded_q & title_tokens)
    file_overlap = len(expanded_q & file_tokens)

    score = (
        text_overlap * 1.0 +
        hint_overlap * 1.7 +
        title_overlap * 2.2 +
        file_overlap * 2.0
    )

    denom = math.sqrt(max(len(text_tokens), 1))
    score += text_overlap / denom

    score += _doc_type_boost(expanded_q, chunk)
    return score


def retrieve_normative_chunks(
    query: str,
    corpus: Iterable[NormChunk],
    top_k: int = 5,
) -> list[dict]:
    return retrieve_normative_chunks_hybrid(
        query=query,
        corpus=list(corpus),
        qdrant_store=None,
        top_k=top_k,
    )