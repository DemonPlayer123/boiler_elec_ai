from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class ShareError(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


class ShareService:
    """Capability-style invitations for independent project sessions.

    An invitation never exposes an existing project. Every successful use creates
    a new isolated project and receives its own project access key.
    """

    def __init__(self, projects_root: str | Path) -> None:
        self.projects_root = Path(projects_root).resolve()
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self.path = self.projects_root / "_share_invites.json"
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"invites": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"invites": {}}
        if not isinstance(payload, dict) or not isinstance(payload.get("invites"), dict):
            return {"invites": {}}
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "token": row["token"],
            "label": row.get("label") or "Совместный запуск Boiler Elec AI",
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "max_uses": row["max_uses"],
            "uses": row.get("uses", 0),
            "active": bool(row.get("active", True)),
        }

    def create(self, *, label: str | None = None, expires_hours: int = 168, max_uses: int = 25) -> dict[str, Any]:
        expires_hours = max(1, min(int(expires_hours), 24 * 90))
        max_uses = max(1, min(int(max_uses), 1000))
        now = _utc_now()
        token = secrets.token_urlsafe(24)
        row = {
            "token": token,
            "label": (label or "Совместный запуск Boiler Elec AI").strip()[:160],
            "created_at": _iso(now),
            "expires_at": _iso(now + timedelta(hours=expires_hours)),
            "max_uses": max_uses,
            "uses": 0,
            "active": True,
        }
        with self._lock:
            payload = self._load()
            payload["invites"][token] = row
            self._save(payload)
        return self._public(row)

    def resolve(self, token: str) -> dict[str, Any]:
        token = str(token or "").strip()
        with self._lock:
            payload = self._load()
            row = payload["invites"].get(token)
            if not isinstance(row, dict):
                raise ShareError("Ссылка-приглашение не найдена")
            if not row.get("active", True):
                raise ShareError("Ссылка-приглашение отключена")
            expires_at = datetime.fromisoformat(row["expires_at"])
            if _utc_now() >= expires_at:
                raise ShareError("Срок действия ссылки-приглашения истёк")
            if int(row.get("uses", 0)) >= int(row.get("max_uses", 1)):
                raise ShareError("Лимит запусков по ссылке исчерпан")
            return self._public(row)

    def consume(self, token: str) -> dict[str, Any]:
        token = str(token or "").strip()
        with self._lock:
            payload = self._load()
            row = payload["invites"].get(token)
            if not isinstance(row, dict):
                raise ShareError("Ссылка-приглашение не найдена")
            if not row.get("active", True):
                raise ShareError("Ссылка-приглашение отключена")
            expires_at = datetime.fromisoformat(row["expires_at"])
            if _utc_now() >= expires_at:
                raise ShareError("Срок действия ссылки-приглашения истёк")
            if int(row.get("uses", 0)) >= int(row.get("max_uses", 1)):
                raise ShareError("Лимит запусков по ссылке исчерпан")
            row["uses"] = int(row.get("uses", 0)) + 1
            self._save(payload)
            return self._public(row)
