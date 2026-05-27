from __future__ import annotations

import re
from typing import Any


def _norm_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("ё", "е")
    return text


def _tokenize(text: str) -> list[str]:
    text = _norm_text(text)
    return re.findall(r"[a-zA-Zа-яА-Я0-9\-]+", text)


def _build_query(req: dict, shortlist_row: dict) -> str:
    parts: list[str] = []

    for key in [
        "ep_kind",
        "load_type",
        "phase_type",
        "device_class",
        "trip_curve",
    ]:
        val = req.get(key)
        if val:
            parts.append(str(val))

    if req.get("rcd_required") is True:
        parts.append("узо дифзащита защита от поражения током")

    top_candidates = shortlist_row.get("top_candidates") or []
    for row in top_candidates[:3]:
        cand = row.get("candidate") or {}
        for key in ["series", "model", "device_class", "trip_curve"]:
            val = cand.get(key)
            if val:
                parts.append(str(val))

    # доменные русские ключи для усиления retrieval
    ep_kind = _norm_text(req.get("ep_kind"))
    load_type = _norm_text(req.get("load_type"))

    if ep_kind in {"pump", "fan", "burner"} or load_type == "motor":
        parts.extend(
            [
                "электродвигатель",
                "защита от сверхтока",
                "автоматический выключатель",
                "характеристика срабатывания",
            ]
        )

    if ep_kind in {"lighting"}:
        parts.extend(
            [
                "освещение",
                "групповая линия",
                "автоматический выключатель",
            ]
        )

    if ep_kind in {"heating"}:
        parts.extend(
            [
                "электрообогрев",
                "нагревательная нагрузка",
                "узо",
                "дифференциальная защита",
            ]
        )

    if ep_kind in {"cabinet", "hvac"}:
        parts.extend(
            [
                "щит",
                "шкаф",
                "низковольтное комплектное устройство",
                "автоматический выключатель",
            ]
        )

    return " ".join(parts)


def _score_chunk(query_tokens: set[str], chunk_text: str, source_title: str) -> float:
    text_tokens = set(_tokenize(chunk_text))
    title_tokens = set(_tokenize(source_title))

    if not text_tokens and not title_tokens:
        return 0.0

    text_overlap = len(query_tokens & text_tokens)
    title_overlap = len(query_tokens & title_tokens)

    score = text_overlap + 1.5 * title_overlap

    # бонус за особо значимые слова
    important = {
        "поуэ", "пуэ", "гост", "iec", "защита", "сверхтока",
        "автоматический", "выключатель", "узо", "дифференциальная",
        "двигатель", "освещение", "нагрев"
    }
    score += 0.5 * len((query_tokens & important) & text_tokens)

    return float(score)


def retrieve_normative_chunks(
    requirements: list[dict],
    shortlist: list[dict],
    normative_corpus: list[dict],
    top_k: int = 5,
) -> list[dict]:
    """
    normative_corpus ожидается как список словарей:
    {
      "doc_id": "...",
      "title": "...",
      "section": "...",
      "chunk_id": "...",
      "text": "..."
    }
    """
    shortlist_by_tag = {
        str(row.get("tag") or "").strip(): row
        for row in shortlist or []
        if row.get("tag")
    }

    results: list[dict] = []

    for req in requirements or []:
        tag = str(req.get("tag") or "").strip()
        if not tag:
            continue

        sl = shortlist_by_tag.get(tag, {"tag": tag, "top_candidates": []})
        query = _build_query(req, sl)
        query_tokens = set(_tokenize(query))

        ranked: list[dict] = []
        for chunk in normative_corpus or []:
            text = str(chunk.get("text") or "")
            title = str(chunk.get("title") or "")
            score = _score_chunk(query_tokens, text, title)
            if score <= 0:
                continue

            ranked.append(
                {
                    "doc_id": chunk.get("doc_id"),
                    "title": chunk.get("title"),
                    "section": chunk.get("section"),
                    "chunk_id": chunk.get("chunk_id"),
                    "text": text,
                    "score": round(score, 3),
                }
            )

        ranked = sorted(ranked, key=lambda x: x["score"], reverse=True)

        results.append(
            {
                "tag": tag,
                "query": query,
                "chunks": ranked[:top_k],
            }
        )

    return results