from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from src.extract.pdf_text import extract_text_pymupdf
from src.extract.schemes.pdf_layout import extract_lines_by_blocks


def _norm_ws(s: str) -> str:
    s = (s or "").replace("\u00A0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _guess_doc_title(file_name: str) -> str:
    stem = Path(file_name).stem.replace("_", " ").strip().lower()

    if "пуэ" in stem:
        return "ПУЭ"
    if "61009" in stem:
        return "ГОСТ IEC 61009-1"
    if "60898" in stem:
        return "ГОСТ IEC 60898-1"
    if "60947 4 1" in stem or "60947-4-1" in stem:
        return "ГОСТ IEC 60947-4-1"
    if "60947 2" in stem or "60947-2" in stem:
        return "ГОСТ IEC 60947-2"
    if "60364 4 41" in stem or "60364-4-41" in stem:
        return "ГОСТ IEC 60364-4-41"
    if "60364 4 43" in stem or "60364-4-43" in stem:
        return "ГОСТ IEC 60364-4-43"
    if "60364 5 53" in stem or "60364-5-53" in stem:
        return "ГОСТ IEC 60364-5-53"
    if "сп 256" in stem or "256" in stem:
        return "СП 256.1325800.2016"
    if "сп 437" in stem or "437" in stem:
        return "СП 437.1325800.2018"

    return Path(file_name).stem

def _resolve_norms_dir(raw_path: str) -> Path:
    """
    Пытаемся найти папку с нормативкой:
    1) как есть, относительно cwd
    2) как абсолютный путь
    3) относительно корня проекта
    """
    p = Path(raw_path)

    # 1. как передали
    if p.exists():
        return p.resolve()

    # 2. относительно корня проекта
    project_root = Path(__file__).resolve().parents[3]
    p2 = (project_root / raw_path).resolve()
    if p2.exists():
        return p2

    # 3. fallback — вернуть абсолютный вариант как есть
    return p.resolve()

def _extract_norm_text(pdf_path: Path) -> str:
    """
    1) обычный text extraction
    2) fallback через layout/block extraction
    """
    text = extract_text_pymupdf(pdf_path)
    text = _norm_ws(text)
    if text:
        return text

    try:
        lines = extract_lines_by_blocks(pdf_path)
        text = "\n".join(lines)
        text = _norm_ws(text)
        if text:
            return text
    except Exception:
        pass

    return ""


def _extract_section_hint(text: str, default_title: str) -> str:
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    head = " | ".join(lines[:4])
    return head[:300] if head else default_title


def _chunk_text(text: str, chunk_size: int = 2200, overlap: int = 300) -> list[str]:
    text = _norm_ws(text)
    if not text:
        return []

    paragraphs = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    buf = ""

    for p in paragraphs:
        p = p.strip()
        if not p:
            continue

        candidate = f"{buf}\n\n{p}".strip() if buf else p
        if len(candidate) <= chunk_size:
            buf = candidate
            continue

        if buf:
            chunks.append(buf)

        if len(p) <= chunk_size:
            buf = p
        else:
            start = 0
            while start < len(p):
                end = start + chunk_size
                part = p[start:end].strip()
                if part:
                    chunks.append(part)
                if end >= len(p):
                    break
                start = max(end - overlap, start + 1)
            buf = ""

    if buf:
        chunks.append(buf)

    return chunks


def build_normative_corpus(norms_dir: Path) -> list[dict]:
    corpus: list[dict] = []

    pdf_files = sorted(
    [p for p in norms_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"]
    )
    for pdf_path in pdf_files:
        raw_text = _extract_norm_text(pdf_path)
        if not raw_text:
            print(f"[NORM_SKIP_EMPTY] {pdf_path.name}")
            continue

        doc_title = _guess_doc_title(pdf_path.name)
        chunks = _chunk_text(raw_text)

        for i, chunk in enumerate(chunks, start=1):
            corpus.append(
                {
                    "chunk_id": f"{pdf_path.stem.lower()}_{i:05d}",
                    "doc_title": doc_title,
                    "source_file": pdf_path.name,
                    "section_hint": _extract_section_hint(chunk, doc_title),
                    "text": chunk,
                }
            )

    return corpus


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--norms_dir", required=True, help="Папка с нормативными PDF")
    ap.add_argument("--out_json", required=True, help="Куда сохранить normative_corpus.json")
    args = ap.parse_args()

    norms_dir = _resolve_norms_dir(args.norms_dir)
    # pdf_count = len(list(norms_dir.glob("*.pdf"))) + len(list(norms_dir.glob("*.PDF")))
    

    print(f"[NORMS_DIR_RAW] {args.norms_dir}")
    print(f"[NORMS_DIR_RESOLVED] {norms_dir}")
    print(f"[NORMS_DIR_EXISTS] {norms_dir.exists()}")

    pdf_files = sorted(
        [p for p in norms_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"]
    )

    print(f"[NORMS_PDF_FILES] {len(pdf_files)}")
    for p in pdf_files[:20]:
        print(f"  - {p.name}")

    corpus = build_normative_corpus(norms_dir)
    _save_json(Path(args.out_json), corpus)

    print(f"norms_pdf_count: {len(pdf_files)}")
    print(f"normative_corpus: {len(corpus)}")


if __name__ == "__main__":
    main()