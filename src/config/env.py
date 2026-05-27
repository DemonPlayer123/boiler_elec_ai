# src/config/env.py
from __future__ import annotations

import os
from pathlib import Path


def load_project_env(env_path: str | Path = ".env") -> None:
    """
    Минимальная загрузка .env без внешней зависимости python-dotenv.

    Формат строк:
      OPENAI_API_KEY=...
      XAI_API_KEY=...
      GROK_CRITIC_ENABLED=1

    Уже заданные переменные окружения не перезатираются.
    Файл .env нельзя коммитить в git.
    """
    path = Path(env_path)

    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value
