"""Local-only bridge to the root-owned Raspberry Pi system helper."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any, Optional


class SystemControlError(RuntimeError):
    """A safe error that can be shown on the HDMI settings screen."""


class SystemController:
    """Call the narrowly-scoped helper installed by the Pi appliance installer."""

    ACTION_TIMEOUTS = {
        "status": 25,
        "wifi-scan": 35,
        "wifi-connect": 55,
        "bluetooth-scan": 35,
        "bluetooth-pair": 70,
    }

    def __init__(self, helper_path: Optional[Path] = None):
        configured = os.getenv("PISTICK_SYSTEM_HELPER", "").strip()
        self.helper_path = Path(
            helper_path or configured or "/usr/local/libexec/pistick-system-helper"
        )

    @property
    def available(self) -> bool:
        return self.helper_path.is_file() and os.access(self.helper_path, os.X_OK)

    def run(self, action: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        if action not in self.ACTION_TIMEOUTS:
            raise SystemControlError("That system action is not supported.")
        if not self.available:
            raise SystemControlError("System controls are not installed on this device.")
        encoded = json.dumps(payload or {}, separators=(",", ":"))
        try:
            completed = subprocess.run(
                ["sudo", "-n", str(self.helper_path), action],
                input=encoded,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.ACTION_TIMEOUTS[action],
                check=False,
                env={
                    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C",
                },
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SystemControlError("The Pi system helper did not respond.") from exc
        try:
            result = json.loads(completed.stdout or "{}")
        except (TypeError, ValueError) as exc:
            raise SystemControlError("The Pi system helper returned an invalid response.") from exc
        if not isinstance(result, dict):
            raise SystemControlError("The Pi system helper returned an invalid response.")
        if completed.returncode != 0 or not result.get("ok", False):
            message = str(result.get("error") or "The requested system action failed.")
            raise SystemControlError(message)
        result.pop("ok", None)
        return result
