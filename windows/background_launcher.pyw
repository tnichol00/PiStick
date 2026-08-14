"""Launch the installed PiStick server without opening a console window."""

from __future__ import annotations

import os
from pathlib import Path
import sys


install_root = Path(os.getenv("PISTICK_SERVER_ROOT") or Path(__file__).resolve().parents[2])
app_dir = install_root / "app"
data_dir = install_root / "data"
os.chdir(app_dir)
sys.path.insert(0, str(app_dir))

from pistick_server.app import main  # noqa: E402


raise SystemExit(main(["--host", "127.0.0.1", "--data-dir", str(data_dir)]))
