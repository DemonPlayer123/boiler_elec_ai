from __future__ import annotations
import re

def norm_spaces(s: str) -> str:
    s = (s or "").replace("\u00A0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()

def norm_tag(tag: str) -> str:
    if tag is None:
        return ""
    t = str(tag).strip()
    t = t.replace("K", "К").replace("k", "К")
    t = re.sub(r"\s+", "", t)
    t = t.replace("К-", "К")
    # normalize GG
    t = t.replace("ГГ", "ГГ.")
    t = t.replace("ГГ..", "ГГ.")
    # fix like "ГГ.1."
    t = re.sub(r"(ГГ\.\d+)\.", r"\1", t, flags=re.IGNORECASE)
    return t

def extract_tag_from_filename(filename: str) -> str | None:
    """
    Extract tag like 'К8' or 'К8.1' from filename.
    Works with underscores.
    """
    s = filename.replace("K", "К").replace("k", "К")
    m = re.search(r"(?i)К\s*\.?\s*(\d{1,3})(?:\s*\.\s*(\d{1,2}))?", s)
    if not m:
        return None
    base = f"К{m.group(1)}"
    if m.group(2):
        return f"{base}.{m.group(2)}"
    return base

def to_float(x: str) -> float:
    return float(str(x).replace(",", "."))
