"""Private configuration for the local-network server."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import threading
from typing import Any


class ConfigStore:
    """Keep secrets in the server data directory, never in browser storage."""

    DEFAULT_PORT = 8787

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            # Windows PowerShell 5.1 may have written an older config with a
            # UTF-8 BOM.  utf-8-sig accepts both BOM and ordinary UTF-8 files.
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        token = str(
            os.getenv("TMDB_READ_TOKEN")
            or raw.get("tmdb_read_token")
            or raw.get("tmdb_token")
            or ""
        ).strip()
        try:
            port = int(os.getenv("PISTICK_SERVER_PORT") or raw.get("port") or self.DEFAULT_PORT)
        except (TypeError, ValueError):
            port = self.DEFAULT_PORT
        if not 1024 <= port <= 65535:
            port = self.DEFAULT_PORT
        normalized = {
            "tmdb_read_token": token,
            "port": port,
            "shutdown_token": str(raw.get("shutdown_token") or secrets.token_urlsafe(32)),
        }
        self._write(normalized)
        return normalized

    @staticmethod
    def _serialize(data: dict[str, Any]) -> str:
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(self._serialize(data), encoding="utf-8")
        os.replace(temporary, self.path)

    @staticmethod
    def usable_token(token: str) -> bool:
        cleaned = str(token or "").strip()
        return bool(
            len(cleaned) >= 24
            and "PASTE_YOUR" not in cleaned.upper()
            and "KEEP_THE_QUOTES" not in cleaned.upper()
        )

    @property
    def tmdb_read_token(self) -> str:
        with self._lock:
            return str(self.data.get("tmdb_read_token") or "")

    @property
    def token_configured(self) -> bool:
        return self.usable_token(self.tmdb_read_token)

    @property
    def port(self) -> int:
        with self._lock:
            return int(self.data.get("port") or self.DEFAULT_PORT)

    @property
    def shutdown_token(self) -> str:
        with self._lock:
            return str(self.data["shutdown_token"])

    def set_tmdb_read_token(self, token: str) -> None:
        cleaned = str(token or "").strip()
        if not self.usable_token(cleaned):
            raise ValueError("Paste the long TMDB API Read Access Token.")
        with self._lock:
            self.data["tmdb_read_token"] = cleaned
            self._write(self.data)

    def public_payload(self) -> dict[str, Any]:
        return {
            "tmdb_configured": self.token_configured,
            "port": self.port,
        }
