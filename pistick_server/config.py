"""Private configuration for the PiStick server."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading


class ConfigStore:
    """Keep secrets in the server data directory, never in browser storage."""

    DEFAULT_PORT = 8787

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.data = self._load()

    def _load(self) -> dict[str, object]:
        source_exists = self.path.is_file()
        try:
            # json.loads(bytes) accepts both ordinary UTF-8 and an older
            # Windows PowerShell UTF-8 BOM without a second decoded copy.
            raw = json.loads(self.path.read_bytes())
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
        configured_lan = raw.get("lan_enabled")
        if configured_lan is None:
            configured_lan = os.getenv("PISTICK_DEFAULT_LAN", "").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        elif isinstance(configured_lan, str):
            configured_lan = configured_lan.strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        normalized = {
            "tmdb_read_token": token,
            "port": port,
            "shutdown_token": str(raw.get("shutdown_token") or os.urandom(32).hex()),
            "lan_enabled": bool(configured_lan),
        }
        if not source_exists or raw != normalized:
            self._write(normalized)
        else:
            try:
                if self.path.stat().st_mode & 0o777 != 0o600:
                    os.chmod(self.path, 0o600)
            except OSError:
                pass
        return normalized

    @staticmethod
    def _serialize(data: dict[str, object]) -> str:
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    def _write(self, data: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
                destination.write(self._serialize(data))
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

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

    @property
    def lan_enabled(self) -> bool:
        with self._lock:
            return bool(self.data.get("lan_enabled", False))

    def set_tmdb_read_token(self, token: str) -> None:
        cleaned = str(token or "").strip()
        if not self.usable_token(cleaned):
            raise ValueError("Paste the long TMDB API Read Access Token.")
        with self._lock:
            self.data["tmdb_read_token"] = cleaned
            self._write(self.data)

    def set_lan_enabled(self, enabled: bool) -> None:
        with self._lock:
            self.data["lan_enabled"] = bool(enabled)
            self._write(self.data)

    def public_payload(self) -> dict[str, object]:
        with self._lock:
            return {
                "tmdb_configured": self.usable_token(
                    str(self.data.get("tmdb_read_token") or "")
                ),
                "port": int(self.data.get("port") or self.DEFAULT_PORT),
                "lan_enabled": bool(self.data.get("lan_enabled", False)),
            }
