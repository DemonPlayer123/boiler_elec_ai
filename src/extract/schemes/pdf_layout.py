from __future__ import annotations
from pathlib import Path
from typing import List, Tuple
import re
import fitz

def extract_lines_by_blocks(pdf_path: Path, max_pages: int | None = None) -> List[str]:
    doc = fitz.open(pdf_path)
    n = doc.page_count if max_pages is None else min(max_pages, doc.page_count)

    out: List[str] = []
    for i in range(n):
        page = doc.load_page(i)
        d = page.get_text("dict")
        items: List[Tuple[float, float, str]] = []

        for b in d.get("blocks", []):
            for ln in b.get("lines", []):
                for sp in ln.get("spans", []):
                    txt = (sp.get("text") or "").replace("\n", " ").strip()
                    if not txt:
                        continue
                    x0, y0, x1, y1 = sp.get("bbox", (0, 0, 0, 0))
                    items.append((y0, x0, txt))

        items.sort(key=lambda t: (t[0], t[1]))

        cur_y = None
        cur = []

        def flush():
            nonlocal cur
            if not cur:
                return
            s = " ".join(cur)
            s = re.sub(r"[ \t]+", " ", s).strip()
            if s:
                out.append(s)
            cur = []

        for y, x, txt in items:
            if cur_y is None:
                cur_y = y
                cur = [txt]
                continue
            if abs(y - cur_y) > 4.5:  # порог можно тюнить
                flush()
                cur_y = y
                cur = [txt]
            else:
                cur.append(txt)

        flush()

    doc.close()
    return out
