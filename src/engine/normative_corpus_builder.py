from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.extract.pdf_text import extract_text_pymupdf


def _norm_text(text: str) -> str:
    text = str(text or "")
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _guess_title_from_filename(path: Path) -> str:
    name = path.stem.strip()
    return name


def _split_into_paragraphs(text: str) -> list[str]:
    text = _norm_text(text)
    if not text:
        return []
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def _chunk_paragraphs(paragraphs: list[str], target_len: int = 1200) -> list[str]:
    """
    Склеивает абзацы в чанки примерно target_len символов.
    """
    chunks: list[str] = []
    buffer = ""

    for p in paragraphs:
        if not buffer:
            buffer = p
            continue

        if len(buffer) + 2 + len(p) <= target_len:
            buffer += "\n\n" + p
        else:
            chunks.append(buffer.strip())
            buffer = p

    if buffer.strip():
        chunks.append(buffer.strip())

    return chunks


def _detect_section(chunk_text: str) -> str:
    first_line = chunk_text.splitlines()[0].strip() if chunk_text.strip() else ""
    if len(first_line) <= 120:
        return first_line
    return "section_unknown"


def build_normative_corpus_from_dir(norms_dir: Path) -> list[dict]:
    """
    Собирает normative_corpus.json из PDF в папке norms_dir.
    """
    corpus: list[dict] = []

    pdf_files = sorted(norms_dir.glob("*.pdf"))
    for pdf_path in pdf_files:
        text = extract_text_pymupdf(pdf_path)
        text = _norm_text(text)
        if not text:
            continue

        title = _guess_title_from_filename(pdf_path)
        paragraphs = _split_into_paragraphs(text)
        chunks = _chunk_paragraphs(paragraphs, target_len=1200)

        for idx, chunk in enumerate(chunks, start=1):
            corpus.append(
                {
                    "doc_id": pdf_path.stem,
                    "title": title,
                    "section": _detect_section(chunk),
                    "chunk_id": f"{pdf_path.stem}_{idx:04d}",
                    "text": chunk,
                    "source_file": pdf_path.name,
                }
            )

    return corpus