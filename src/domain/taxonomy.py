from __future__ import annotations
from pathlib import Path
import yaml

def load_taxonomy(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))
