from __future__ import annotations
from pathlib import Path
import fitz  # PyMuPDF
import re

def extract_text_pymupdf(pdf_path: Path, max_pages: int | None = None) -> str:
    doc = fitz.open(pdf_path)
    n = doc.page_count if max_pages is None else min(max_pages, doc.page_count)
    chunks = []
    for i in range(n):
        page = doc.load_page(i)
        t = page.get_text("text") or ""
        t = t.replace("\r\n", "\n").replace("\r", "\n")
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        chunks.append(t.strip())
    doc.close()
    return "\n\n".join(chunks).strip()
