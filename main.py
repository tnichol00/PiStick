import hashlib
import json
import math
import os
import sys
import threading
import time
import uuid
import webbrowser
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import requests
from requests.adapters import HTTPAdapter

from adblock import (
    AdBlockSettings,
    build_playback_adblock_script,
    is_blocked_ad_url,
    load_adblock_settings,
    same_origin,
)
from playback_api import PlaybackAPIError, getmovie, getshow

# Keep Chromium lean before QtWebEngine is imported. PiStick uses one embedded
# player, so extra renderer processes, extension services, update checks, and a
# large browser cache only waste RAM on a television appliance.
_CHROMIUM_FLAGS = (
    "--renderer-process-limit=1",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-domain-reliability",
    "--disable-extensions",
    "--disable-sync",
    "--disable-translate",
    "--no-first-run",
    "--disk-cache-size=25165824",
    "--media-cache-size=33554432",
)
_existing_chromium_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
for _flag in _CHROMIUM_FLAGS:
    _flag_name = _flag.split("=", 1)[0]
    if _flag_name not in _existing_chromium_flags:
        _existing_chromium_flags = f"{_existing_chromium_flags} {_flag}".strip()
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = _existing_chromium_flags
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

# PySide6 remains the preferred desktop binding. Raspberry Pi OS images for the
# original ARMv6 Pi Zero commonly provide PyQt5 from apt instead, so the exact
# same UI can run there without maintaining a second application.
QT_BINDING = "PySide6"
try:
    from PySide6.QtCore import (
        QEvent,
        QEasingCurve,
        QObject,
        QPoint,
        QPropertyAnimation,
        QRect,
        QRunnable,
        QSize,
        Slot,
        Qt,
        QThreadPool,
        QTimer,
        Signal,
        QUrl,
    )
    from PySide6.QtGui import QFont, QImage, QKeySequence, QPixmap, QPixmapCache, QShortcut
    from PySide6.QtWidgets import (
        QAbstractButton,
        QApplication,
        QDialog,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QScrollArea,
        QStackedWidget,
        QVBoxLayout,
        QWidget,
    )
except (ImportError, OSError):
    QT_BINDING = "PyQt5"
    from PyQt5.QtCore import (  # type: ignore[no-redef]
        QEvent,
        QEasingCurve,
        QObject,
        QPoint,
        QPropertyAnimation,
        QRect,
        QRunnable,
        QSize,
        QThreadPool,
        QTimer,
        QUrl,
        Qt,
        pyqtSignal as Signal,
        pyqtSlot as Slot,
    )
    from PyQt5.QtGui import QFont, QImage, QKeySequence, QPixmap, QPixmapCache  # type: ignore[no-redef]
    from PyQt5.QtWidgets import (  # type: ignore[no-redef]
        QAbstractButton,
        QApplication,
        QDialog,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QScrollArea,
        QShortcut,
        QStackedWidget,
        QVBoxLayout,
        QWidget,
    )

pygame = None
_PYGAME_IMPORT_ATTEMPTED = False


def _controller_device_may_exist() -> bool:
    """Avoid loading SDL on Linux until a joystick device is actually present."""
    if os.getenv("PISTICK_EAGER_CONTROLLER", "").strip().lower() in {"1", "true", "yes"}:
        return True
    if not sys.platform.startswith("linux"):
        return True
    try:
        input_root = Path("/dev/input")
        return any(input_root.glob("js*")) or any(
            input_root.glob("by-id/*-joystick*")
        )
    except OSError:
        return False


def _load_pygame_module():
    global pygame, _PYGAME_IMPORT_ATTEMPTED
    if pygame is not None:
        return pygame
    if _PYGAME_IMPORT_ATTEMPTED or not _controller_device_may_exist():
        return None
    _PYGAME_IMPORT_ATTEMPTED = True
    try:
        import importlib

        pygame = importlib.import_module("pygame")
    except Exception:
        pygame = None
    return pygame


APP_NAME = "PiStick"
APP_VERSION = "3.3.0-playback-adblock"
TMDB_CACHE_SCHEMA = "compact-v1"
TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"


def _runtime_file(env_name: str, filename: str) -> Path:
    """Allow appliance installs to keep private data outside release folders."""
    configured = os.getenv(env_name, "").strip()
    return Path(configured).expanduser() if configured else Path(__file__).with_name(filename)


CONFIG_PATH = _runtime_file("PISTICK_CONFIG_PATH", "config.json")
STATE_PATH = _runtime_file("PISTICK_STATE_PATH", "pistick_state.json")
CACHE_ROOT = Path(
    os.getenv("PISTICK_CACHE_DIR", "").strip()
    or Path(os.getenv("XDG_CACHE_HOME", "").strip() or (Path.home() / ".cache")) / "pistick"
)


def _system_memory_mb() -> int:
    """Read total RAM without adding a platform-specific dependency."""
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return max(1, int(line.split()[1]) // 1024)
    except (OSError, ValueError, IndexError):
        pass
    return 4096


@dataclass(frozen=True)
class RuntimeTuning:
    low_memory: bool
    api_threads: int
    image_threads: int
    image_memory_cache_bytes: int
    image_prefetch_margin: int
    image_release_margin: int
    web_cache_bytes: int


def _runtime_tuning() -> RuntimeTuning:
    override = os.getenv("PISTICK_LOW_MEMORY", "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        low_memory = True
    elif override in {"0", "false", "no", "off"}:
        low_memory = False
    else:
        low_memory = (os.cpu_count() or 1) <= 2 or _system_memory_mb() <= 768
    if low_memory:
        return RuntimeTuning(True, 2, 2, 10 * 1024 * 1024, 420, 950, 24 * 1024 * 1024)
    cpu_count = max(1, os.cpu_count() or 1)
    return RuntimeTuning(
        False,
        min(6, max(3, cpu_count // 2)),
        min(4, max(2, cpu_count // 2)),
        48 * 1024 * 1024,
        900,
        3000,
        96 * 1024 * 1024,
    )


RUNTIME = _runtime_tuning()

# YouTube requires embedded desktop/WebView clients to identify themselves with
# an HTTPS Referer. Keep this stable if PiStick is packaged later.
YOUTUBE_APP_ID = "com.layeredkingdom.pistick"
YOUTUBE_REFERER = f"https://{YOUTUBE_APP_ID}/"


def build_youtube_embed_html(video_key: str) -> str:
    """Create a controllable YouTube player with an identified WebView origin."""
    origin = YOUTUBE_REFERER.rstrip("/")
    safe_key = json.dumps(str(video_key))
    safe_origin = json.dumps(origin)
    safe_referrer = json.dumps(YOUTUBE_REFERER)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="strict-origin-when-cross-origin">
  <style>
    html, body {{ width:100%; height:100%; margin:0; background:#0b0b0d; overflow:hidden; }}
    #player, iframe {{ width:100%; height:100%; border:0; display:block; }}
  </style>
</head>
<body>
  <div id="player"></div>
  <script>
    let player = null;
    let playRequested = false;

    function onYouTubeIframeAPIReady() {{
      player = new YT.Player('player', {{
        videoId: {safe_key},
        host: 'https://www.youtube.com',
        playerVars: {{
          rel: 0,
          playsinline: 1,
          enablejsapi: 1,
          origin: {safe_origin},
          widget_referrer: {safe_referrer}
        }},
        events: {{
          onReady: function(event) {{
            if (playRequested) event.target.playVideo();
          }}
        }}
      }});
    }}

    window.pistickPlayTrailer = function() {{
      playRequested = true;
      if (!player || typeof player.playVideo !== 'function') return false;
      player.playVideo();
      return true;
    }};

    window.pistickPauseTrailer = function() {{
      playRequested = false;
      if (!player || typeof player.pauseVideo !== 'function') return false;
      player.pauseVideo();
      return true;
    }};

    const api = document.createElement('script');
    api.src = 'https://www.youtube.com/iframe_api';
    document.head.appendChild(api);
  </script>
</body>
</html>"""


# Keep background QRunnables alive until their queued GUI-thread callbacks have
# been delivered. Releasing them inside run() can delete the signal owner before
# Qt handles the queued result, which is especially easy to hit with search.
_ACTIVE_WORKERS: set[QRunnable] = set()
_WORKER_REAPER: Optional["_WorkerReaper"] = None


def _start_worker(pool: QThreadPool, worker: QRunnable) -> None:
    global _WORKER_REAPER
    if _WORKER_REAPER is None:
        _WORKER_REAPER = _WorkerReaper()
    worker.setAutoDelete(False)
    worker.signals.finished.connect(_WORKER_REAPER.release)
    _ACTIVE_WORKERS.add(worker)
    pool.start(worker)


def _release_worker(worker: QRunnable) -> None:
    _ACTIVE_WORKERS.discard(worker)


@dataclass
class AppConfig:
    tmdb_read_token: str = ""

    @classmethod
    def load(cls) -> "AppConfig":
        token = os.getenv("TMDB_READ_TOKEN", "").strip()
        if token:
            return cls(tmdb_read_token=token)

        if CONFIG_PATH.exists():
            try:
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                token = str(raw.get("tmdb_token") or raw.get("tmdb_read_token") or "").strip()
                return cls(tmdb_read_token=token)
            except Exception:
                pass
        return cls()


class WatchStateStore:
    """Local per-profile watch history for the embedded playback experience."""

    DEFAULT_AVATARS = ["red", "blue", "green", "purple", "orange", "teal"]

    def __init__(self, path: Path = STATE_PATH):
        self.path = path
        self._last_saved_payload: Optional[str] = None
        self.data: dict[str, Any] = self._load()

    def _default_data(self) -> dict[str, Any]:
        profile_id = "profile-1"
        return {
            "active_profile": None,
            "profiles": [
                {"id": profile_id, "name": "Profile 1", "avatar": "red"},
            ],
            "watch_state": {profile_id: {}},
        }

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("profiles"), list):
                    raw.setdefault("watch_state", {})
                    raw.setdefault("active_profile", None)
                    for profile in raw.get("profiles", []):
                        raw["watch_state"].setdefault(profile.get("id", ""), {})
                    self._last_saved_payload = self._serialize(raw)
                    return raw
            except Exception:
                pass
        data = self._default_data()
        self._save_data(data)
        return data

    @staticmethod
    def _serialize(data: dict[str, Any]) -> str:
        # Compact JSON reduces SD-card writes while preserving the state schema.
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    def _save_data(self, data: dict[str, Any]) -> None:
        payload = self._serialize(data)
        if payload == self._last_saved_payload and self.path.exists():
            return
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, self.path)
            self._last_saved_payload = payload
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def save(self) -> None:
        self._save_data(self.data)

    def profiles(self) -> list[dict[str, Any]]:
        return list(self.data.get("profiles", []))

    def profile(self, profile_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not profile_id:
            return None
        return next((p for p in self.profiles() if p.get("id") == profile_id), None)

    def set_active_profile(self, profile_id: str) -> None:
        self.data["active_profile"] = profile_id
        self.data.setdefault("watch_state", {}).setdefault(profile_id, {})
        self.save()

    def add_profile(self, name: str) -> dict[str, Any]:
        profiles = self.data.setdefault("profiles", [])
        profile = {
            "id": f"profile-{uuid.uuid4().hex[:10]}",
            "name": name.strip() or f"Profile {len(profiles) + 1}",
            "avatar": self.DEFAULT_AVATARS[len(profiles) % len(self.DEFAULT_AVATARS)],
        }
        profiles.append(profile)
        self.data.setdefault("watch_state", {})[profile["id"]] = {}
        self.save()
        return profile

    def rename_profile(self, profile_id: str, name: str) -> None:
        profile = self.profile(profile_id)
        if profile and name.strip():
            profile["name"] = name.strip()
            self.save()

    def delete_profile(self, profile_id: str) -> bool:
        profiles = self.data.get("profiles", [])
        if len(profiles) <= 1:
            return False
        self.data["profiles"] = [p for p in profiles if p.get("id") != profile_id]
        self.data.setdefault("watch_state", {}).pop(profile_id, None)
        if self.data.get("active_profile") == profile_id:
            self.data["active_profile"] = None
        self.save()
        return True

    @staticmethod
    def media_key(media: dict[str, Any]) -> str:
        return f"{media.get('media_type', 'movie')}:{media.get('id', '')}"

    @staticmethod
    def snapshot(media: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "id",
            "media_type",
            "title",
            "name",
            "year",
            "release_date",
            "first_air_date",
            "poster_path",
            "backdrop_path",
            "overview",
            "vote_average",
            "number_of_seasons",
            "seasons",
        )
        return {key: media.get(key) for key in keys if media.get(key) is not None}

    def _profile_history(self, profile_id: Optional[str]) -> dict[str, Any]:
        if not profile_id:
            return {}
        return self.data.setdefault("watch_state", {}).setdefault(profile_id, {})

    def entry(self, profile_id: Optional[str], media: dict[str, Any]) -> Optional[dict[str, Any]]:
        return self._profile_history(profile_id).get(self.media_key(media))

    @staticmethod
    def episode_key(season_number: int, episode_number: int) -> str:
        return f"{int(season_number)}:{int(episode_number)}"

    @staticmethod
    def available_seasons(media: dict[str, Any]) -> list[dict[str, Any]]:
        """Return aired/known seasons, with regular seasons before specials."""
        seasons = [
            dict(season)
            for season in media.get("seasons", [])
            if isinstance(season, dict)
            and int(season.get("episode_count", 0) or 0) > 0
        ]
        regular = sorted(
            (season for season in seasons if int(season.get("season_number", 0) or 0) > 0),
            key=lambda season: int(season.get("season_number", 0) or 0),
        )
        specials = sorted(
            (season for season in seasons if int(season.get("season_number", 0) or 0) == 0),
            key=lambda season: str(season.get("name", "")),
        )
        return regular + specials

    @classmethod
    def next_episode_position(
        cls,
        media: dict[str, Any],
        season_number: int,
        episode_number: int,
    ) -> Optional[tuple[int, int]]:
        seasons = cls.available_seasons(media)
        season_numbers = [int(season.get("season_number", 0) or 0) for season in seasons]
        counts = {
            int(season.get("season_number", 0) or 0): int(season.get("episode_count", 0) or 0)
            for season in seasons
        }
        current_count = counts.get(int(season_number), 0)
        if current_count and int(episode_number) < current_count:
            return int(season_number), int(episode_number) + 1

        try:
            index = season_numbers.index(int(season_number))
        except ValueError:
            return None
        for next_season in season_numbers[index + 1 :]:
            if next_season > 0 and counts.get(next_season, 0) > 0:
                return next_season, 1
        return None

    def episode_entries(
        self,
        profile_id: Optional[str],
        media: dict[str, Any],
    ) -> dict[str, Any]:
        entry = self.entry(profile_id, media) or {}
        episodes = entry.get("episodes", {})
        return episodes if isinstance(episodes, dict) else {}

    def episode_entry(
        self,
        profile_id: Optional[str],
        media: dict[str, Any],
        season_number: int,
        episode_number: int,
    ) -> Optional[dict[str, Any]]:
        return self.episode_entries(profile_id, media).get(
            self.episode_key(season_number, episode_number)
        )

    def latest_episode_entry(
        self,
        profile_id: Optional[str],
        media: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        episodes = list(self.episode_entries(profile_id, media).values())
        episodes = [episode for episode in episodes if isinstance(episode, dict)]
        if not episodes:
            return None
        return max(episodes, key=lambda episode: float(episode.get("updated_at", 0.0) or 0.0))

    def resume_episode(
        self,
        profile_id: Optional[str],
        media: dict[str, Any],
    ) -> tuple[int, int]:
        """Pick the latest unfinished episode, or the episode after a finished one."""
        latest = self.latest_episode_entry(profile_id, media)
        if latest is None:
            seasons = self.available_seasons(media)
            regular = [
                int(season.get("season_number", 0) or 0)
                for season in seasons
                if int(season.get("season_number", 0) or 0) > 0
            ]
            return (regular[0] if regular else 1), 1

        season_number = int(latest.get("season_number", 1) or 1)
        episode_number = int(latest.get("episode_number", 1) or 1)
        if latest.get("status") == "finished":
            next_position = self.next_episode_position(media, season_number, episode_number)
            if next_position is not None:
                return next_position
        return season_number, episode_number

    @staticmethod
    def episode_snapshot(episode: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "id",
            "name",
            "overview",
            "air_date",
            "still_path",
            "runtime",
            "season_number",
            "episode_number",
        )
        return {key: episode.get(key) for key in keys if episode.get(key) is not None}

    def _write_episode_progress(
        self,
        profile_id: Optional[str],
        media: dict[str, Any],
        episode: dict[str, Any],
        progress: float,
    ) -> None:
        if not profile_id:
            return
        season_number = int(episode.get("season_number", 1) or 1)
        episode_number = int(episode.get("episode_number", 1) or 1)
        progress = max(0.0, min(1.0, float(progress)))
        now = time.time()
        history = self._profile_history(profile_id)
        key = self.media_key(media)
        show_entry = dict(history.get(key, {}))
        episodes = dict(show_entry.get("episodes", {}))
        episode_data = {
            "status": "finished" if progress >= 0.98 else "in_progress",
            "progress": 1.0 if progress >= 0.98 else progress,
            "updated_at": now,
            "season_number": season_number,
            "episode_number": episode_number,
            "episode": self.episode_snapshot(episode),
        }
        episodes[self.episode_key(season_number, episode_number)] = episode_data

        next_position = self.next_episode_position(media, season_number, episode_number)
        show_finished = episode_data["status"] == "finished" and next_position is None
        history[key] = {
            "status": "finished" if show_finished else "in_progress",
            "progress": (
                1.0
                if show_finished
                else 0.03
                if episode_data["status"] == "finished"
                else max(0.03, min(0.97, episode_data["progress"]))
            ),
            "updated_at": now,
            "media": self.snapshot(media) or show_entry.get("media", {}),
            "episodes": episodes,
            "last_episode": {
                "season_number": season_number,
                "episode_number": episode_number,
            },
        }
        self.save()

    def mark_episode_started(
        self,
        profile_id: Optional[str],
        media: dict[str, Any],
        episode: dict[str, Any],
    ) -> None:
        previous = self.episode_entry(
            profile_id,
            media,
            int(episode.get("season_number", 1) or 1),
            int(episode.get("episode_number", 1) or 1),
        ) or {}
        progress = float(previous.get("progress", 0.0) or 0.0)
        if previous.get("status") == "finished":
            progress = 0.03
        self._write_episode_progress(profile_id, media, episode, max(0.03, progress))

    def mark_episode_finished(
        self,
        profile_id: Optional[str],
        media: dict[str, Any],
        episode: dict[str, Any],
    ) -> None:
        self._write_episode_progress(profile_id, media, episode, 1.0)

    def set_episode_progress(
        self,
        profile_id: Optional[str],
        media: dict[str, Any],
        episode: dict[str, Any],
        progress: float,
    ) -> None:
        """Store progress for one TV episode when a player reports it."""
        self._write_episode_progress(profile_id, media, episode, progress)

    def set_episode_position(
        self,
        profile_id: Optional[str],
        media: dict[str, Any],
        episode: dict[str, Any],
        position_seconds: float,
        duration_seconds: float,
    ) -> None:
        duration = max(0.0, float(duration_seconds))
        position = max(0.0, min(float(position_seconds), duration or float(position_seconds)))
        progress = position / duration if duration > 0 else 0.03
        self._write_episode_progress(profile_id, media, episode, progress)
        entry = self.episode_entry(
            profile_id,
            media,
            int(episode.get("season_number", 1) or 1),
            int(episode.get("episode_number", 1) or 1),
        )
        if entry is not None:
            entry["position_seconds"] = round(position, 1)
            entry["duration_seconds"] = round(duration, 1)
            self.save()

    def mark_started(self, profile_id: Optional[str], media: dict[str, Any]) -> None:
        if not profile_id:
            return
        history = self._profile_history(profile_id)
        key = self.media_key(media)
        previous = history.get(key, {})
        progress = float(previous.get("progress", 0.0) or 0.0)
        # Until the embed service exposes progress, 3% simply means "started".
        if previous.get("status") == "finished":
            progress = 0.03
        else:
            progress = max(progress, 0.03)
        history[key] = {
            "status": "in_progress",
            "progress": min(progress, 0.97),
            "updated_at": time.time(),
            "media": self.snapshot(media),
        }
        self.save()

    def mark_finished(self, profile_id: Optional[str], media: dict[str, Any]) -> None:
        if not profile_id:
            return
        history = self._profile_history(profile_id)
        key = self.media_key(media)
        previous = history.get(key, {})
        history[key] = {
            "status": "finished",
            "progress": 1.0,
            "updated_at": time.time(),
            "media": self.snapshot(media) or previous.get("media", {}),
        }
        self.save()

    def mark_unwatched(self, profile_id: Optional[str], media: dict[str, Any]) -> None:
        if not profile_id:
            return
        self._profile_history(profile_id).pop(self.media_key(media), None)
        self.save()

    def set_progress(self, profile_id: Optional[str], media: dict[str, Any], progress: float) -> None:
        """Store a 0.0-1.0 playback fraction when a player reports one."""
        if not profile_id:
            return
        progress = max(0.0, min(1.0, float(progress)))
        if progress >= 0.98:
            self.mark_finished(profile_id, media)
            return
        history = self._profile_history(profile_id)
        history[self.media_key(media)] = {
            "status": "in_progress",
            "progress": progress,
            "updated_at": time.time(),
            "media": self.snapshot(media),
        }
        self.save()

    def set_position(
        self,
        profile_id: Optional[str],
        media: dict[str, Any],
        position_seconds: float,
        duration_seconds: float,
    ) -> None:
        if not profile_id:
            return
        duration = max(0.0, float(duration_seconds))
        position = max(0.0, min(float(position_seconds), duration or float(position_seconds)))
        progress = position / duration if duration > 0 else 0.03
        self.set_progress(profile_id, media, progress)
        entry = self.entry(profile_id, media)
        if entry is not None:
            entry["position_seconds"] = round(position, 1)
            entry["duration_seconds"] = round(duration, 1)
            self.save()

    def continue_watching(self, profile_id: Optional[str]) -> list[dict[str, Any]]:
        entries = [
            entry
            for entry in self._profile_history(profile_id).values()
            if entry.get("status") == "in_progress" and entry.get("media")
        ]
        entries.sort(key=lambda x: float(x.get("updated_at", 0.0)), reverse=True)
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in entries:
            media = dict(entry.get("media", {}))
            key = self.media_key(media)
            if key in seen:
                continue
            seen.add(key)
            items.append(media)
        return items


class _JsonResponseCache:
    """Small RAM + disk cache for TMDB responses.

    TMDB lists and metadata change slowly compared with navigation speed. The
    cache avoids re-downloading and reparsing the same JSON every time a profile
    changes or a title is reopened, and stale data remains a safe offline
    fallback when the network briefly drops.
    """

    def __init__(self, root: Path, max_memory_entries: int = 64):
        self.root = root
        self.max_memory_entries = max(8, int(max_memory_entries))
        self._memory: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = threading.RLock()
        self._writes = 0

    def get(self, key: str, allow_stale: bool = False) -> Optional[dict[str, Any]]:
        now = time.time()
        with self._lock:
            memory_entry = self._memory.get(key)
            if memory_entry is not None:
                expires_at, data = memory_entry
                if allow_stale or expires_at > now:
                    self._memory.move_to_end(key)
                    return data

        path = self.root / f"{key}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            expires_at = float(payload.get("expires_at", 0.0) or 0.0)
            data = payload.get("data")
            if not isinstance(data, dict) or (not allow_stale and expires_at <= now):
                return None
        except (OSError, ValueError, TypeError):
            return None

        with self._lock:
            self._memory[key] = (expires_at, data)
            self._memory.move_to_end(key)
            self._trim_memory()
        return data

    def put(self, key: str, data: dict[str, Any], ttl_seconds: int) -> None:
        expires_at = time.time() + max(1, int(ttl_seconds))
        with self._lock:
            self._memory[key] = (expires_at, data)
            self._memory.move_to_end(key)
            self._trim_memory()

        path = self.root / f"{key}.json"
        temporary = self.root / f".{key}-{os.getpid()}-{threading.get_ident()}.tmp"
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(
                    {"expires_at": expires_at, "data": data},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

        self._writes += 1
        if self._writes % 64 == 0:
            self._trim_disk()

    def _trim_memory(self) -> None:
        while len(self._memory) > self.max_memory_entries:
            self._memory.popitem(last=False)

    def _trim_disk(self) -> None:
        try:
            files = sorted(
                self.root.glob("*.json"),
                key=lambda candidate: candidate.stat().st_mtime,
                reverse=True,
            )
            for old_path in files[256:]:
                old_path.unlink(missing_ok=True)
        except OSError:
            pass


class TMDBClient:
    def __init__(self, read_token: str):
        self.read_token = read_token
        self._thread_local = threading.local()
        self._cache = _JsonResponseCache(
            CACHE_ROOT / "api",
            max_memory_entries=24 if RUNTIME.low_memory else 64,
        )

    def _session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is not None:
            return session
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=2, pool_maxsize=2, max_retries=1)
        session.mount("https://", adapter)
        session.headers.update(
            {
                "Authorization": f"Bearer {self.read_token}",
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
                "User-Agent": f"{APP_NAME}/{APP_VERSION}",
            }
        )
        self._thread_local.session = session
        return session

    @staticmethod
    def _cache_ttl(endpoint: str) -> int:
        if endpoint.startswith("/search/"):
            return 5 * 60
        if endpoint.startswith("/trending/"):
            return 15 * 60
        if "/season/" in endpoint:
            return 24 * 60 * 60
        if endpoint.endswith("/popular") or endpoint.endswith("/upcoming"):
            return 30 * 60
        if endpoint.endswith("/top_rated"):
            return 6 * 60 * 60
        return 6 * 60 * 60

    @staticmethod
    def _fields(source: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
        return {name: source.get(name) for name in names if source.get(name) is not None}

    @classmethod
    def _compact_list_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        item_fields = (
            "id",
            "media_type",
            "title",
            "name",
            "release_date",
            "first_air_date",
            "poster_path",
            "backdrop_path",
            "overview",
            "vote_average",
        )
        compact = cls._fields(payload, ("page", "total_pages", "total_results"))
        compact["results"] = [
            cls._fields(item, item_fields)
            for item in payload.get("results", [])
            if isinstance(item, dict)
        ]
        return compact

    @classmethod
    def _compact_details_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        compact = cls._fields(
            payload,
            (
                "id",
                "title",
                "name",
                "release_date",
                "first_air_date",
                "poster_path",
                "backdrop_path",
                "overview",
                "vote_average",
                "runtime",
                "episode_run_time",
                "number_of_seasons",
            ),
        )
        compact["genres"] = [
            cls._fields(genre, ("id", "name"))
            for genre in payload.get("genres", [])
            if isinstance(genre, dict)
        ]
        compact["seasons"] = [
            cls._fields(
                season,
                (
                    "id",
                    "name",
                    "season_number",
                    "episode_count",
                    "air_date",
                    "poster_path",
                    "overview",
                ),
            )
            for season in payload.get("seasons", [])
            if isinstance(season, dict)
        ]
        videos = payload.get("videos", {})
        youtube = [
            video
            for video in (videos.get("results", []) if isinstance(videos, dict) else [])
            if isinstance(video, dict) and video.get("site") == "YouTube" and video.get("key")
        ]
        ranked_videos = (
            [video for video in youtube if video.get("type") == "Trailer" and video.get("official")]
            + [video for video in youtube if video.get("type") == "Trailer" and not video.get("official")]
            + [video for video in youtube if video.get("type") != "Trailer"]
        )
        unique_videos: list[dict[str, Any]] = []
        seen_video_keys: set[str] = set()
        for video in ranked_videos:
            key = str(video.get("key", ""))
            if not key or key in seen_video_keys:
                continue
            seen_video_keys.add(key)
            unique_videos.append(video)
            if len(unique_videos) >= 16:
                break
        compact["videos"] = {
            "results": [
                cls._fields(
                    video,
                    ("id", "key", "name", "site", "type", "official", "published_at"),
                )
                for video in unique_videos
            ]
        }
        credits = payload.get("credits", {})
        compact["credits"] = {
            "cast": [
                cls._fields(person, ("id", "name"))
                for person in (credits.get("cast", []) if isinstance(credits, dict) else [])
                if isinstance(person, dict) and person.get("name")
            ][:5]
        }
        return compact

    @classmethod
    def _compact_season_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        compact = cls._fields(
            payload,
            ("id", "name", "overview", "air_date", "poster_path", "season_number"),
        )
        episode_fields = (
            "id",
            "name",
            "overview",
            "air_date",
            "still_path",
            "runtime",
            "season_number",
            "episode_number",
        )
        compact["episodes"] = [
            cls._fields(episode, episode_fields)
            for episode in payload.get("episodes", [])
            if isinstance(episode, dict)
        ]
        return compact

    def get(
        self,
        endpoint: str,
        *,
        _cache_variant: str = "raw",
        _transform: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
        **params: Any,
    ) -> dict[str, Any]:
        params.setdefault("language", "en-US")
        cache_source = json.dumps(
            [
                TMDB_CACHE_SCHEMA,
                _cache_variant,
                endpoint,
                sorted((str(key), str(value)) for key, value in params.items()),
            ],
            separators=(",", ":"),
        )
        cache_key = hashlib.sha256(cache_source.encode("utf-8")).hexdigest()
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            response = self._session().get(
                f"{TMDB_API_BASE}{endpoint}",
                params=params,
                timeout=(4, 14),
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("TMDB returned an invalid response")
            if _transform is not None:
                data = _transform(data)
        except Exception:
            stale = self._cache.get(cache_key, allow_stale=True)
            if stale is not None:
                return stale
            raise

        self._cache.put(cache_key, data, self._cache_ttl(endpoint))
        return data

    @staticmethod
    def normalize(item: dict[str, Any], fallback_type: Optional[str] = None) -> dict[str, Any]:
        item = dict(item)
        media_type = item.get("media_type") or fallback_type
        if media_type not in {"movie", "tv"}:
            media_type = "movie" if "title" in item else "tv"
        title = item.get("title") or item.get("name") or "Untitled"
        date = item.get("release_date") or item.get("first_air_date") or ""
        item["media_type"] = media_type
        item["title"] = title
        item["year"] = date[:4] if date else item.get("year", "")
        return item

    def trending(self) -> list[dict[str, Any]]:
        data = self.get(
            "/trending/all/week",
            _cache_variant="media-list",
            _transform=self._compact_list_payload,
        )
        return [
            self.normalize(x)
            for x in data.get("results", [])
            if x.get("media_type") in {"movie", "tv"} and x.get("poster_path")
        ]

    def popular_movies(self) -> list[dict[str, Any]]:
        data = self.get(
            "/movie/popular",
            _cache_variant="media-list",
            _transform=self._compact_list_payload,
        )
        return [self.normalize(x, "movie") for x in data.get("results", [])]

    def popular_tv(self) -> list[dict[str, Any]]:
        data = self.get(
            "/tv/popular",
            _cache_variant="media-list",
            _transform=self._compact_list_payload,
        )
        return [self.normalize(x, "tv") for x in data.get("results", [])]

    def top_rated_movies(self) -> list[dict[str, Any]]:
        data = self.get(
            "/movie/top_rated",
            _cache_variant="media-list",
            _transform=self._compact_list_payload,
        )
        return [self.normalize(x, "movie") for x in data.get("results", [])]

    def upcoming_movies(self) -> list[dict[str, Any]]:
        data = self.get(
            "/movie/upcoming",
            _cache_variant="media-list",
            _transform=self._compact_list_payload,
        )
        return [self.normalize(x, "movie") for x in data.get("results", [])]

    def search(self, query: str) -> list[dict[str, Any]]:
        data = self.get(
            "/search/multi",
            query=query,
            include_adult="false",
            _cache_variant="media-list",
            _transform=self._compact_list_payload,
        )
        items = []
        for item in data.get("results", []):
            if item.get("media_type") not in {"movie", "tv"} or not item.get("poster_path"):
                continue
            items.append(self.normalize(item))
        return items

    def details(self, media_type: str, media_id: int) -> dict[str, Any]:
        data = self.get(
            f"/{media_type}/{media_id}",
            append_to_response="videos,credits",
            _cache_variant="details",
            _transform=self._compact_details_payload,
        )
        return self.normalize(data, media_type)

    def season_details(self, series_id: int, season_number: int) -> dict[str, Any]:
        """Load the episodes for one TV season from TMDB."""
        data = dict(
            self.get(
                f"/tv/{int(series_id)}/season/{int(season_number)}",
                _cache_variant="season",
                _transform=self._compact_season_payload,
            )
        )
        data["season_number"] = int(data.get("season_number", season_number) or season_number)
        episodes = []
        for raw_episode in data.get("episodes", []):
            if not isinstance(raw_episode, dict):
                continue
            episode = dict(raw_episode)
            episode["season_number"] = int(
                episode.get("season_number", data["season_number"]) or data["season_number"]
            )
            episode["episode_number"] = int(episode.get("episode_number", 0) or 0)
            if episode["episode_number"] > 0:
                episodes.append(episode)
        data["episodes"] = episodes
        return data


class WorkerSignals(QObject):
    success = Signal(object)
    error = Signal(str)
    finished = Signal(object)


class _WorkerReaper(QObject):
    @Slot(object)
    def release(self, worker: QRunnable) -> None:
        _release_worker(worker)


class FunctionWorker(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            self.signals.success.emit(self.fn())
        except Exception as exc:
            try:
                self.signals.error.emit(str(exc))
            except RuntimeError:
                pass
        finally:
            try:
                self.signals.finished.emit(self)
            except RuntimeError:
                _release_worker(self)


class ImageSignals(QObject):
    loaded = Signal(object)
    failed = Signal(str)
    finished = Signal(object)


_IMAGE_HTTP_LOCAL = threading.local()
_IMAGE_CACHE_WRITE_LOCK = threading.Lock()
_IMAGE_CACHE_WRITES = 0


def _image_http_session() -> requests.Session:
    session = getattr(_IMAGE_HTTP_LOCAL, "session", None)
    if session is not None:
        return session
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=2, pool_maxsize=2, max_retries=1)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": f"{APP_NAME}/{APP_VERSION}",
        }
    )
    _IMAGE_HTTP_LOCAL.session = session
    return session


def _image_disk_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return CACHE_ROOT / "images" / f"{digest}.img"


def _load_image_bytes(url: str) -> bytes:
    """Read a poster from cache or stream it there without a second RAM copy."""
    path = _image_disk_path(url)
    try:
        if path.is_file() and path.stat().st_size > 0:
            return path.read_bytes()
    except OSError:
        pass

    temporary = path.with_name(
        f".{path.stem}-{os.getpid()}-{threading.get_ident()}.tmp"
    )
    sink = None
    fallback_chunks: Optional[list[bytes]] = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        sink = temporary.open("wb")
    except OSError:
        fallback_chunks = []

    total = 0
    try:
        with _image_http_session().get(url, timeout=(4, 14), stream=True) as response:
            response.raise_for_status()
            for chunk in response.iter_content(64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > 12 * 1024 * 1024:
                    raise ValueError("Image response exceeded PiStick's safety limit")
                if sink is not None:
                    sink.write(chunk)
                elif fallback_chunks is not None:
                    fallback_chunks.append(chunk)
    except Exception:
        if sink is not None:
            sink.close()
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    if sink is not None:
        sink.close()
    if total <= 0:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ValueError("Image response was empty")

    if fallback_chunks is not None:
        return b"".join(fallback_chunks)

    try:
        os.replace(temporary, path)
    except OSError:
        try:
            data = temporary.read_bytes()
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return data
    _trim_image_disk_cache_periodically(path.parent)
    return path.read_bytes()
def _trim_image_disk_cache_periodically(root: Path) -> None:
    global _IMAGE_CACHE_WRITES
    with _IMAGE_CACHE_WRITE_LOCK:
        _IMAGE_CACHE_WRITES += 1
        if _IMAGE_CACHE_WRITES % 128:
            return
        try:
            files = sorted(
                root.glob("*.img"),
                key=lambda candidate: candidate.stat().st_mtime,
                reverse=True,
            )
            retained_bytes = 0
            for index, cache_file in enumerate(files):
                size = cache_file.stat().st_size
                retained_bytes += size
                if index >= 511 or retained_bytes > 128 * 1024 * 1024:
                    cache_file.unlink(missing_ok=True)
        except OSError:
            pass


class ImageWorker(QRunnable):
    def __init__(self, key: str, url: str, width: int, height: int, crop: bool):
        super().__init__()
        self.key = key
        self.url = url
        self.width = int(width)
        self.height = int(height)
        self.crop = bool(crop)
        self.signals = ImageSignals()

    def run(self) -> None:
        try:
            image = QImage.fromData(_load_image_bytes(self.url))
            if image.isNull():
                raise ValueError("Unsupported image data")
            target_size = QSize(self.width, self.height)
            mode = Qt.KeepAspectRatioByExpanding if self.crop else Qt.KeepAspectRatio
            image = image.scaled(target_size, mode, Qt.SmoothTransformation)
            if self.crop:
                x = max(0, (image.width() - self.width) // 2)
                y = max(0, (image.height() - self.height) // 2)
                image = image.copy(x, y, self.width, self.height)
            try:
                self.signals.loaded.emit((self.key, image))
            except RuntimeError:
                pass
        except Exception:
            try:
                self.signals.failed.emit(self.key)
            except RuntimeError:
                pass
        finally:
            try:
                self.signals.finished.emit(self)
            except RuntimeError:
                _release_worker(self)


@dataclass
class _PendingImage:
    key: str
    url: str
    width: int
    height: int
    crop: bool
    subscribers: list[weakref.ReferenceType]


class ImagePipeline(QObject):
    """Viewport-aware, coalescing image loader with bounded decoded memory."""

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(RUNTIME.image_threads)
        self.pool.setExpiryTimeout(3000)
        self._requests: dict[str, _PendingImage] = {}
        self._pending: OrderedDict[str, _PendingImage] = OrderedDict()
        self._inflight: set[str] = set()
        self._cache: OrderedDict[str, tuple[QPixmap, int]] = OrderedDict()
        self._cache_cost = 0
        self._registered: list[weakref.ReferenceType] = []
        self._dispatch_timer = QTimer(self)
        self._dispatch_timer.setSingleShot(True)
        self._dispatch_timer.timeout.connect(self._scan_and_dispatch)

    @staticmethod
    def cache_key(url: str, width: int, height: int, crop: bool) -> str:
        return f"{url}|{int(width)}x{int(height)}|{int(bool(crop))}"

    def request(self, target: "RemoteImage", url: str) -> None:
        key = self.cache_key(
            url,
            target.target_size.width(),
            target.target_size.height(),
            target.crop,
        )
        target._image_url = url
        target._image_cache_key = key
        target._image_failed = False
        if not target._pipeline_registered:
            target._pipeline_registered = True
            self._registered.append(weakref.ref(target))

        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            target._set_shared_pixmap(cached[0])
            return

        request = self._requests.get(key)
        if request is None:
            request = _PendingImage(
                key,
                url,
                target.target_size.width(),
                target.target_size.height(),
                target.crop,
                [],
            )
            self._requests[key] = request
            self._pending[key] = request
        self._add_subscriber(request, target)
        self.kick()

    @staticmethod
    def _add_subscriber(request: _PendingImage, target: "RemoteImage") -> None:
        for target_ref in request.subscribers:
            if target_ref() is target:
                return
        request.subscribers.append(weakref.ref(target))

    def kick(self, delay: int = 0) -> None:
        if not self._dispatch_timer.isActive():
            self._dispatch_timer.start(max(0, int(delay)))

    def viewport_changed(self) -> None:
        # Scroll animations can emit dozens of value changes. One coalesced scan
        # per frame-sized interval keeps image work off that hot path.
        if not self._dispatch_timer.isActive():
            self._dispatch_timer.start(55)

    @staticmethod
    def _near_viewport(widget: "RemoteImage", margin: int) -> bool:
        try:
            window = widget.window()
            if window is None or not widget.isVisibleTo(window):
                return False
            ancestor = widget.parentWidget()
            while ancestor is not None:
                if isinstance(ancestor, QScrollArea):
                    viewport = ancestor.viewport()
                    top_left = widget.mapTo(viewport, QPoint(0, 0))
                    image_rect = QRect(top_left, widget.size())
                    visible_rect = viewport.rect().adjusted(-margin, -margin, margin, margin)
                    if not visible_rect.intersects(image_rect):
                        return False
                ancestor = ancestor.parentWidget()
            return True
        except RuntimeError:
            return False

    def _scan_and_dispatch(self) -> None:
        # A poster can appear in the registry and again as a subscriber to a
        # coalesced request. Cache geometry answers for this scan so scrolling
        # never performs the same nested map-to-viewport walk twice.
        visibility: dict[tuple[int, int], bool] = {}

        def near(target: "RemoteImage", margin: int) -> bool:
            key = (id(target), int(margin))
            cached = visibility.get(key)
            if cached is None:
                cached = self._near_viewport(target, margin)
                visibility[key] = cached
            return cached

        live_registered: list[weakref.ReferenceType] = []
        for target_ref in self._registered:
            target = target_ref()
            if target is None:
                continue
            live_registered.append(target_ref)
            try:
                if (
                    RUNTIME.low_memory
                    and target._has_image
                    and not near(target, RUNTIME.image_release_margin)
                ):
                    target._release_pixmap()
                elif (
                    target._image_url
                    and not target._has_image
                    and not target._image_failed
                    and near(target, RUNTIME.image_prefetch_margin)
                ):
                    self.request(target, target._image_url)
            except RuntimeError:
                continue
        self._registered = live_registered

        for key, request in list(self._pending.items()):
            request.subscribers = [
                target_ref
                for target_ref in request.subscribers
                if target_ref() is not None
            ]
            if not request.subscribers:
                self._pending.pop(key, None)
                self._requests.pop(key, None)

        while len(self._inflight) < RUNTIME.image_threads:
            selected_key: Optional[str] = None
            for key, request in self._pending.items():
                if any(
                    target is not None
                    and near(target, RUNTIME.image_prefetch_margin)
                    for target in (target_ref() for target_ref in request.subscribers)
                ):
                    selected_key = key
                    break
            if selected_key is None:
                break

            request = self._pending.pop(selected_key)
            self._inflight.add(selected_key)
            worker = ImageWorker(
                request.key,
                request.url,
                request.width,
                request.height,
                request.crop,
            )
            worker.signals.loaded.connect(self._image_loaded)
            worker.signals.failed.connect(self._image_failed)
            _start_worker(self.pool, worker)

    @Slot(object)
    def _image_loaded(self, payload: object) -> None:
        try:
            key, image = payload
        except (TypeError, ValueError):
            return
        if not isinstance(image, QImage) or image.isNull():
            self._image_failed(str(key))
            return
        pixmap = QPixmap.fromImage(image)
        self._remember(str(key), pixmap)
        request = self._requests.get(str(key))
        if request is not None:
            for target_ref in request.subscribers:
                target = target_ref()
                if target is None:
                    continue
                try:
                    if target._image_cache_key == key:
                        target._set_shared_pixmap(pixmap)
                except RuntimeError:
                    pass
        self._finish_request(str(key))

    @Slot(str)
    def _image_failed(self, key: str) -> None:
        request = self._requests.get(key)
        if request is not None:
            for target_ref in request.subscribers:
                target = target_ref()
                if target is None:
                    continue
                try:
                    if target._image_cache_key == key:
                        target._set_failed()
                except RuntimeError:
                    pass
        self._finish_request(key)

    def _finish_request(self, key: str) -> None:
        self._inflight.discard(key)
        self._pending.pop(key, None)
        self._requests.pop(key, None)
        self.kick()

    def _remember(self, key: str, pixmap: QPixmap) -> None:
        old = self._cache.pop(key, None)
        if old is not None:
            self._cache_cost -= old[1]
        cost = max(1, pixmap.width() * pixmap.height() * 4)
        self._cache[key] = (pixmap, cost)
        self._cache_cost += cost
        while self._cache and self._cache_cost > RUNTIME.image_memory_cache_bytes:
            _old_key, (_old_pixmap, old_cost) = self._cache.popitem(last=False)
            self._cache_cost -= old_cost


_IMAGE_PIPELINE: Optional[ImagePipeline] = None


def _image_pipeline() -> ImagePipeline:
    global _IMAGE_PIPELINE
    if _IMAGE_PIPELINE is None:
        _IMAGE_PIPELINE = ImagePipeline(QApplication.instance())
    return _IMAGE_PIPELINE


def _notify_image_view_changed() -> None:
    if _IMAGE_PIPELINE is not None:
        _IMAGE_PIPELINE.viewport_changed()


class RemoteImage(QLabel):
    imageReady = Signal(object)

    def __init__(
        self,
        thread_pool: QThreadPool,
        width: int,
        height: int,
        radius: int = 8,
        crop: bool = True,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.thread_pool = thread_pool
        self.target_size = QSize(width, height)
        self.crop = crop
        self._image_url = ""
        self._image_cache_key = ""
        self._has_image = False
        self._image_failed = False
        self._pipeline_registered = False
        self.setFixedSize(width, height)
        self.setAlignment(Qt.AlignCenter)
        self.setObjectName(f"remoteImage{radius if radius in (8, 10, 14) else 8}")
        self.setText("Loading…")

    def load(self, url: str) -> None:
        if not url:
            self.setText("No image")
            return
        _image_pipeline().request(self, url)

    def copy_from(self, source: "RemoteImage") -> None:
        """Share a loaded poster with a carousel clone without downloading it again."""
        if source._image_url:
            self.load(source._image_url)
            return
        source.imageReady.connect(self._set_shared_pixmap)
        pixmap = source.pixmap()
        if pixmap is not None and not pixmap.isNull():
            self._set_shared_pixmap(pixmap)

    def _set_failed(self) -> None:
        self._image_failed = True
        self._has_image = False
        self.setText("No image")

    def _set_shared_pixmap(self, pixmap: QPixmap) -> None:
        if pixmap is None or pixmap.isNull():
            self.setText("No image")
            return
        self.setPixmap(pixmap)
        self.setText("")
        self._image_failed = False
        self._has_image = True
        self.imageReady.emit(pixmap)

    def _release_pixmap(self) -> None:
        if not self._has_image:
            return
        self.setPixmap(QPixmap())
        self.setText("Loading…")
        self._has_image = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        _image_pipeline().viewport_changed()


TrailerWebView = None
_WEBENGINE_ATTEMPTED = False
_TRAILER_WEB_PROFILE = None
_PLAYBACK_WEB_PROFILE = None
_PLAYBACK_AD_INTERCEPTOR = None
WindowsPlaybackWebView = None
_WINDOWS_WEBVIEW_ATTEMPTED = False
_WINDOWS_WEBVIEW_INITIALIZED = False
_WINDOWS_WEBVIEW_ERROR = ""


def _initialize_windows_playback_webview() -> bool:
    """Prepare Qt WebView's Edge WebView2 backend before QApplication exists."""
    global _WINDOWS_WEBVIEW_ATTEMPTED
    global _WINDOWS_WEBVIEW_INITIALIZED
    global _WINDOWS_WEBVIEW_ERROR

    if _WINDOWS_WEBVIEW_ATTEMPTED:
        return _WINDOWS_WEBVIEW_INITIALIZED
    _WINDOWS_WEBVIEW_ATTEMPTED = True

    if sys.platform != "win32" or QT_BINDING != "PySide6":
        return False

    try:
        # Qt WebView defaults to its "native" plug-in, but pin the explicit key
        # so an installed Qt WebEngine plug-in cannot win by load order.
        os.environ["QT_WEBVIEW_PLUGIN"] = "webview2"
        from PySide6.QtWebView import QWebView, QtWebView

        # QWebView's public QWindow API was added in Qt/PySide 6.11. Merely
        # importing an older QtWebView module is therefore not sufficient.
        if not hasattr(QWebView, "runJavaScript"):
            raise RuntimeError("PySide6.QtWebView.QWebView is unavailable")
        QtWebView.initialize()
        _WINDOWS_WEBVIEW_INITIALIZED = True
    except (ImportError, OSError, RuntimeError) as exc:
        _WINDOWS_WEBVIEW_ERROR = str(exc).strip() or exc.__class__.__name__
    return _WINDOWS_WEBVIEW_INITIALIZED


def _windows_playback_unavailable_message() -> str:
    message = (
        "Windows playback needs PySide6 6.11 or newer and the Microsoft Edge "
        "WebView2 Runtime. Run: py -m pip install --upgrade \"PySide6>=6.11,<7\""
    )
    if _WINDOWS_WEBVIEW_ERROR:
        return f"{message}\n\nDetails: {_WINDOWS_WEBVIEW_ERROR}"
    return message


_PLAYBACK_FRAME_BRIDGE_SOURCE = r"""
(() => {
    const bridgeToken = __PISTICK_BRIDGE_TOKEN__;
    if (window.__pistickMediaBridgeToken === bridgeToken) return;
    window.__pistickMediaBridgeToken = bridgeToken;

    const progressType = 'pistick-playback-progress';
    const commandType = 'pistick-media-command';

    const numberOrZero = (value) => {
        const number = Number(value);
        return Number.isFinite(number) ? number : 0;
    };

    const localVideo = () => {
        const videos = Array.from(document.querySelectorAll('video'));
        let selected = null;
        let selectedScore = -1;
        for (const video of videos) {
            const area = Math.max(0, numberOrZero(video.clientWidth))
                * Math.max(0, numberOrZero(video.clientHeight));
            const duration = Math.max(0, numberOrZero(video.duration));
            const playingBonus = (!video.paused && !video.ended) ? 1000000000000 : 0;
            const score = playingBonus + (area * 1000) + duration;
            if (score > selectedScore) {
                selected = video;
                selectedScore = score;
            }
        }
        return selected;
    };

    const applyPendingSeek = (video) => {
        const target = Number(window.__pistickPendingSeekSeconds);
        if (!video || !Number.isFinite(target) || target <= 0) return false;
        const duration = Number(video.duration);
        if (video.readyState === 0 || !Number.isFinite(duration) || duration <= 0) {
            if (!video.__pistickSeekMetadataBound) {
                video.__pistickSeekMetadataBound = true;
                video.addEventListener(
                    'loadedmetadata',
                    () => applyPendingSeek(video),
                    { once: true }
                );
            }
            return false;
        }
        try {
            video.currentTime = Math.min(target, Math.max(0, duration - 1));
            window.__pistickPendingSeekSeconds = null;
            return true;
        } catch (_error) {
            return false;
        }
    };

    const requestSeek = (value) => {
        const target = Number(value);
        if (!Number.isFinite(target) || target <= 0) return;
        window.__pistickPendingSeekSeconds = target;
        if (typeof window.pistickSeekTo === 'function') {
            try {
                window.pistickSeekTo(target);
                window.__pistickPendingSeekSeconds = null;
            } catch (_error) {
                // Fall through to an ordinary HTML5 video when available.
            }
        }
        applyPendingSeek(localVideo());
    };

    const validProgress = (data) => {
        if (!data || data.type !== progressType) return null;
        if (data.bridgeToken && data.bridgeToken !== bridgeToken) return null;
        const currentTime = numberOrZero(data.currentTime ?? data.position);
        const duration = numberOrZero(data.duration);
        if (currentTime < 0 || duration <= 0) return null;
        return { currentTime, duration, updatedAt: Date.now() };
    };

    const saveProgress = (data) => {
        const progress = validProgress(data);
        if (progress) window.__pistickPlaybackState = progress;
    };

    const sendProgress = () => {
        const video = localVideo();
        if (!video) return;
        const currentTime = numberOrZero(video.currentTime);
        const duration = numberOrZero(video.duration);
        if (currentTime < 0 || duration <= 0) return;
        const data = {
            type: progressType,
            bridgeToken,
            currentTime,
            duration
        };
        try {
            if (window === window.top) saveProgress(data);
            else window.top.postMessage(data, '*');
        } catch (_error) {
            // Cross-origin WindowProxy.postMessage is allowed. If a sandboxed
            // frame blocks even that operation, the next polling pass can retry.
        }
    };

    const relayToChildren = (message) => {
        for (let index = 0; index < window.frames.length; index += 1) {
            try {
                window.frames[index].postMessage(message, '*');
            } catch (_error) {
                // Never inspect a child Window directly; only post to it.
            }
        }
    };

    // This listener is registered on the current frame's own Window. It never
    // reads parent.addEventListener or iframe.contentWindow properties.
    window.addEventListener('message', (event) => {
        const data = event && event.data;
        if (!data || typeof data !== 'object') return;
        if (data.type === progressType) {
            if (window === window.top) saveProgress(data);
            return;
        }
        if (data.type !== commandType || data.bridgeToken !== bridgeToken) return;

        const video = localVideo();
        if (video && data.action === 'pause') video.pause();
        if (video && data.action === 'play') {
            const result = video.play();
            if (result && typeof result.catch === 'function') result.catch(() => {});
        }
        if (data.action === 'seek') requestSeek(data.positionSeconds);
        relayToChildren(data);
    });

    const bindVideoEvents = () => {
        applyPendingSeek(localVideo());
        for (const video of document.querySelectorAll('video')) {
            if (video.__pistickProgressEventsBound) continue;
            video.__pistickProgressEventsBound = true;
            for (const eventName of ['timeupdate', 'durationchange', 'loadedmetadata', 'pause', 'ended']) {
                video.addEventListener(eventName, sendProgress, { passive: true });
            }
        }
        sendProgress();
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bindVideoEvents, { once: true });
    } else {
        bindVideoEvents();
    }
    const progressTimer = window.setInterval(bindVideoEvents, 1000);
    window.addEventListener(
        'pagehide',
        () => window.clearInterval(progressTimer),
        { once: true }
    );
})();
"""


def get_trailer_web_view_class():
    """Import Chromium only after the user opens the trailer screen."""
    global TrailerWebView, _WEBENGINE_ATTEMPTED
    if TrailerWebView is not None:
        return TrailerWebView
    if _WEBENGINE_ATTEMPTED:
        return None
    _WEBENGINE_ATTEMPTED = True
    try:
        if QT_BINDING == "PySide6":
            from PySide6.QtWebEngineCore import (
                QWebEnginePage,
                QWebEngineProfile,
                QWebEngineScript,
                QWebEngineSettings,
                QWebEngineUrlRequestInterceptor,
            )
            from PySide6.QtWebEngineWidgets import QWebEngineView
        else:
            from PyQt5.QtWebEngineCore import (  # type: ignore[no-redef]
                QWebEngineUrlRequestInterceptor,
            )
            from PyQt5.QtWebEngineWidgets import (  # type: ignore[no-redef]
                QWebEnginePage,
                QWebEngineProfile,
                QWebEngineScript,
                QWebEngineSettings,
                QWebEngineView,
            )
    except (ImportError, OSError):
        return None

    class _PlaybackRequestInterceptor(QWebEngineUrlRequestInterceptor):
        """Block known ad hosts before Chromium reaches the network stack."""

        def __init__(self, settings: AdBlockSettings, parent: Optional[QObject] = None):
            super().__init__(parent)
            self._settings = settings

        def interceptRequest(self, info) -> None:
            try:
                request_url = info.requestUrl().toString()
                if is_blocked_ad_url(request_url, self._settings):
                    info.block(True)
            except (AttributeError, RuntimeError):
                return

    class _MediaPage(QWebEnginePage):
        """Keep API playback in its trusted window and discard pop-ups."""

        def __init__(self, profile, parent, *, block_ads: bool = False):
            super().__init__(profile, parent)
            self._block_ads = bool(block_ads)
            self._trusted_url = ""

        def set_trusted_url(self, url: str) -> None:
            self._trusted_url = str(url or "")

        def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
            candidate = url.toString()
            if (
                self._block_ads
                and is_main_frame
                and self._trusted_url
                and candidate != "about:blank"
                and not same_origin(candidate, self._trusted_url)
            ):
                return False
            return super().acceptNavigationRequest(url, navigation_type, is_main_frame)

        def createWindow(self, window_type):
            if self._block_ads:
                return None
            return super().createWindow(window_type)

    class _TrailerWebView(QWebEngineView):
        """Web player that observes clicks without stealing controller focus."""

        clicked = Signal()

        def __init__(
            self,
            parent: Optional[QWidget] = None,
            *,
            block_ads: bool = False,
        ):
            super().__init__(parent)
            self._block_ads = bool(block_ads)
            self._adblock_settings = (
                load_adblock_settings(CONFIG_PATH)
                if self._block_ads
                else AdBlockSettings(enabled=False, blocked_hosts=frozenset())
            )
            # The profile must outlive every page that uses it. A shared,
            # application-owned profile avoids Qt releasing a dialog-scoped
            # profile while its asynchronous WebEnginePage is still shutting
            # down. Pages remain view-scoped and are discarded after playback.
            global _TRAILER_WEB_PROFILE
            global _PLAYBACK_WEB_PROFILE
            global _PLAYBACK_AD_INTERCEPTOR
            profile = _PLAYBACK_WEB_PROFILE if self._block_ads else _TRAILER_WEB_PROFILE
            if profile is None:
                profile_parent = QApplication.instance()
                profile_name = (
                    "pistick-playback-media"
                    if self._block_ads
                    else "pistick-trailer-media"
                )
                try:
                    profile = QWebEngineProfile(profile_name, profile_parent)
                except TypeError:
                    profile = QWebEngineProfile(profile_parent)
                self._configure_profile(profile, QWebEngineProfile)
                if self._block_ads:
                    if self._adblock_settings.enabled:
                        interceptor = _PlaybackRequestInterceptor(
                            self._adblock_settings,
                            profile,
                        )
                        profile.setUrlRequestInterceptor(interceptor)
                        _PLAYBACK_AD_INTERCEPTOR = interceptor
                    _PLAYBACK_WEB_PROFILE = profile
                else:
                    _TRAILER_WEB_PROFILE = profile
            self._profile = profile
            self._page = _MediaPage(
                self._profile,
                self,
                block_ads=self._block_ads,
            )
            # QWebEngineView owns its automatically created page. On PySide6,
            # setPage() can destroy that page immediately, which also
            # invalidates its Python wrapper. Do not call deleteLater() on the
            # old wrapper after setPage(); Qt has already handled its lifetime.
            self.setPage(self._page)
            self._bridge_token = uuid.uuid4().hex
            self._bridge_script = QWebEngineScript()
            self._bridge_script.setName("pistick-cross-frame-media-bridge")
            injection_points = getattr(QWebEngineScript, "InjectionPoint", QWebEngineScript)
            self._bridge_script.setInjectionPoint(
                getattr(injection_points, "DocumentReady")
            )
            worlds = getattr(QWebEngineScript, "ScriptWorldId", QWebEngineScript)
            self._bridge_script.setWorldId(getattr(worlds, "MainWorld", 0))
            self._bridge_script.setRunsOnSubFrames(True)
            self._bridge_script.setSourceCode(
                _PLAYBACK_FRAME_BRIDGE_SOURCE.replace(
                    "__PISTICK_BRIDGE_TOKEN__",
                    json.dumps(self._bridge_token),
                )
            )
            self._page.scripts().insert(self._bridge_script)
            self._adblock_script = None
            if self._adblock_settings.enabled:
                self._adblock_script = QWebEngineScript()
                self._adblock_script.setName("pistick-playback-adblock")
                self._adblock_script.setInjectionPoint(
                    getattr(injection_points, "DocumentCreation")
                )
                self._adblock_script.setWorldId(getattr(worlds, "MainWorld", 0))
                self._adblock_script.setRunsOnSubFrames(True)
                self._adblock_script.setSourceCode(
                    build_playback_adblock_script(self._adblock_settings)
                )
                self._page.scripts().insert(self._adblock_script)

            web_attribute = getattr(QWebEngineSettings, "WebAttribute", QWebEngineSettings)
            for name, enabled in (
                ("PlaybackRequiresUserGesture", False),
                ("JavascriptCanOpenWindows", False),
                ("PluginsEnabled", False),
                ("AutoLoadIconsForPage", False),
            ):
                attribute = getattr(web_attribute, name, None)
                if attribute is not None:
                    self.settings().setAttribute(attribute, enabled)
            self._filter_installed = False
            app = QApplication.instance()
            if app is not None:
                app.installEventFilter(self)
                self._filter_installed = True

        def setUrl(self, url: QUrl) -> None:
            if self._block_ads:
                self._page.set_trusted_url(url.toString())
            super().setUrl(url)

        @staticmethod
        def _configure_profile(profile, profile_type) -> None:
            try:
                cache_path = CACHE_ROOT / "webengine"
                cache_path.mkdir(parents=True, exist_ok=True)
                profile.setCachePath(str(cache_path))
                profile.setHttpCacheMaximumSize(RUNTIME.web_cache_bytes)
                cache_enum_type = getattr(profile_type, "HttpCacheType", profile_type)
                disk_cache = getattr(cache_enum_type, "DiskHttpCache", None)
                if disk_cache is not None:
                    profile.setHttpCacheType(disk_cache)
                profile.setSpellCheckEnabled(False)
                cookie_enum_type = getattr(
                    profile_type,
                    "PersistentCookiesPolicy",
                    profile_type,
                )
                no_persistent_cookies = getattr(
                    cookie_enum_type,
                    "NoPersistentCookies",
                    None,
                )
                if no_persistent_cookies is not None:
                    profile.setPersistentCookiesPolicy(no_persistent_cookies)
            except (AttributeError, OSError, RuntimeError):
                pass

        def _contains_widget(self, widget: QObject) -> bool:
            current = widget if isinstance(widget, QWidget) else None
            while current is not None:
                if current is self:
                    return True
                current = current.parentWidget()
            return False

        def eventFilter(self, watched: QObject, event: QEvent) -> bool:
            if (
                event.type() == QEvent.MouseButtonRelease
                and event.button() == Qt.LeftButton
                and self._contains_widget(watched)
            ):
                self.clicked.emit()
            return False

        def play_media(self) -> None:
            bridge_token = json.dumps(self._bridge_token)
            self.page().runJavaScript(
                """
                (() => {
                    if (typeof window.pistickPlayTrailer === 'function') {
                        window.pistickPlayTrailer();
                    }
                    const video = document.querySelector('video');
                    if (video) {
                        const result = video.play();
                        if (result && typeof result.catch === 'function') result.catch(() => {});
                    }
                    window.postMessage({
                        type: 'pistick-media-command',
                        bridgeToken: __PISTICK_BRIDGE_TOKEN__,
                        action: 'play'
                    }, '*');
                    return true;
                })();
                """.replace("__PISTICK_BRIDGE_TOKEN__", bridge_token)
            )

        def pause_media(self) -> None:
            bridge_token = json.dumps(self._bridge_token)
            self.page().runJavaScript(
                """
                (() => {
                    if (typeof window.pistickPauseTrailer === 'function') {
                        window.pistickPauseTrailer();
                    }
                    const video = document.querySelector('video');
                    if (video) video.pause();
                    window.postMessage({
                        type: 'pistick-media-command',
                        bridgeToken: __PISTICK_BRIDGE_TOKEN__,
                        action: 'pause'
                    }, '*');
                    return true;
                })();
                """.replace("__PISTICK_BRIDGE_TOKEN__", bridge_token)
            )

        def seek_media(self, position_seconds: float) -> None:
            try:
                position = float(position_seconds)
            except (TypeError, ValueError):
                return
            if not math.isfinite(position) or position <= 0:
                return
            bridge_token = json.dumps(self._bridge_token)
            seek_position = json.dumps(position)
            self.page().runJavaScript(
                """
                (() => {
                    window.postMessage({
                        type: 'pistick-media-command',
                        bridgeToken: __PISTICK_BRIDGE_TOKEN__,
                        action: 'seek',
                        positionSeconds: __PISTICK_SEEK_POSITION__
                    }, '*');
                    return true;
                })();
                """
                .replace("__PISTICK_BRIDGE_TOKEN__", bridge_token)
                .replace("__PISTICK_SEEK_POSITION__", seek_position)
            )

        def request_playback_state(self, callback: Callable[[object], None]) -> None:
            """Read progress reported by the page or any nested player frame."""
            try:
                self.page().runJavaScript(
                    """
                    (() => {
                        try {
                            if (typeof window.pistickGetPlaybackState === 'function') {
                                const state = window.pistickGetPlaybackState();
                                if (state && typeof state.then !== 'function') return state;
                            }
                            const video = document.querySelector('video');
                            if (video) {
                                return {
                                    currentTime: Number(video.currentTime || 0),
                                    duration: Number.isFinite(video.duration) ? Number(video.duration) : 0
                                };
                            }
                            const state = window.__pistickPlaybackState || null;
                            if (!state) return null;
                            if (state.updatedAt && Date.now() - state.updatedAt > 15000) return null;
                            return state;
                        } catch (_error) {
                            return null;
                        }
                    })();
                    """,
                    callback,
                )
            except RuntimeError:
                callback(None)

        # Compatibility aliases for the existing trailer dialog.
        play_trailer = play_media
        pause_trailer = pause_media

        def dispose(self) -> None:
            if self._filter_installed:
                app = QApplication.instance()
                if app is not None:
                    app.removeEventFilter(self)
                self._filter_installed = False
            try:
                self.stop()
                self.page().setAudioMuted(True)
                # setPage() immediately deletes the current page because that
                # page is parented to this view. The shared profile therefore
                # never begins teardown while a page still refers to it.
                replacement_page = QWebEnginePage(self)
                self.setPage(replacement_page)
                self._page = replacement_page
            except RuntimeError:
                pass
            self._bridge_script = None
            self._adblock_script = None

    _TrailerWebView.__name__ = "TrailerWebView"
    TrailerWebView = _TrailerWebView
    return TrailerWebView


def get_windows_playback_web_view_class():
    """Return a QWidget wrapper around Windows' native Edge WebView2 view."""
    global WindowsPlaybackWebView
    if WindowsPlaybackWebView is not None:
        return WindowsPlaybackWebView
    if not _WINDOWS_WEBVIEW_INITIALIZED:
        return None

    try:
        from PySide6.QtWebView import QWebView, QWebViewLoadingInfo
    except (ImportError, OSError):
        return None

    load_status = getattr(QWebViewLoadingInfo, "LoadStatus", None)
    load_succeeded = getattr(load_status, "Succeeded", None)
    load_failed = getattr(load_status, "Failed", None)
    load_stopped = getattr(load_status, "Stopped", None)

    class _WindowsPlaybackWebView(QWidget):
        """Edge-backed player used on Windows when Qt WebEngine lacks HLS codecs."""

        clicked = Signal()
        loadFinished = Signal(bool)

        def __init__(
            self,
            parent: Optional[QWidget] = None,
            *,
            block_ads: bool = True,
        ):
            super().__init__(parent)
            self._bridge_token = uuid.uuid4().hex
            self._disposed = False
            self._capabilities_reported = False
            self._adblock_settings = (
                load_adblock_settings(CONFIG_PATH)
                if block_ads
                else AdBlockSettings(enabled=False, blocked_hosts=frozenset())
            )
            self._adblock_source = build_playback_adblock_script(
                self._adblock_settings
            )
            self._trusted_url = ""
            self._adblock_injected_for_url = ""
            self._restore_scheduled = False
            self._native_view = QWebView()
            window_type = getattr(Qt, "WindowType", Qt)
            no_focus = getattr(window_type, "WindowDoesNotAcceptFocus", None)
            if no_focus is not None:
                self._native_view.setFlag(no_focus, True)
            self._native_view.loadingChanged.connect(self._loading_changed)
            self._native_view.loadProgressChanged.connect(self._load_progress_changed)
            self._native_view.urlChanged.connect(self._url_changed)
            self._window_container = QWidget.createWindowContainer(
                self._native_view,
                self,
            )
            self._window_container.setFocusPolicy(Qt.NoFocus)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            layout.addWidget(self._window_container)

        def _run_javascript(
            self,
            source: str,
            callback: Optional[Callable[[object], None]] = None,
        ) -> None:
            if self._disposed:
                if callback is not None:
                    callback(None)
                return
            result_callback = callback or (lambda _result: None)
            try:
                self._native_view.runJavaScript(source, result_callback)
            except (RuntimeError, TypeError):
                result_callback(None)

        def _loading_changed(self, info) -> None:
            if self._disposed:
                return
            try:
                status = info.status()
            except (AttributeError, RuntimeError):
                return
            if status == load_succeeded:
                bridge_source = _PLAYBACK_FRAME_BRIDGE_SOURCE.replace(
                    "__PISTICK_BRIDGE_TOKEN__",
                    json.dumps(self._bridge_token),
                )
                source = f"{self._adblock_source}\n{bridge_source}"
                self._run_javascript(source, self._bridge_ready)
            elif status in {load_failed, load_stopped}:
                self.loadFinished.emit(False)

        def _load_progress_changed(self, progress: int) -> None:
            """Install filtering as early as Qt's native WebView API permits."""
            if self._disposed or not self._adblock_settings.enabled or progress <= 0:
                return
            try:
                current_url = self._native_view.url().toString()
            except RuntimeError:
                return
            if not current_url or current_url == self._adblock_injected_for_url:
                return
            self._adblock_injected_for_url = current_url
            self._run_javascript(self._adblock_source)

        def _url_changed(self, url: QUrl) -> None:
            if self._disposed or not self._adblock_settings.enabled:
                return
            candidate = url.toString()
            if not self._trusted_url or candidate == "about:blank":
                return
            if same_origin(candidate, self._trusted_url) or self._restore_scheduled:
                return
            self._restore_scheduled = True
            QTimer.singleShot(0, self._restore_trusted_url)

        def _restore_trusted_url(self) -> None:
            self._restore_scheduled = False
            if self._disposed or not self._trusted_url:
                return
            try:
                self._native_view.stop()
                self._native_view.setUrl(QUrl(self._trusted_url))
            except RuntimeError:
                pass

        def _bridge_ready(self, _result: object) -> None:
            if self._disposed:
                return
            if not self._capabilities_reported:
                self._run_javascript(
                    """
                    (() => JSON.stringify({
                        userAgent: navigator.userAgent || '',
                        mediaSource: typeof MediaSource !== 'undefined',
                        h264Aac: typeof MediaSource !== 'undefined'
                            && typeof MediaSource.isTypeSupported === 'function'
                            && MediaSource.isTypeSupported(
                                'video/mp4; codecs="avc1.42E01E, mp4a.40.2"'
                            )
                    }))();
                    """,
                    self._media_capabilities_received,
                )
            self.loadFinished.emit(True)

        def _media_capabilities_received(self, result: object) -> None:
            self._capabilities_reported = True
            capabilities = self._decode_javascript_result(result)
            if not isinstance(capabilities, dict):
                return
            user_agent = str(capabilities.get("userAgent") or "")
            engine = "Edge WebView2" if "Edg/" in user_agent else "unknown native WebView"
            hls_ready = bool(capabilities.get("mediaSource")) and bool(
                capabilities.get("h264Aac")
            )
            print(
                f"[PiStick] Windows playback engine: {engine}; "
                f"H.264/AAC MSE: {'available' if hls_ready else 'unavailable'}"
            )

        def setUrl(self, url: QUrl) -> None:
            if self._adblock_settings.enabled:
                self._trusted_url = url.toString()
                self._adblock_injected_for_url = ""
            self._native_view.setUrl(url)

        def mouseReleaseEvent(self, event) -> None:
            self.clicked.emit()
            super().mouseReleaseEvent(event)

        def play_media(self) -> None:
            bridge_token = json.dumps(self._bridge_token)
            self._run_javascript(
                """
                (() => {
                    const video = document.querySelector('video');
                    if (video) {
                        const result = video.play();
                        if (result && typeof result.catch === 'function') result.catch(() => {});
                    }
                    window.postMessage({
                        type: 'pistick-media-command',
                        bridgeToken: __PISTICK_BRIDGE_TOKEN__,
                        action: 'play'
                    }, '*');
                    return true;
                })();
                """.replace("__PISTICK_BRIDGE_TOKEN__", bridge_token)
            )

        def pause_media(self) -> None:
            bridge_token = json.dumps(self._bridge_token)
            self._run_javascript(
                """
                (() => {
                    const video = document.querySelector('video');
                    if (video) video.pause();
                    window.postMessage({
                        type: 'pistick-media-command',
                        bridgeToken: __PISTICK_BRIDGE_TOKEN__,
                        action: 'pause'
                    }, '*');
                    return true;
                })();
                """.replace("__PISTICK_BRIDGE_TOKEN__", bridge_token)
            )

        def seek_media(self, position_seconds: float) -> None:
            try:
                position = float(position_seconds)
            except (TypeError, ValueError):
                return
            if not math.isfinite(position) or position <= 0:
                return
            bridge_token = json.dumps(self._bridge_token)
            seek_position = json.dumps(position)
            self._run_javascript(
                """
                (() => {
                    window.postMessage({
                        type: 'pistick-media-command',
                        bridgeToken: __PISTICK_BRIDGE_TOKEN__,
                        action: 'seek',
                        positionSeconds: __PISTICK_SEEK_POSITION__
                    }, '*');
                    return true;
                })();
                """
                .replace("__PISTICK_BRIDGE_TOKEN__", bridge_token)
                .replace("__PISTICK_SEEK_POSITION__", seek_position)
            )

        @staticmethod
        def _decode_javascript_result(result: object) -> object:
            decoded = result
            for _attempt in range(2):
                if not isinstance(decoded, str) or not decoded.strip():
                    break
                try:
                    decoded = json.loads(decoded)
                except (TypeError, ValueError):
                    break
            return decoded

        def request_playback_state(self, callback: Callable[[object], None]) -> None:
            def result_received(result: object) -> None:
                callback(self._decode_javascript_result(result))

            self._run_javascript(
                """
                (() => {
                    try {
                        let state = null;
                        if (typeof window.pistickGetPlaybackState === 'function') {
                            const reported = window.pistickGetPlaybackState();
                            if (reported && typeof reported.then !== 'function') state = reported;
                        }
                        if (!state) {
                            const video = document.querySelector('video');
                            if (video) {
                                state = {
                                    currentTime: Number(video.currentTime || 0),
                                    duration: Number.isFinite(video.duration) ? Number(video.duration) : 0
                                };
                            }
                        }
                        if (!state) state = window.__pistickPlaybackState || null;
                        if (state && state.updatedAt && Date.now() - state.updatedAt > 15000) {
                            state = null;
                        }
                        return state ? JSON.stringify(state) : '';
                    } catch (_error) {
                        return '';
                    }
                })();
                """,
                result_received,
            )

        # Compatibility aliases for TrailerDialog's shared controls.
        play_trailer = play_media
        pause_trailer = pause_media

        def dispose(self) -> None:
            if self._disposed:
                return
            self._disposed = True
            self._trusted_url = ""
            try:
                self._native_view.stop()
                self._native_view.setUrl(QUrl("about:blank"))
            except RuntimeError:
                pass

    _WindowsPlaybackWebView.__name__ = "WindowsPlaybackWebView"
    WindowsPlaybackWebView = _WindowsPlaybackWebView
    return WindowsPlaybackWebView


def get_playback_web_view_class():
    """Select a codec-capable player without changing the documented API URL."""
    if sys.platform == "win32" and QT_BINDING == "PySide6":
        return get_windows_playback_web_view_class()
    return get_trailer_web_view_class()


class SmoothScrollArea(QScrollArea):
    """QScrollArea with animated wheel and controller scrolling."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._v_animation = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._h_animation = QPropertyAnimation(self.horizontalScrollBar(), b"value", self)
        for animation in (self._v_animation, self._h_animation):
            animation.setDuration(230)
            animation.setEasingCurve(QEasingCurve.OutCubic)
        self.verticalScrollBar().valueChanged.connect(_notify_image_view_changed)
        self.horizontalScrollBar().valueChanged.connect(_notify_image_view_changed)

    @staticmethod
    def _clamp(bar, value: int) -> int:
        return max(bar.minimum(), min(bar.maximum(), int(value)))

    def _animate(self, bar, animation: QPropertyAnimation, target: int, duration: int = 230) -> None:
        target = self._clamp(bar, target)
        if target == bar.value():
            return
        animation.stop()
        animation.setDuration(duration)
        animation.setStartValue(bar.value())
        animation.setEndValue(target)
        animation.start()

    def wheelEvent(self, event) -> None:
        vertical = self.verticalScrollBar()
        horizontal = self.horizontalScrollBar()
        delta_y = event.angleDelta().y()
        delta_x = event.angleDelta().x()

        if vertical.maximum() > 0 and not (event.modifiers() & Qt.ShiftModifier):
            delta = delta_y or delta_x
            target = vertical.value() - int(delta * 1.55)
            self._animate(vertical, self._v_animation, target, 260)
            event.accept()
            return

        if horizontal.maximum() > 0 and (delta_x or event.modifiers() & Qt.ShiftModifier):
            delta = delta_x or delta_y
            target = horizontal.value() - int(delta * 1.55)
            self._animate(horizontal, self._h_animation, target, 240)
            event.accept()
            return

        # A vertical wheel over a horizontal movie row should still scroll the page.
        event.ignore()

    def smooth_ensure_widget_visible(self, widget: QWidget, xmargin: int = 40, ymargin: int = 40) -> None:
        content = self.widget()
        if content is None:
            return
        try:
            top_left = widget.mapTo(content, QPoint(0, 0))
        except Exception:
            return
        left = top_left.x()
        top = top_left.y()
        right = left + widget.width()
        bottom = top + widget.height()

        hbar = self.horizontalScrollBar()
        vbar = self.verticalScrollBar()
        viewport_w = self.viewport().width()
        viewport_h = self.viewport().height()

        htarget = hbar.value()
        if left < htarget + xmargin:
            htarget = left - xmargin
        elif right > htarget + viewport_w - xmargin:
            htarget = right - viewport_w + xmargin

        vtarget = vbar.value()
        if top < vtarget + ymargin:
            vtarget = top - ymargin
        elif bottom > vtarget + viewport_h - ymargin:
            vtarget = bottom - viewport_h + ymargin

        self._animate(hbar, self._h_animation, htarget, 210)
        self._animate(vbar, self._v_animation, vtarget, 240)


class HorizontalMediaScrollArea(SmoothScrollArea):
    """A truly horizontal-only carousel.

    The row itself never handles vertical movement. Vertical wheel/trackpad
    input is forwarded to the outer page, while horizontal input moves only
    this carousel.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWidgetResizable(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.verticalScrollBar().setSingleStep(0)
        self.verticalScrollBar().setPageStep(0)
        self.horizontal_rebase_callback: Optional[Callable[[], None]] = None
        self._drag_active = False
        self._dragging = False
        self._drag_start_x = 0
        self._drag_start_value = 0

    def _parent_vertical_scroll_area(self) -> Optional[SmoothScrollArea]:
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, SmoothScrollArea) and parent is not self:
                if parent.verticalScrollBar().maximum() > parent.verticalScrollBar().minimum():
                    return parent
            parent = parent.parentWidget()
        return None

    @staticmethod
    def _event_deltas(event) -> tuple[int, int, bool, bool]:
        angle = event.angleDelta()
        pixel = event.pixelDelta()
        precise_x = not pixel.isNull() and bool(pixel.x())
        precise_y = not pixel.isNull() and bool(pixel.y())
        delta_x = pixel.x() if precise_x else angle.x()
        delta_y = pixel.y() if precise_y else angle.y()
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        return int(delta_x), int(delta_y), precise_x, shift

    def forward_vertical_delta(self, delta_y: int, precise: bool = False) -> bool:
        page_scroll = self._parent_vertical_scroll_area()
        if page_scroll is None or not delta_y:
            return False
        bar = page_scroll.verticalScrollBar()
        multiplier = 3.0 if precise else 1.55
        target = bar.value() - int(delta_y * multiplier)
        page_scroll._animate(bar, page_scroll._v_animation, target, 260)
        return True

    def handle_filtered_wheel(self, event) -> bool:
        """Called by MainWindow's application-wide event filter.

        This catches wheel events even when they land on a poster/title child
        instead of the QScrollArea viewport.
        """
        angle = event.angleDelta()
        pixel = event.pixelDelta()
        precise_y = not pixel.isNull() and bool(pixel.y())
        precise_x = not pixel.isNull() and bool(pixel.x())
        delta_y = pixel.y() if precise_y else angle.y()
        delta_x = pixel.x() if precise_x else angle.x()
        shift = bool(event.modifiers() & Qt.ShiftModifier)

        # Any normal vertical component belongs to the main page. Do not let a
        # diagonal trackpad gesture move the horizontal row vertically.
        if delta_y and not shift:
            self.forward_vertical_delta(int(delta_y), precise_y)
            event.accept()
            return True

        # Pure horizontal trackpad input (or Shift+wheel) moves only the row.
        horizontal_delta = delta_x or (delta_y if shift else 0)
        if horizontal_delta:
            bar = self.horizontalScrollBar()
            if callable(self.horizontal_rebase_callback):
                self.horizontal_rebase_callback()
            multiplier = 3.0 if precise_x else 1.55
            target = bar.value() - int(horizontal_delta * multiplier)
            self._animate(bar, self._h_animation, target, 240)
            event.accept()
            return True

        return False

    def wheelEvent(self, event) -> None:
        if self.handle_filtered_wheel(event):
            return
        event.ignore()

    @staticmethod
    def _global_x(event) -> int:
        if hasattr(event, "globalPosition"):
            return int(event.globalPosition().x())
        return int(event.globalPos().x())

    def begin_mouse_drag(self, event) -> None:
        self._drag_active = True
        self._dragging = False
        self._drag_start_x = self._global_x(event)
        if callable(self.horizontal_rebase_callback):
            self.horizontal_rebase_callback()
        self._drag_start_value = self.horizontalScrollBar().value()

    def update_mouse_drag(self, event) -> bool:
        if not self._drag_active or not (event.buttons() & Qt.LeftButton):
            return False
        distance = self._global_x(event) - self._drag_start_x
        if not self._dragging and abs(distance) < 7:
            return False
        self._dragging = True
        self._h_animation.stop()
        bar = self.horizontalScrollBar()
        bar.setValue(self._clamp(bar, self._drag_start_value - distance))

        # Rebase while dragging so a long swipe never reaches a physical edge.
        before = bar.value()
        if callable(self.horizontal_rebase_callback):
            self.horizontal_rebase_callback()
        self._drag_start_value += bar.value() - before
        return True

    def end_mouse_drag(self) -> bool:
        consumed = self._drag_active and self._dragging
        self._drag_active = False
        self._dragging = False
        if callable(self.horizontal_rebase_callback):
            self.horizontal_rebase_callback()
        return consumed

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # A hidden vertical bar can still retain a stale value after resizes.
        self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())

    def smooth_ensure_widget_visible(self, widget: QWidget, xmargin: int = 40, ymargin: int = 40) -> None:
        """Controller focus may move the row left/right, never up/down."""
        content = self.widget()
        if content is None:
            return
        try:
            top_left = widget.mapTo(content, QPoint(0, 0))
        except Exception:
            return

        left = top_left.x()
        right = left + widget.width()
        hbar = self.horizontalScrollBar()
        viewport_w = self.viewport().width()

        target = hbar.value()
        if left < target + xmargin:
            target = left - xmargin
        elif right > target + viewport_w - xmargin:
            target = right - viewport_w + xmargin
        self._animate(hbar, self._h_animation, target, 210)

        # The row has no valid vertical movement.
        self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())


class ClickableFrame(QFrame):
    clicked = Signal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class MediaCard(ClickableFrame):
    def __init__(
        self,
        media: dict[str, Any],
        thread_pool: QThreadPool,
        open_details: Callable[[dict[str, Any]], None],
        width: int = 166,
        state_lookup: Optional[Callable[[dict[str, Any]], Optional[dict[str, Any]]]] = None,
        show_progress: bool = False,
        image_source: Optional[RemoteImage] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.media = media
        self.state_lookup = state_lookup
        self.show_progress = bool(show_progress)
        self.watched_badge: Optional[QLabel] = None
        self.progress_bar: Optional[QProgressBar] = None
        self.setObjectName("mediaCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setProperty("controllerSelected", False)

        # Give the controller outline real breathing room around the poster/text.
        outline_space = 6
        self.setFixedWidth(width + outline_space * 2)
        layout = QVBoxLayout(self)
        self.card_layout = layout
        layout.setContentsMargins(outline_space, outline_space, outline_space, outline_space)
        layout.setSpacing(7)

        poster_height = int(width * 1.5)
        poster_holder = QFrame()
        self.poster_holder = poster_holder
        poster_holder.setObjectName("posterHolder")
        poster_holder.setFixedSize(width, poster_height)
        self.poster = RemoteImage(thread_pool, width, poster_height, radius=8, parent=poster_holder)
        self.poster.move(0, 0)
        poster_path = media.get("poster_path")
        if image_source is not None:
            self.poster.copy_from(image_source)
        elif poster_path:
            # TMDB's 185 px source is already larger than the 166/172 px card.
            # Downloading w342 wastes bandwidth, decode time, and peak RAM with
            # no visible benefit at this rendered size.
            self.poster.load(f"{TMDB_IMAGE_BASE}/w185{poster_path}")

        layout.addWidget(poster_holder)

        title = QLabel(media.get("title", "Untitled"))
        title.setObjectName("cardTitle")
        title.setWordWrap(True)
        title.setMinimumHeight(38)
        title.setMaximumHeight(42)
        layout.addWidget(title)

        meta = QLabel(self._meta_text(media))
        meta.setObjectName("cardMeta")
        meta.setMinimumHeight(16)
        layout.addWidget(meta)

        self.clicked.connect(lambda: open_details(self.media))
        self.refresh_watch_state()

    def refresh_watch_state(self) -> None:
        entry = self.state_lookup(self.media) if self.state_lookup else None
        finished = bool(entry and entry.get("status") == "finished")
        in_progress = bool(entry and entry.get("status") == "in_progress")

        if finished and self.watched_badge is None:
            watched = QLabel("✓  WATCHED", self.poster_holder)
            watched.setObjectName("watchedBadge")
            watched.adjustSize()
            watched.move(8, 8)
            watched.raise_()
            watched.show()
            self.watched_badge = watched
        elif not finished and self.watched_badge is not None:
            self.watched_badge.hide()
            self.watched_badge.deleteLater()
            self.watched_badge = None

        if self.show_progress and in_progress:
            if self.progress_bar is None:
                progress = QProgressBar()
                progress.setObjectName("watchProgress")
                progress.setRange(0, 1000)
                progress.setTextVisible(False)
                progress.setFixedHeight(5)
                self.card_layout.insertWidget(1, progress)
                self.progress_bar = progress
            self.progress_bar.setValue(
                int(float(entry.get("progress", 0.0) or 0.0) * 1000)
            )
        elif self.progress_bar is not None:
            self.card_layout.removeWidget(self.progress_bar)
            self.progress_bar.hide()
            self.progress_bar.deleteLater()
            self.progress_bar = None

    def set_controller_selected(self, selected: bool) -> None:
        self.setProperty("controllerSelected", bool(selected))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def activate(self) -> None:
        self.clicked.emit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Left, Qt.Key_Right):
            row = getattr(self, "controller_row", None)
            direction = "left" if event.key() == Qt.Key_Left else "right"
            if isinstance(row, MediaRow) and row.keyboard_move(self, direction):
                event.accept()
                return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.activate()
            event.accept()
            return
        super().keyPressEvent(event)

    @staticmethod
    def _meta_text(media: dict[str, Any]) -> str:
        media_kind = "Series" if media.get("media_type") == "tv" else "Movie"
        year = media.get("year") or ""
        rating = media.get("vote_average")
        rating_text = f"★ {rating:.1f}" if isinstance(rating, (int, float)) and rating else ""
        return "  •  ".join(x for x in [year, media_kind, rating_text] if x)


class MediaRow(QWidget):
    """A mouse, keyboard, trackpad, and controller-friendly infinite row."""

    def __init__(
        self,
        title: str,
        items: list[dict[str, Any]],
        thread_pool: QThreadPool,
        open_details: Callable[[dict[str, Any]], None],
        state_lookup: Optional[Callable[[dict[str, Any]], Optional[dict[str, Any]]]] = None,
        show_progress: bool = False,
        infinite: bool = True,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        unique_items: list[dict[str, Any]] = []
        seen_items: set[tuple[str, str]] = set()
        for item in items:
            media_id = item.get("id")
            if media_id is None:
                identity = (
                    "fallback",
                    f"{item.get('title') or item.get('name', '')}|{item.get('poster_path', '')}",
                )
            else:
                identity = (str(item.get("media_type") or "movie"), str(media_id))
            if identity in seen_items:
                continue
            seen_items.add(identity)
            unique_items.append(item)
            if len(unique_items) >= 20:
                break

        self.items = unique_items
        self.infinite = bool(infinite and len(self.items) > 1)
        self.cards: list[MediaCard] = []
        self.left_clone: Optional[MediaCard] = None
        self.right_clone: Optional[MediaCard] = None
        self._wrapping = False
        self._wrap_animation: Optional[QPropertyAnimation] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        heading = QLabel(title)
        heading.setObjectName("rowHeading")
        outer.addWidget(heading)

        self.scroll = HorizontalMediaScrollArea()
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll_height = 364 if show_progress else 354
        self.scroll.setFixedHeight(self.scroll_height)
        self.scroll.viewport().setStyleSheet("background: transparent;")

        self.content = QWidget()
        self.content.setObjectName("transparentWidget")
        self.row_layout = QHBoxLayout(self.content)
        self.row_layout.setContentsMargins(0, 0, 18, 0)
        self.row_layout.setSpacing(12)

        def make_card(
            item: dict[str, Any],
            clone: bool = False,
            image_source: Optional[RemoteImage] = None,
        ) -> MediaCard:
            card = MediaCard(
                item,
                thread_pool,
                open_details,
                state_lookup=state_lookup,
                show_progress=show_progress,
                image_source=image_source,
            )
            card.controller_row = self
            card.setProperty("controllerClone", clone)
            if clone:
                card.setFocusPolicy(Qt.NoFocus)
            return card

        # Render a cyclic edge band on both sides of the focusable cards. The
        # scrollbar can then be rebased by exactly one logical period after a
        # wheel, drag, or key animation; identical posters occupy the same
        # pixels, so the rebase stays invisible and no empty edge is exposed.
        if self.items:
            self.cards = [make_card(item) for item in self.items]
            if self.infinite:
                item_count = len(self.items)
                card_step = self.cards[0].width() + self.row_layout.spacing()
                screen = QApplication.primaryScreen()
                viewport_budget = (
                    screen.availableGeometry().width() if screen is not None else 1440
                )
                # Only one viewport of edge copies is required to hide a
                # physical end while the scrollbar is invisibly rebased. The
                # old implementation kept three complete 20-card periods (60
                # full card trees), even though most could never be seen.
                edge_count = max(2, (viewport_budget + card_step - 1) // card_step)

                left_cards: list[MediaCard] = []
                for offset in range(edge_count, 0, -1):
                    item_index = (-offset) % item_count
                    card = make_card(
                        self.items[item_index],
                        clone=True,
                        image_source=self.cards[item_index].poster,
                    )
                    left_cards.append(card)
                    self.row_layout.addWidget(card)

                for card in self.cards:
                    self.row_layout.addWidget(card)

                right_cards: list[MediaCard] = []
                for offset in range(edge_count):
                    item_index = offset % item_count
                    card = make_card(
                        self.items[item_index],
                        clone=True,
                        image_source=self.cards[item_index].poster,
                    )
                    right_cards.append(card)
                    self.row_layout.addWidget(card)

                self.left_clone = left_cards[-1]
                self.right_clone = right_cards[0]
            else:
                for card in self.cards:
                    self.row_layout.addWidget(card)

        card_count = self.row_layout.count()
        margins = self.row_layout.contentsMargins()
        if self.cards and card_count:
            width = (
                margins.left()
                + margins.right()
                + card_count * self.cards[0].width()
                + max(0, card_count - 1) * self.row_layout.spacing()
            )
        else:
            width = 1
        self.content.setFixedSize(width, self.scroll_height - 2)
        self.row_layout.activate()
        self.scroll.setWidget(self.content)
        self.scroll.horizontal_rebase_callback = self._rebase_scroll_position
        self.scroll._h_animation.finished.connect(self._rebase_scroll_position)
        outer.addWidget(self.scroll)

        # Start on the first real card, with the left-side duplicate clipped away.
        if self.cards:
            QTimer.singleShot(0, self._position_on_first_real)
            QTimer.singleShot(80, self._position_on_first_real)

    @property
    def wrapping(self) -> bool:
        return self._wrapping

    def _position_on_first_real(self) -> None:
        if not self.cards:
            return
        self.row_layout.activate()
        bar = self.scroll.horizontalScrollBar()
        bar.setValue(max(bar.minimum(), min(bar.maximum(), self.cards[0].x())))

    def _period_width(self) -> int:
        if self.cards and self.right_clone is not None:
            measured = self.right_clone.x() - self.cards[0].x()
            if measured > 0:
                return measured
        return self._card_step() * max(1, len(self.cards))

    def _rebase_scroll_position(self) -> None:
        """Move to an identical copy of the row before a physical edge appears."""
        if not self.infinite or not self.cards or self._wrapping:
            return
        bar = self.scroll.horizontalScrollBar()
        anchor = self.cards[0].x()
        period = self._period_width()
        if period <= 0:
            return
        value = bar.value()
        if anchor <= value < anchor + period:
            return
        rebased = anchor + ((value - anchor) % period)
        bar.setValue(max(bar.minimum(), min(bar.maximum(), int(rebased))))

    def _card_step(self) -> int:
        if len(self.cards) >= 2:
            return max(1, self.cards[1].x() - self.cards[0].x())
        if self.cards and self.right_clone is not None:
            return max(1, self.right_clone.x() - self.cards[0].x())
        if self.cards:
            return max(1, self.cards[0].width() + self.row_layout.spacing())
        return 1

    def _animate_wrap(
        self,
        current: MediaCard,
        clone: MediaCard,
        destination: MediaCard,
        direction: str,
        select_callback: Callable[[MediaCard, bool], None],
    ) -> None:
        if self._wrapping:
            return
        self._rebase_scroll_position()
        self._wrapping = True

        bar = self.scroll.horizontalScrollBar()
        start_value = bar.value()
        step = self._card_step()
        if direction == "right":
            target = min(bar.maximum(), start_value + step)
        else:
            target = max(bar.minimum(), start_value - step)

        # Move a controller outline onto the edge copy for the short animation.
        # Physical-keyboard focus uses the same movement without that property.
        show_controller_outline = bool(current.property("controllerSelected"))
        if show_controller_outline:
            try:
                current.set_controller_selected(False)
                clone.set_controller_selected(True)
            except RuntimeError:
                pass

        animation = QPropertyAnimation(bar, b"value", self)
        self._wrap_animation = animation
        animation.setDuration(220)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.setStartValue(start_value)
        animation.setEndValue(target)

        def finish() -> None:
            if show_controller_outline:
                try:
                    clone.set_controller_selected(False)
                except RuntimeError:
                    pass

            # Preserve the duplicate's exact screen position while switching to
            # its real twin. This is the invisible rebase that avoids a rewind.
            if direction == "right":
                clone_x = clone.x()
                dest_x = destination.x()
                snap = target - (clone_x - dest_x)
            else:
                clone_x = clone.x()
                dest_x = destination.x()
                snap = target + (dest_x - clone_x)

            bar.setValue(max(bar.minimum(), min(bar.maximum(), int(snap))))
            self._wrapping = False
            self._wrap_animation = None
            select_callback(destination, False)

        animation.finished.connect(finish)
        animation.start()

    def controller_move(
        self,
        current: MediaCard,
        direction: str,
        select_callback: Callable[[MediaCard, bool], None],
    ) -> bool:
        """Handle left/right controller motion within this row.

        Returns True when the row consumed the controller input.
        """
        if direction not in ("left", "right") or current not in self.cards:
            return False
        if self._wrapping:
            return True
        if len(self.cards) <= 1:
            return True

        index = self.cards.index(current)
        if direction == "right":
            if index < len(self.cards) - 1:
                select_callback(self.cards[index + 1], True)
            elif self.right_clone is not None:
                self._animate_wrap(current, self.right_clone, self.cards[0], "right", select_callback)
            return True

        if index > 0:
            select_callback(self.cards[index - 1], True)
        elif self.left_clone is not None:
            self._animate_wrap(current, self.left_clone, self.cards[-1], "left", select_callback)
        return True

    def keyboard_move(self, current: MediaCard, direction: str) -> bool:
        """Use Left/Right on a focused card with the same circular movement."""

        def select(card: MediaCard, ensure_visible: bool) -> None:
            card.setFocus(Qt.TabFocusReason)
            if ensure_visible:
                self.scroll.smooth_ensure_widget_visible(card, 48, 48)

        return self.controller_move(current, direction, select)


class ProfileCard(ClickableFrame):
    AVATAR_COLORS = {
        "red": "#c9192e",
        "blue": "#1672d4",
        "green": "#15995c",
        "purple": "#7b45b6",
        "orange": "#d46b21",
        "teal": "#148e99",
    }

    def __init__(
        self,
        profile: Optional[dict[str, Any]],
        on_activate: Callable[[], None],
        add_profile: bool = False,
        manage_mode: bool = False,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.profile = profile
        self.add_profile = add_profile
        self.setObjectName("profileCard")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(174, 212)
        self.setProperty("controllerSelected", False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 7, 7, 4)
        layout.setSpacing(11)

        avatar = QLabel("+" if add_profile else self._initials(profile.get("name", "") if profile else ""))
        self.avatar = avatar
        avatar.setObjectName("profileAvatar")
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setFixedSize(160, 160)
        avatar.setProperty("controllerSelected", False)
        avatar.setProperty("mouseHover", False)
        if add_profile:
            avatar.setProperty("addProfile", True)
            avatar.setProperty("avatarColor", "add")
        else:
            avatar.setProperty("avatarColor", (profile or {}).get("avatar", "blue"))

        if manage_mode and not add_profile:
            edit = QLabel("✎", avatar)
            edit.setObjectName("profileEditOverlay")
            edit.setAlignment(Qt.AlignCenter)
            edit.setGeometry(0, 0, 160, 160)
            edit.raise_()

        layout.addWidget(avatar, 0, Qt.AlignCenter)

        name = QLabel("Add Profile" if add_profile else (profile or {}).get("name", "Profile"))
        name.setObjectName("profileName")
        name.setAlignment(Qt.AlignCenter)
        name.setWordWrap(False)
        layout.addWidget(name)
        self.clicked.connect(on_activate)

    @staticmethod
    def _initials(name: str) -> str:
        parts = [p for p in name.strip().split() if p]
        if not parts:
            return "P"
        return "".join(p[0].upper() for p in parts[:2])

    def set_controller_selected(self, selected: bool) -> None:
        self.setProperty("controllerSelected", bool(selected))
        self.avatar.setProperty("controllerSelected", bool(selected))
        for widget in (self, self.avatar):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    def enterEvent(self, event):
        self.avatar.setProperty("mouseHover", True)
        self.avatar.style().unpolish(self.avatar)
        self.avatar.style().polish(self.avatar)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.avatar.setProperty("mouseHover", False)
        self.avatar.style().unpolish(self.avatar)
        self.avatar.style().polish(self.avatar)
        super().leaveEvent(event)

    def activate(self) -> None:
        self.clicked.emit()


class ControllerManager(QObject):
    connectionChanged = Signal(bool, str)
    navigate = Signal(str)
    activate = Signal()
    back = Signal()
    pause = Signal()

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.joystick = None
        self.connected = False
        self.last_scan = 0.0
        self.last_button_state: dict[int, bool] = {}
        self.direction_state = {"left": False, "right": False, "up": False, "down": False}
        self.direction_next_repeat = {k: 0.0 for k in self.direction_state}
        self.repeat_delay = 0.31
        self.repeat_rate = 0.115
        self.deadzone = 0.55
        self._pygame_ready = False

        self._initialize_pygame()

        self.timer = QTimer(self)
        self.timer.setInterval(260)
        self.timer.timeout.connect(self._poll)
        self.timer.start()

    def _initialize_pygame(self) -> bool:
        module = _load_pygame_module()
        if module is None:
            return False
        if self._pygame_ready:
            return True
        try:
            # PiStick only needs SDL's event and joystick subsystems. A full
            # pygame.init() also starts audio, font, and other modules that
            # reserve memory and can block on absent Pi hardware.
            if hasattr(module, "display"):
                module.display.init()
            module.joystick.init()
            self._pygame_ready = True
        except Exception:
            self._pygame_ready = False
        return self._pygame_ready

    def _set_poll_interval(self, connected: bool) -> None:
        interval = 35 if connected else 260
        if self.timer.interval() != interval:
            self.timer.setInterval(interval)

    def _scan(self) -> None:
        if pygame is None:
            return
        try:
            pygame.event.pump()
            count = pygame.joystick.get_count()
            if count > 0 and not self.connected:
                joystick = pygame.joystick.Joystick(0)
                joystick.init()
                self.joystick = joystick
                self.connected = True
                self.last_button_state.clear()
                self._set_poll_interval(True)
                self.connectionChanged.emit(True, joystick.get_name() or "Controller")
            elif count == 0 and self.connected:
                self.joystick = None
                self.connected = False
                self.last_button_state.clear()
                self._set_poll_interval(False)
                self.connectionChanged.emit(False, "")
        except Exception:
            if self.connected:
                self.joystick = None
                self.connected = False
                self.last_button_state.clear()
                self._set_poll_interval(False)
                self.connectionChanged.emit(False, "")

    def _button_edge(self, button: int) -> bool:
        if not self.joystick or button >= self.joystick.get_numbuttons():
            return False
        pressed = bool(self.joystick.get_button(button))
        previous = self.last_button_state.get(button, False)
        self.last_button_state[button] = pressed
        return pressed and not previous

    def _direction_values(self) -> dict[str, bool]:
        values = {"left": False, "right": False, "up": False, "down": False}
        if not self.joystick:
            return values
        try:
            if self.joystick.get_numhats() > 0:
                hx, hy = self.joystick.get_hat(0)
                values["left"] |= hx < 0
                values["right"] |= hx > 0
                values["up"] |= hy > 0
                values["down"] |= hy < 0
            if self.joystick.get_numaxes() >= 2:
                x = self.joystick.get_axis(0)
                y = self.joystick.get_axis(1)
                values["left"] |= x < -self.deadzone
                values["right"] |= x > self.deadzone
                values["up"] |= y < -self.deadzone
                values["down"] |= y > self.deadzone
        except Exception:
            pass
        return values

    def _poll(self) -> None:
        now = time.monotonic()
        if not self._pygame_ready:
            if now - self.last_scan > 1.0:
                self.last_scan = now
                if self._initialize_pygame():
                    self._scan()
            return
        if now - self.last_scan > 1.0:
            self.last_scan = now
            self._scan()
        if not self.connected or not self.joystick:
            return
        try:
            pygame.event.pump()
        except Exception:
            return

        if self._button_edge(0):  # A / Cross
            self.activate.emit()
        if self._button_edge(1):  # B / Circle
            self.back.emit()
        if self._button_edge(2):  # X / Square
            self.pause.emit()

        current = self._direction_values()
        for direction, pressed in current.items():
            was_pressed = self.direction_state[direction]
            if pressed and not was_pressed:
                self.navigate.emit(direction)
                self.direction_next_repeat[direction] = now + self.repeat_delay
            elif pressed and was_pressed and now >= self.direction_next_repeat[direction]:
                self.navigate.emit(direction)
                self.direction_next_repeat[direction] = now + self.repeat_rate
            elif not pressed:
                self.direction_next_repeat[direction] = 0.0
            self.direction_state[direction] = pressed


class OnScreenKeyboard(QDialog):
    def __init__(
        self,
        initial_text: str = "",
        title_text: str = "Search PiStick",
        submit_text: str = "Search",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title_text)
        self.setModal(True)
        self.setMinimumSize(900, 520)
        self.resize(980, 560)
        _ensure_app_stylesheet()
        self.setObjectName("keyboardDialog")
        self.first_key: Optional[QPushButton] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(34, 28, 34, 30)
        outer.setSpacing(18)

        title = QLabel(title_text)
        title.setObjectName("keyboardTitle")
        outer.addWidget(title)

        self.text_box = QLineEdit(initial_text)
        self.text_box.setObjectName("keyboardText")
        self.text_box.setPlaceholderText("Type here…")
        self.text_box.setReadOnly(True)
        self.text_box.setMinimumHeight(58)
        outer.addWidget(self.text_box)

        keyboard = QGridLayout()
        keyboard.setHorizontalSpacing(10)
        keyboard.setVerticalSpacing(10)
        rows = ["1234567890", "QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]
        max_cols = 10
        for row_index, chars in enumerate(rows):
            offset = (max_cols - len(chars)) // 2
            for col_index, char in enumerate(chars):
                button = QPushButton(char)
                button.setObjectName("keyboardKey")
                button.setMinimumSize(72, 58)
                button.setProperty("controllerSelected", False)
                button.clicked.connect(lambda _checked=False, c=char: self._append(c))
                keyboard.addWidget(button, row_index, col_index + offset)
                if self.first_key is None:
                    self.first_key = button
        outer.addLayout(keyboard)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        backspace = QPushButton("⌫  Backspace")
        backspace.setObjectName("keyboardAction")
        backspace.setProperty("controllerSelected", False)
        backspace.clicked.connect(self._backspace)
        actions.addWidget(backspace)

        space = QPushButton("Space")
        space.setObjectName("keyboardAction")
        space.setProperty("controllerSelected", False)
        space.clicked.connect(lambda: self._append(" "))
        actions.addWidget(space, 2)

        clear = QPushButton("Clear")
        clear.setObjectName("keyboardAction")
        clear.setProperty("controllerSelected", False)
        clear.clicked.connect(self.text_box.clear)
        actions.addWidget(clear)

        cancel = QPushButton("Cancel")
        cancel.setObjectName("secondaryButton")
        cancel.setProperty("controllerSelected", False)
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)

        submit = QPushButton(submit_text)
        submit.setObjectName("watchButton")
        submit.setProperty("controllerSelected", False)
        submit.clicked.connect(self.accept)
        actions.addWidget(submit)
        outer.addLayout(actions)

        hint = QLabel("Controller: D-pad / left stick to move   •   A to select   •   B to go back")
        hint.setObjectName("controllerHint")
        hint.setAlignment(Qt.AlignCenter)
        outer.addWidget(hint)

    def _append(self, text: str) -> None:
        self.text_box.setText(self.text_box.text() + text)

    def _backspace(self) -> None:
        self.text_box.setText(self.text_box.text()[:-1])

    def text(self) -> str:
        return self.text_box.text().strip()


class TextInputDialog(QDialog):
    """PiStick-styled physical-keyboard text dialog (no native white popup)."""

    def __init__(
        self,
        title_text: str,
        label_text: str,
        initial_text: str = "",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("textInputDialog")
        self.setWindowTitle(title_text)
        self.setModal(True)
        self.setFixedSize(470, 220)
        _ensure_app_stylesheet()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        title = QLabel(title_text)
        title.setObjectName("textInputTitle")
        layout.addWidget(title)

        label = QLabel(label_text)
        label.setObjectName("textInputLabel")
        layout.addWidget(label)

        self.edit = QLineEdit(initial_text)
        self.edit.setObjectName("textInputField")
        self.edit.setMinimumHeight(44)
        self.edit.returnPressed.connect(self.accept)
        layout.addWidget(self.edit)

        buttons = QHBoxLayout()
        buttons.addStretch(1)

        cancel = QPushButton("Cancel")
        cancel.setObjectName("textDialogCancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)

        ok = QPushButton("Save" if initial_text else "Add")
        ok.setObjectName("textDialogAccept")
        ok.clicked.connect(self.accept)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

        QTimer.singleShot(0, self._focus_edit)

    def _focus_edit(self) -> None:
        self.edit.setFocus(Qt.OtherFocusReason)
        self.edit.selectAll()

    @classmethod
    def get_text(
        cls,
        parent: QWidget,
        title_text: str,
        label_text: str,
        initial_text: str = "",
    ) -> tuple[str, bool]:
        dialog = cls(title_text, label_text, initial_text, parent)
        run_dialog = getattr(dialog, "exec", None) or getattr(dialog, "exec_")
        accepted = run_dialog() == QDialog.Accepted
        return dialog.edit.text().strip(), accepted


class TrailerDialog(QDialog):
    """On-demand embedded player whose browser lifetime matches this screen."""

    controlsReady = Signal(object)

    def __init__(
        self,
        title: str,
        trailer: Optional[dict[str, Any]] = None,
        parent: Optional[QWidget] = None,
        *,
        embed_url: str = "",
        player_label: str = "Trailer",
    ):
        super().__init__(parent)
        self.trailer = dict(trailer or {})
        self.embed_url = str(embed_url).strip()
        self.player_label = str(player_label).strip() or "Player"
        self.trailer_web: Optional[QWidget] = None
        self.trailer_placeholder: Optional[QFrame] = None
        self.trailer_overlay: Optional[QFrame] = None
        self.trailer_overlay_hint: Optional[QLabel] = None
        self._trailer_animation: Optional[QPropertyAnimation] = None
        self._trailer_fullscreen = False
        self._trailer_animating = False
        self._trailer_original_global_rect: Optional[QRect] = None
        self._window_geometry: Optional[QRect] = None
        self._was_fullscreen = False
        self._was_maximized = False
        self._play_started = False
        self._disposed = False

        self._fullscreen_timer = QTimer(self)
        self._fullscreen_timer.setSingleShot(True)
        self._fullscreen_timer.setInterval(2000)
        self._fullscreen_timer.timeout.connect(self.enter_fullscreen)

        self.setWindowTitle(f"{title} — {self.player_label}")
        self.resize(1080, 700)
        self.setMinimumSize(860, 540)
        self.setModal(True)
        _ensure_app_stylesheet()

        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        topbar = QHBoxLayout()
        topbar.setContentsMargins(28, 18, 24, 12)
        heading = QLabel(f"{self.player_label}  •  {title}")
        heading.setObjectName("rowHeading")
        topbar.addWidget(heading)
        topbar.addStretch(1)
        close = QPushButton("✕")
        self.close_button = close
        close.setObjectName("iconButton")
        close.setProperty("controllerSelected", False)
        close.setFocusPolicy(Qt.StrongFocus)
        close.setFixedSize(42, 42)
        close.clicked.connect(self.reject)
        topbar.addWidget(close)
        main.addLayout(topbar)

        body = QVBoxLayout()
        body.setContentsMargins(34, 0, 34, 34)
        body.setSpacing(0)
        main.addLayout(body, 1)

        placeholder = QFrame()
        placeholder.setObjectName("trailerPlaceholder")
        placeholder.setMinimumHeight(360)
        placeholder_layout = QVBoxLayout(placeholder)
        placeholder_layout.setContentsMargins(0, 0, 0, 0)
        placeholder_layout.setSpacing(0)
        body.addWidget(placeholder, 1)
        self.trailer_placeholder = placeholder

        trailer_view_class = (
            get_playback_web_view_class()
            if self.embed_url
            else get_trailer_web_view_class()
        )
        if trailer_view_class is None:
            message = "Embedded playback is unavailable in this build."
            if self.embed_url and sys.platform == "win32" and QT_BINDING == "PySide6":
                message = _windows_playback_unavailable_message()
            note = QLabel(message)
            note.setObjectName("mutedLabel")
            note.setAlignment(Qt.AlignCenter)
            note.setWordWrap(True)
            placeholder_layout.addWidget(note, 1)
        else:
            web = trailer_view_class(
                placeholder,
                block_ads=bool(self.embed_url),
            )
            web.setMinimumHeight(360)
            web.setFocusPolicy(Qt.NoFocus)
            web.clicked.connect(self._schedule_fullscreen)
            web.loadFinished.connect(
                lambda loaded, player=web: player.play_media() if loaded else None
            )
            if self.embed_url:
                web.setUrl(QUrl(self.embed_url))
            else:
                web.setHtml(
                    build_youtube_embed_html(str(self.trailer.get("key", ""))),
                    QUrl(f"{YOUTUBE_REFERER}trailer.html"),
                )
            placeholder_layout.addWidget(web)
            self.trailer_web = web

        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self._escape_requested)
        QTimer.singleShot(0, self._start_playback)
        QTimer.singleShot(0, lambda: self.controlsReady.emit(self.close_button))

    def _start_playback(self) -> None:
        if self._play_started or self.trailer_web is None:
            return
        self._play_started = True
        if hasattr(self.trailer_web, "play_media"):
            self.trailer_web.play_media()
        self._schedule_fullscreen()

    def _schedule_fullscreen(self) -> None:
        if self._trailer_fullscreen or self._trailer_animating or self.trailer_web is None:
            return
        self._fullscreen_timer.start()

    def enter_fullscreen(self) -> None:
        web = self.trailer_web
        placeholder = self.trailer_placeholder
        if (
            web is None
            or placeholder is None
            or self._trailer_fullscreen
            or not web.isVisible()
        ):
            return

        self._fullscreen_timer.stop()
        self._trailer_original_global_rect = QRect(
            web.mapToGlobal(QPoint(0, 0)),
            web.size(),
        )
        self._window_geometry = self.geometry()
        self._was_fullscreen = self.isFullScreen()
        self._was_maximized = self.isMaximized()

        placeholder_layout = placeholder.layout()
        if placeholder_layout is not None:
            placeholder_layout.removeWidget(web)

        overlay = QFrame(self)
        overlay.setObjectName("trailerFullscreenOverlay")
        overlay.setStyleSheet("background:#000000; border:0;")
        overlay_layout = QVBoxLayout(overlay)
        overlay_layout.setContentsMargins(0, 0, 0, 0)
        overlay_layout.setSpacing(0)
        web.setParent(overlay)
        overlay_layout.addWidget(web)

        hint = QLabel("Controller: X pause  •  B back     Keyboard: Esc back", overlay)
        hint.setObjectName("trailerFullscreenHint")
        hint.setStyleSheet(
            "background:rgba(0,0,0,185); color:#ffffff; border-radius:8px; "
            "padding:8px 12px; font-size:13px; font-weight:700;"
        )
        hint.adjustSize()

        self.trailer_overlay = overlay
        self.trailer_overlay_hint = hint
        self._trailer_fullscreen = True
        self._trailer_animating = True
        overlay.show()
        overlay.raise_()
        hint.raise_()

        self.showFullScreen()
        QTimer.singleShot(0, self._animate_open)

    def _animate_open(self) -> None:
        overlay = self.trailer_overlay
        original = self._trailer_original_global_rect
        if overlay is None or original is None or not self._trailer_fullscreen:
            return
        start = QRect(self.mapFromGlobal(original.topLeft()), original.size())
        overlay.setGeometry(start)
        self._position_hint()

        animation = QPropertyAnimation(overlay, b"geometry", self)
        self._trailer_animation = animation
        animation.setDuration(420)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.setStartValue(start)
        animation.setEndValue(self.rect())
        animation.valueChanged.connect(lambda _value: self._position_hint())

        def finish() -> None:
            if self.trailer_overlay is not None:
                self.trailer_overlay.setGeometry(self.rect())
            self._trailer_animating = False
            self._trailer_animation = None
            self._position_hint()

        animation.finished.connect(finish)
        animation.start()

    def _position_hint(self) -> None:
        overlay = self.trailer_overlay
        hint = self.trailer_overlay_hint
        if overlay is None or hint is None:
            return
        hint.adjustSize()
        hint.move(max(18, overlay.width() - hint.width() - 24), 20)
        hint.raise_()

    def is_fullscreen(self) -> bool:
        return self._trailer_fullscreen

    def pause_fullscreen(self) -> None:
        if (
            self._trailer_fullscreen
            and self.trailer_web is not None
            and hasattr(self.trailer_web, "pause_media")
        ):
            self.trailer_web.pause_media()

    def exit_fullscreen(self) -> bool:
        if not self._trailer_fullscreen or self.trailer_overlay is None:
            return False
        self._fullscreen_timer.stop()
        if self._trailer_animation is not None:
            self._trailer_animation.stop()

        overlay = self.trailer_overlay
        original = self._trailer_original_global_rect
        if original is None:
            target = QRect(self.rect().center(), QSize(1, 1))
        else:
            target = QRect(self.mapFromGlobal(original.topLeft()), original.size())

        self._trailer_animating = True
        animation = QPropertyAnimation(overlay, b"geometry", self)
        self._trailer_animation = animation
        animation.setDuration(380)
        animation.setEasingCurve(QEasingCurve.InOutCubic)
        animation.setStartValue(overlay.geometry())
        animation.setEndValue(target)
        animation.valueChanged.connect(lambda _value: self._position_hint())
        animation.finished.connect(self._finish_exit)
        animation.start()
        return True

    def _finish_exit(self) -> None:
        web = self.trailer_web
        placeholder = self.trailer_placeholder
        overlay = self.trailer_overlay
        if web is not None and placeholder is not None:
            overlay_layout = overlay.layout() if overlay is not None else None
            if overlay_layout is not None:
                overlay_layout.removeWidget(web)
            web.setParent(placeholder)
            placeholder_layout = placeholder.layout()
            if placeholder_layout is not None:
                placeholder_layout.addWidget(web)
            web.show()

        if overlay is not None:
            overlay.hide()
            overlay.deleteLater()
        self.trailer_overlay = None
        self.trailer_overlay_hint = None
        self._trailer_animation = None
        self._trailer_animating = False
        self._trailer_fullscreen = False
        self._restore_window()
        self.controlsReady.emit(self.close_button)

    def _restore_window(self) -> None:
        if self._was_fullscreen:
            self.showFullScreen()
        elif self._was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
            if self._window_geometry is not None:
                self.setGeometry(self._window_geometry)

    def controller_focusables(self) -> list[QWidget]:
        if self._trailer_fullscreen:
            return []
        return [self.close_button] if self.close_button.isVisible() else []

    def controller_current_target(self, current: Optional[QWidget] = None) -> Optional[QWidget]:
        focusables = self.controller_focusables()
        if current in focusables:
            return current
        return focusables[0] if focusables else None

    def controller_move_target(
        self,
        _direction: str,
        current: Optional[QWidget] = None,
    ) -> Optional[QWidget]:
        return self.controller_current_target(current)

    def controller_back(self) -> bool:
        if self.exit_fullscreen():
            return True
        self.reject()
        return True

    def _escape_requested(self) -> None:
        if not self.exit_fullscreen():
            self.reject()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._trailer_fullscreen and not self._trailer_animating and self.trailer_overlay is not None:
            self.trailer_overlay.setGeometry(self.rect())
            self._position_hint()

    def _dispose_player(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._fullscreen_timer.stop()
        if self._trailer_animation is not None:
            self._trailer_animation.stop()
            self._trailer_animation = None
        web = self.trailer_web
        self.trailer_web = None
        if web is not None and hasattr(web, "dispose"):
            try:
                web.dispose()
            except RuntimeError:
                pass
        if web is not None:
            web.deleteLater()

    def closeEvent(self, event) -> None:
        self._dispose_player()
        super().closeEvent(event)


class PlaybackDialog(TrailerDialog):
    """Movie or episode player backed by PiStick's playback API."""

    progressReported = Signal(float, float)

    def __init__(
        self,
        title: str,
        embed_url: str,
        parent: Optional[QWidget] = None,
        *,
        resume_seconds: float = 0.0,
    ):
        super().__init__(
            title,
            parent=parent,
            embed_url=embed_url,
            player_label="Now Playing",
        )
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(2000)
        self._progress_timer.timeout.connect(self._poll_progress)
        self._progress_request_pending = False
        self._closing_player = False
        try:
            requested_resume = float(resume_seconds)
        except (TypeError, ValueError):
            requested_resume = 0.0
        self._resume_seconds = (
            requested_resume
            if math.isfinite(requested_resume) and requested_resume > 0
            else 0.0
        )
        self._resume_attempts = 0
        self._resume_timer = QTimer(self)
        self._resume_timer.setInterval(1000)
        self._resume_timer.timeout.connect(self._try_resume)
        if self._resume_seconds > 0:
            self._resume_timer.start()
            QTimer.singleShot(250, self._try_resume)
        self._progress_timer.start()

    def _try_resume(self) -> None:
        if self._closing_player or self._resume_seconds <= 0:
            self._resume_timer.stop()
            return
        web = self.trailer_web
        if web is not None and hasattr(web, "seek_media"):
            try:
                web.seek_media(self._resume_seconds)
            except RuntimeError:
                pass
        self._resume_attempts += 1
        if self._resume_attempts >= 30:
            self._resume_timer.stop()

    def _poll_progress(self) -> None:
        web = self.trailer_web
        if (
            self._closing_player
            or self._progress_request_pending
            or web is None
            or not hasattr(web, "request_playback_state")
        ):
            return
        self._progress_request_pending = True
        try:
            web.request_playback_state(self._progress_received)
        except RuntimeError:
            self._progress_request_pending = False

    def _progress_received(self, state: object) -> None:
        self._progress_request_pending = False
        if self._closing_player:
            return
        if not isinstance(state, dict):
            return
        try:
            position = float(state.get("currentTime", state.get("position", 0.0)) or 0.0)
            duration = float(state.get("duration", 0.0) or 0.0)
        except (TypeError, ValueError):
            return
        if position >= 0 and duration > 0:
            if self._resume_seconds > 0 and position >= max(0.0, self._resume_seconds - 3.0):
                self._resume_seconds = 0.0
                self._resume_timer.stop()
            self.progressReported.emit(position, duration)

    def closeEvent(self, event) -> None:
        self._closing_player = True
        self._resume_timer.stop()
        self._progress_timer.stop()
        super().closeEvent(event)


class DetailDialog(QDialog):
    stateChanged = Signal()
    controlsReady = Signal(object)

    def __init__(
        self,
        client: TMDBClient,
        initial_media: dict[str, Any],
        thread_pool: QThreadPool,
        watch_state: WatchStateStore,
        profile_id: Optional[str],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.client = client
        self.media = initial_media
        self.thread_pool = thread_pool
        self.watch_state = watch_state
        self.profile_id = profile_id
        self.controller_actions: list[QPushButton] = []
        self.preferred_controller_widget: Optional[QWidget] = None
        self.watch_show_button: Optional[QPushButton] = None
        self.episode_panel: Optional[QFrame] = None
        self.episode_panel_close_button: Optional[QPushButton] = None
        self.episode_list_layout: Optional[QVBoxLayout] = None
        self.episode_scroll: Optional[QScrollArea] = None
        self.season_buttons: list[QPushButton] = []
        self.episode_buttons: list[QPushButton] = []
        self._season_cache: dict[int, dict[str, Any]] = {}
        self._season_request_generation = 0
        self._active_season: Optional[int] = None
        self._resume_episode_position: tuple[int, int] = (1, 1)
        self._episode_picker_open = False
        self._episode_picker_animating = False
        self._episode_panel_animation: Optional[QPropertyAnimation] = None
        self._episode_after_close: Optional[Callable[[], None]] = None
        self.trailer: Optional[dict[str, Any]] = None
        self.trailer_button: Optional[QPushButton] = None
        self._trailer_dialog: Optional[TrailerDialog] = None
        self._playback_dialog: Optional[PlaybackDialog] = None
        self.setWindowTitle(initial_media.get("title", APP_NAME))
        self.resize(1080, 760)
        self.setMinimumSize(860, 620)
        self.setModal(True)
        _ensure_app_stylesheet()

        self.main = QVBoxLayout(self)
        self.main.setContentsMargins(0, 0, 0, 0)
        self.main.setSpacing(0)

        topbar = QHBoxLayout()
        topbar.setContentsMargins(24, 18, 24, 12)
        topbar.addStretch(1)
        close_btn = QPushButton("✕")
        self.close_button = close_btn
        close_btn.setObjectName("iconButton")
        close_btn.setProperty("controllerSelected", False)
        close_btn.setFocusPolicy(Qt.StrongFocus)
        close_btn.setFixedSize(42, 42)
        close_btn.clicked.connect(self.reject)
        topbar.addWidget(close_btn)
        self.main.addLayout(topbar)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(34, 0, 34, 34)
        self.body.setSpacing(18)
        self.main.addLayout(self.body)

        loading = QLabel("Loading title…")
        loading.setObjectName("loadingLabel")
        loading.setAlignment(Qt.AlignCenter)
        self.body.addWidget(loading, 1)

        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self._escape_requested)
        self._load_details()

    def _load_details(self) -> None:
        worker = FunctionWorker(lambda: self.client.details(self.media["media_type"], int(self.media["id"])))
        worker.signals.success.connect(self._render)
        worker.signals.error.connect(self._show_error)
        _start_worker(self.thread_pool, worker)

    def _clear_body(self) -> None:
        while self.body.count():
            item = self.body.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            child_layout = item.layout()
            if child_layout:
                self._clear_nested_layout(child_layout)

    @staticmethod
    def _clear_nested_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            if item.layout():
                DetailDialog._clear_nested_layout(item.layout())

    def _discard_episode_picker(self) -> None:
        self._season_request_generation += 1
        if self._episode_panel_animation is not None:
            self._episode_panel_animation.stop()
            self._episode_panel_animation = None
        if self.episode_panel is not None:
            self.episode_panel.hide()
            self.episode_panel.deleteLater()
        self.episode_panel = None
        self.episode_panel_close_button = None
        self.episode_list_layout = None
        self.episode_scroll = None
        self.season_buttons = []
        self.episode_buttons = []
        self.watch_show_button = None
        self._active_season = None
        self._episode_picker_open = False
        self._episode_picker_animating = False
        self._episode_after_close = None

    def _episode_panel_target_geometry(self) -> QRect:
        margin = 28
        width = max(320, min(880, self.width() - margin * 2))
        height = max(300, min(520, self.height() - 112))
        button = self.watch_show_button
        if button is not None:
            button_top_left = self.mapFromGlobal(button.mapToGlobal(QPoint(0, 0)))
            desired_x = button_top_left.x()
            desired_y = button_top_left.y() + button.height() + 12
        else:
            desired_x = (self.width() - width) // 2
            desired_y = 84
        x = max(margin, min(desired_x, self.width() - width - margin))
        y = max(72, min(desired_y, self.height() - height - margin))
        return QRect(x, y, width, height)

    def _watch_button_geometry(self) -> QRect:
        button = self.watch_show_button
        if button is None:
            center = self.rect().center()
            return QRect(center.x(), center.y(), 1, 1)
        top_left = self.mapFromGlobal(button.mapToGlobal(QPoint(0, 0)))
        return QRect(top_left, button.size())

    def _build_episode_picker(self, media: dict[str, Any]) -> None:
        panel = QFrame(self)
        panel.setObjectName("episodePicker")
        panel.setAttribute(Qt.WA_StyledBackground, True)
        panel.hide()

        outer = QVBoxLayout(panel)
        outer.setContentsMargins(24, 20, 24, 22)
        outer.setSpacing(13)

        header = QHBoxLayout()
        title = QLabel("Episodes")
        title.setObjectName("episodePickerTitle")
        header.addWidget(title)
        header.addStretch(1)
        close = QPushButton("✕")
        close.setObjectName("episodePickerClose")
        close.setProperty("controllerSelected", False)
        close.setFocusPolicy(Qt.StrongFocus)
        close.setFixedSize(38, 38)
        close.clicked.connect(self.close_episode_picker)
        header.addWidget(close)
        outer.addLayout(header)

        season_hint = QLabel("Choose a season")
        season_hint.setObjectName("episodePickerHint")
        outer.addWidget(season_hint)

        season_scroll = QScrollArea()
        season_scroll.setObjectName("seasonScroll")
        season_scroll.setWidgetResizable(True)
        season_scroll.setFrameShape(QFrame.NoFrame)
        season_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        season_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        season_scroll.setFixedHeight(58)
        season_host = QWidget()
        season_host.setObjectName("transparentWidget")
        season_layout = QHBoxLayout(season_host)
        season_layout.setContentsMargins(0, 2, 0, 4)
        season_layout.setSpacing(9)

        seasons = self.watch_state.available_seasons(media)
        for season in seasons:
            number = int(season.get("season_number", 0) or 0)
            label = str(season.get("name") or ("Specials" if number == 0 else f"Season {number}"))
            button = QPushButton(label)
            button.setObjectName("seasonButton")
            button.setProperty("seasonNumber", number)
            button.setProperty("activeSeason", False)
            button.setProperty("controllerSelected", False)
            button.setFocusPolicy(Qt.StrongFocus)
            button.setMinimumWidth(112)
            button.clicked.connect(lambda _checked=False, n=number: self._select_season(n))
            season_layout.addWidget(button)
            self.season_buttons.append(button)
        season_layout.addStretch(1)
        season_host.setMinimumWidth(max(1, len(self.season_buttons)) * 121)
        season_scroll.setWidget(season_host)
        outer.addWidget(season_scroll)

        episode_hint = QLabel("Pick an episode")
        episode_hint.setObjectName("episodePickerHint")
        outer.addWidget(episode_hint)

        episode_scroll = QScrollArea()
        episode_scroll.setObjectName("episodeScroll")
        episode_scroll.setWidgetResizable(True)
        episode_scroll.setFrameShape(QFrame.NoFrame)
        episode_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        episode_host = QWidget()
        episode_host.setObjectName("transparentWidget")
        episode_layout = QVBoxLayout(episode_host)
        episode_layout.setContentsMargins(0, 0, 8, 4)
        episode_layout.setSpacing(9)
        episode_scroll.setWidget(episode_host)
        outer.addWidget(episode_scroll, 1)

        self.episode_panel = panel
        self.episode_panel_close_button = close
        self.episode_list_layout = episode_layout
        self.episode_scroll = episode_scroll

        if not seasons:
            self._show_episode_message("No seasons were returned for this show.", "mutedLabel")

    def _show_episode_message(self, text: str, object_name: str = "loadingLabel") -> None:
        layout = self.episode_list_layout
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.episode_buttons = []
        label = QLabel(text)
        label.setObjectName(object_name)
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addStretch(1)

    def is_episode_picker_open(self) -> bool:
        return self._episode_picker_open

    def toggle_episode_picker(self) -> None:
        if self._episode_picker_open:
            self.close_episode_picker()
        else:
            self.open_episode_picker()

    def open_episode_picker(self) -> None:
        panel = self.episode_panel
        if panel is None or self.media.get("media_type") != "tv":
            return
        if self._episode_panel_animation is not None:
            self._episode_panel_animation.stop()
        self._episode_picker_open = True
        self._episode_picker_animating = True
        self._resume_episode_position = self.watch_state.resume_episode(self.profile_id, self.media)
        season_number = self._resume_episode_position[0]
        available_numbers = [
            int(button.property("seasonNumber")) for button in self.season_buttons
        ]
        if season_number not in available_numbers and available_numbers:
            season_number = available_numbers[0]
            self._resume_episode_position = (season_number, 1)

        panel.setGeometry(self._watch_button_geometry())
        panel.show()
        panel.raise_()
        self._select_season(season_number, self._resume_episode_position[1])

        animation = QPropertyAnimation(panel, b"geometry", self)
        self._episode_panel_animation = animation
        animation.setDuration(360)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.setStartValue(panel.geometry())
        animation.setEndValue(self._episode_panel_target_geometry())

        def finish() -> None:
            self._episode_picker_animating = False
            self._episode_panel_animation = None
            if self.episode_panel is not None:
                self.episode_panel.setGeometry(self._episode_panel_target_geometry())
                self.episode_panel.raise_()
            target = self._find_episode_button(*self._resume_episode_position)
            if target is None:
                target = self._active_season_button()
            if target is not None:
                self.controlsReady.emit(target)

        animation.finished.connect(finish)
        animation.start()

        active = self._active_season_button()
        if active is not None:
            self.controlsReady.emit(active)

    def close_episode_picker(self) -> bool:
        panel = self.episode_panel
        if panel is None or not self._episode_picker_open:
            return False
        self._season_request_generation += 1
        if self._episode_panel_animation is not None:
            self._episode_panel_animation.stop()
        self._episode_picker_animating = True
        animation = QPropertyAnimation(panel, b"geometry", self)
        self._episode_panel_animation = animation
        animation.setDuration(300)
        animation.setEasingCurve(QEasingCurve.InOutCubic)
        animation.setStartValue(panel.geometry())
        animation.setEndValue(self._watch_button_geometry())

        def finish() -> None:
            if self.episode_panel is not None:
                self.episode_panel.hide()
            self._episode_picker_open = False
            self._episode_picker_animating = False
            self._episode_panel_animation = None
            after_close = self._episode_after_close
            self._episode_after_close = None
            if after_close is not None:
                after_close()
            elif self.watch_show_button is not None:
                self.controlsReady.emit(self.watch_show_button)

        animation.finished.connect(finish)
        animation.start()
        return True

    def _active_season_button(self) -> Optional[QPushButton]:
        return next(
            (
                button
                for button in self.season_buttons
                if int(button.property("seasonNumber")) == self._active_season
            ),
            None,
        )

    def _select_season(self, season_number: int, episode_number: Optional[int] = None) -> None:
        if not self._episode_picker_open:
            return
        self._active_season = int(season_number)
        for button in self.season_buttons:
            button.setProperty(
                "activeSeason",
                int(button.property("seasonNumber")) == self._active_season,
            )
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

        target_episode = int(episode_number or 1)
        if self._active_season == self._resume_episode_position[0]:
            target_episode = int(episode_number or self._resume_episode_position[1])
        self._show_episode_message("Loading episodes…")

        cached = self._season_cache.get(self._active_season)
        if cached is not None:
            self._render_season(cached, target_episode)
            return

        self._season_request_generation += 1
        generation = self._season_request_generation
        series_id = int(self.media.get("id", 0) or 0)
        selected_season = self._active_season
        worker = FunctionWorker(
            lambda: self.client.season_details(series_id, selected_season)
        )
        worker.signals.success.connect(
            lambda data, s=selected_season, e=target_episode, g=generation: self._season_loaded(
                s, e, g, data
            )
        )
        worker.signals.error.connect(
            lambda error, s=selected_season, g=generation: self._season_load_failed(s, g, error)
        )
        _start_worker(self.thread_pool, worker)

    def _season_loaded(
        self,
        season_number: int,
        episode_number: int,
        generation: int,
        data: dict[str, Any],
    ) -> None:
        self._season_cache[int(season_number)] = data
        if (
            generation != self._season_request_generation
            or not self._episode_picker_open
            or self._active_season != int(season_number)
        ):
            return
        self._render_season(data, episode_number)

    def _season_load_failed(self, season_number: int, generation: int, error: str) -> None:
        if (
            generation != self._season_request_generation
            or not self._episode_picker_open
            or self._active_season != int(season_number)
        ):
            return
        self._show_episode_message(f"Could not load this season.\n\n{error}", "errorLabel")

    def _render_season(self, season: dict[str, Any], target_episode: int) -> None:
        layout = self.episode_list_layout
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.episode_buttons = []

        episodes = [episode for episode in season.get("episodes", []) if isinstance(episode, dict)]
        for episode in episodes:
            season_number = int(episode.get("season_number", self._active_season or 1) or 1)
            episode_number = int(episode.get("episode_number", 1) or 1)
            state = self.watch_state.episode_entry(
                self.profile_id,
                self.media,
                season_number,
                episode_number,
            ) or {}
            is_resume = (season_number, episode_number) == self._resume_episode_position
            button = QPushButton(self._episode_button_text(episode, state, is_resume))
            button.setObjectName("episodeButton")
            button.setProperty("seasonNumber", season_number)
            button.setProperty("episodeNumber", episode_number)
            button.setProperty("episodeStatus", state.get("status", "unwatched"))
            button.setProperty("resumeTarget", is_resume)
            button.setProperty("controllerSelected", False)
            button.setFocusPolicy(Qt.StrongFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(86)
            button.setToolTip(episode.get("overview") or episode.get("name") or "")
            button.clicked.connect(
                lambda _checked=False, selected=dict(episode): self._watch_episode_clicked(selected)
            )
            layout.addWidget(button)
            self.episode_buttons.append(button)
        layout.addStretch(1)

        if not self.episode_buttons:
            self._show_episode_message("No episodes were returned for this season.", "mutedLabel")
            return

        target = self._find_episode_button(self._active_season or 1, target_episode)
        if target is None:
            target = self.episode_buttons[0]

        def reveal() -> None:
            if self.episode_scroll is not None and target is not None:
                self.episode_scroll.ensureWidgetVisible(target, 12, 24)
            if target is not None:
                self.controlsReady.emit(target)

        QTimer.singleShot(0, reveal)

    @staticmethod
    def _episode_button_text(
        episode: dict[str, Any],
        state: dict[str, Any],
        is_resume: bool,
    ) -> str:
        number = int(episode.get("episode_number", 1) or 1)
        name = episode.get("name") or f"Episode {number}"
        runtime = episode.get("runtime")
        status = state.get("status")
        icon = "✓" if status == "finished" else "▶"
        if is_resume:
            if status == "in_progress":
                action = "RESUME"
            elif status == "finished":
                action = "REPLAY"
            else:
                action = "UP NEXT"
        else:
            action = "WATCHED" if status == "finished" else ""
        meta = f"{int(runtime)} min" if runtime else (episode.get("air_date") or "")
        overview = " ".join(str(episode.get("overview") or "No description available.").split())
        if len(overview) > 105:
            overview = overview[:102].rstrip() + "…"
        action_text = f"   •   {action}" if action else ""
        meta_text = f"{meta}   •   " if meta else ""
        return f"{icon}   {number}. {name}{action_text}\n      {meta_text}{overview}"

    def _find_episode_button(
        self,
        season_number: int,
        episode_number: int,
    ) -> Optional[QPushButton]:
        return next(
            (
                button
                for button in self.episode_buttons
                if int(button.property("seasonNumber")) == int(season_number)
                and int(button.property("episodeNumber")) == int(episode_number)
            ),
            None,
        )

    def _episode_for_position(self, season_number: int, episode_number: int) -> dict[str, Any]:
        season = self._season_cache.get(int(season_number), {})
        for episode in season.get("episodes", []):
            if int(episode.get("episode_number", 0) or 0) == int(episode_number):
                return dict(episode)
        return {
            "season_number": int(season_number),
            "episode_number": int(episode_number),
            "name": f"Episode {int(episode_number)}",
        }

    def _episode_playback_payload(self, episode: dict[str, Any]) -> dict[str, Any]:
        season_number = int(episode.get("season_number", 1) or 1)
        episode_number = int(episode.get("episode_number", 1) or 1)
        show_title = self.media.get("title") or self.media.get("name") or "TV Show"
        payload = dict(episode)
        payload.update(
            {
                "media_type": "episode",
                "series_id": self.media.get("id"),
                "show_title": show_title,
                "title": (
                    f"{show_title} — S{season_number}:E{episode_number} — "
                    f"{episode.get('name') or f'Episode {episode_number}'}"
                ),
                "season_number": season_number,
                "episode_number": episode_number,
                "show": self.watch_state.snapshot(self.media),
            }
        )
        return payload

    def _watch_episode_clicked(self, episode: dict[str, Any]) -> None:
        payload = self._episode_playback_payload(episode)
        saved = self.watch_state.episode_entry(
            self.profile_id,
            self.media,
            int(payload["season_number"]),
            int(payload["episode_number"]),
        ) or {}
        start_seconds = int(float(saved.get("position_seconds", 0.0) or 0.0))
        try:
            embed_url = getshow(
                int(self.media.get("id", 0) or 0),
                int(payload["season_number"]),
                int(payload["episode_number"]),
            )
        except (PlaybackAPIError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Playback unavailable", str(exc))
            return

        self.watch_state.mark_episode_started(self.profile_id, self.media, episode)
        self.stateChanged.emit()
        media = dict(self.media)
        episode_for_progress = dict(episode)

        def save_progress(position: float, duration: float) -> None:
            self.watch_state.set_episode_position(
                self.profile_id,
                media,
                episode_for_progress,
                position,
                duration,
            )
            self.stateChanged.emit()

        def start_playback() -> None:
            self._render(media)
            self._open_playback_dialog(
                str(payload["title"]),
                embed_url,
                save_progress,
                resume_seconds=start_seconds,
            )

        self._episode_after_close = start_playback
        if not self.close_episode_picker():
            self._episode_after_close = None
            start_playback()

    def _active_playback_dialog(self) -> Optional[PlaybackDialog]:
        dialog = self._playback_dialog
        if dialog is None:
            return None
        try:
            if dialog.isVisible():
                return dialog
        except RuntimeError:
            pass
        self._playback_dialog = None
        return None

    def _active_player_dialog(self) -> Optional[TrailerDialog]:
        return self._active_playback_dialog() or self._active_trailer_dialog()

    def _discard_playback_state(self) -> None:
        dialog = self._playback_dialog
        self._playback_dialog = None
        if dialog is None:
            return
        try:
            dialog.close()
        except RuntimeError:
            pass

    def _open_playback_dialog(
        self,
        title: str,
        embed_url: str,
        progress_callback: Callable[[float, float], None],
        *,
        resume_seconds: float = 0.0,
    ) -> None:
        active = self._active_player_dialog()
        if active is not None:
            active.raise_()
            active.activateWindow()
            return

        self.close_episode_picker()
        dialog = PlaybackDialog(
            title,
            embed_url,
            self,
            resume_seconds=resume_seconds,
        )
        self._playback_dialog = dialog
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        dialog.controlsReady.connect(self.controlsReady.emit)
        dialog.progressReported.connect(progress_callback)
        dialog.finished.connect(lambda _result, d=dialog: self._playback_finished(d))
        dialog.open()

    def _playback_finished(self, dialog: PlaybackDialog) -> None:
        if self._playback_dialog is dialog:
            self._playback_dialog = None
        preferred = self.watch_show_button or self.preferred_controller_widget
        if preferred is not None:
            self.controlsReady.emit(preferred)

    def _active_trailer_dialog(self) -> Optional[TrailerDialog]:
        dialog = self._trailer_dialog
        if dialog is None:
            return None
        try:
            if dialog.isVisible():
                return dialog
        except RuntimeError:
            pass
        self._trailer_dialog = None
        return None

    def _discard_trailer_state(self) -> None:
        dialog = self._trailer_dialog
        self._trailer_dialog = None
        if dialog is None:
            return
        try:
            dialog.close()
        except RuntimeError:
            pass

    def _watch_trailer_clicked(self) -> None:
        if not self.trailer:
            return
        active = self._active_trailer_dialog()
        if active is not None:
            active.raise_()
            active.activateWindow()
            return

        self.close_episode_picker()
        dialog = TrailerDialog(
            str(self.media.get("title") or "Trailer"),
            self.trailer,
            self,
        )
        self._trailer_dialog = dialog
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        dialog.controlsReady.connect(self.controlsReady.emit)
        dialog.finished.connect(lambda _result, d=dialog: self._trailer_finished(d))
        dialog.open()

    def _trailer_finished(self, dialog: TrailerDialog) -> None:
        if self._trailer_dialog is dialog:
            self._trailer_dialog = None
        preferred = self.trailer_button or self.preferred_controller_widget
        if preferred is not None:
            self.controlsReady.emit(preferred)

    def is_player_screen_open(self) -> bool:
        return self._active_player_dialog() is not None

    def is_player_fullscreen(self) -> bool:
        dialog = self._active_player_dialog()
        return bool(dialog is not None and dialog.is_fullscreen())

    def exit_player_fullscreen(self) -> bool:
        dialog = self._active_player_dialog()
        return bool(dialog is not None and dialog.exit_fullscreen())

    def pause_fullscreen_player(self) -> None:
        dialog = self._active_player_dialog()
        if dialog is not None:
            dialog.pause_fullscreen()

    def controller_back(self) -> bool:
        dialog = self._active_player_dialog()
        if dialog is not None:
            return dialog.controller_back()
        if self.close_episode_picker():
            return True
        return False

    def _escape_requested(self) -> None:
        if self.controller_back():
            return
        self.reject()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if (
            self._episode_picker_open
            and not self._episode_picker_animating
            and self.episode_panel is not None
        ):
            self.episode_panel.setGeometry(self._episode_panel_target_geometry())
            self.episode_panel.raise_()

    def closeEvent(self, event) -> None:
        self._discard_playback_state()
        self._discard_trailer_state()
        if self._episode_panel_animation is not None:
            self._episode_panel_animation.stop()
        super().closeEvent(event)

    def _render(self, media: dict[str, Any]) -> None:
        self.media = media
        self.controller_actions = []
        self.preferred_controller_widget = None
        self.trailer_button = None
        self._discard_playback_state()
        self._discard_trailer_state()
        self._discard_episode_picker()
        self._clear_body()

        hero = QFrame()
        hero.setObjectName("detailHero")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(0, 0, 0, 0)
        hero_layout.setSpacing(24)

        poster = RemoteImage(self.thread_pool, 230, 345, radius=10)
        if media.get("poster_path"):
            poster.load(f"{TMDB_IMAGE_BASE}/w342{media['poster_path']}")
        hero_layout.addWidget(poster, 0, Qt.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(12)
        info.setContentsMargins(0, 6, 0, 0)

        title = QLabel(media.get("title", "Untitled"))
        title.setObjectName("detailTitle")
        title.setWordWrap(True)
        info.addWidget(title)

        entry = self.watch_state.entry(self.profile_id, media)
        if entry and entry.get("status") == "finished":
            watched_label = QLabel("✓  Watched")
            watched_label.setObjectName("detailWatchedBadge")
            info.addWidget(watched_label, 0, Qt.AlignLeft)
        elif entry and entry.get("status") == "in_progress":
            latest_episode = self.watch_state.latest_episode_entry(self.profile_id, media)
            if media.get("media_type") == "tv" and latest_episode is not None:
                resume_season, resume_episode = self.watch_state.resume_episode(self.profile_id, media)
                if latest_episode.get("status") == "finished":
                    progress_text = f"Up next  •  S{resume_season}:E{resume_episode}"
                else:
                    progress_pct = max(
                        1,
                        int(float(latest_episode.get("progress", 0.0) or 0.0) * 100),
                    )
                    progress_text = (
                        f"Continue watching  •  S{resume_season}:E{resume_episode}  •  "
                        f"{progress_pct}%"
                    )
            else:
                progress_pct = max(1, int(float(entry.get("progress", 0.0)) * 100))
                progress_text = f"Continue watching  •  {progress_pct}%"
            progress_label = QLabel(progress_text)
            progress_label.setObjectName("continueLabel")
            info.addWidget(progress_label, 0, Qt.AlignLeft)

        meta_bits = []
        if media.get("year"):
            meta_bits.append(media["year"])
        runtime = media.get("runtime")
        if not runtime and media.get("episode_run_time"):
            runtime = media["episode_run_time"][0] if media["episode_run_time"] else None
        if runtime:
            h, m = divmod(int(runtime), 60)
            meta_bits.append(f"{h}h {m}m" if h else f"{m}m")
        rating = media.get("vote_average")
        if rating:
            meta_bits.append(f"★ {rating:.1f}/10")
        meta_bits.append("TV Series" if media.get("media_type") == "tv" else "Movie")
        meta = QLabel("   •   ".join(meta_bits))
        meta.setObjectName("detailMeta")
        info.addWidget(meta)

        genres = ", ".join(g.get("name", "") for g in media.get("genres", [])[:4] if g.get("name"))
        if genres:
            genre_label = QLabel(genres)
            genre_label.setObjectName("genreLabel")
            genre_label.setWordWrap(True)
            info.addWidget(genre_label)

        overview = QLabel(media.get("overview") or "No description is available for this title.")
        overview.setObjectName("overviewLabel")
        overview.setWordWrap(True)
        overview.setMaximumWidth(700)
        info.addWidget(overview)

        cast_names = [c.get("name") for c in media.get("credits", {}).get("cast", [])[:5] if c.get("name")]
        if cast_names:
            cast = QLabel("Cast: " + ", ".join(cast_names))
            cast.setObjectName("castLabel")
            cast.setWordWrap(True)
            info.addWidget(cast)

        buttons = QHBoxLayout()
        watch_word = "Show" if media.get("media_type") == "tv" else "Movie"
        watch_button = QPushButton(f"▶  Watch {watch_word}")
        watch_button.setObjectName("watchButton")
        watch_button.setProperty("controllerSelected", False)
        watch_button.setCursor(Qt.PointingHandCursor)
        watch_button.setFocusPolicy(Qt.StrongFocus)
        if media.get("media_type") == "tv":
            self.watch_show_button = watch_button
            watch_button.clicked.connect(self.toggle_episode_picker)
        else:
            watch_button.clicked.connect(lambda: self._watch_clicked(media))
        buttons.addWidget(watch_button)
        self.controller_actions.append(watch_button)
        self.preferred_controller_widget = watch_button

        entry = self.watch_state.entry(self.profile_id, media)
        if entry and entry.get("status") == "in_progress":
            if media.get("media_type") == "tv":
                finished = QPushButton("✓  Mark Episode Finished")
                finished.clicked.connect(lambda: self._mark_current_episode_finished(media))
            else:
                finished = QPushButton("✓  Mark as Finished")
                finished.clicked.connect(lambda: self._mark_finished(media))
            finished.setObjectName("secondaryButton")
            finished.setProperty("controllerSelected", False)
            finished.setFocusPolicy(Qt.StrongFocus)
            buttons.addWidget(finished)
            self.controller_actions.append(finished)
        elif entry and entry.get("status") == "finished":
            unwatched = QPushButton("Mark as Unwatched")
            unwatched.setObjectName("secondaryButton")
            unwatched.setProperty("controllerSelected", False)
            unwatched.setFocusPolicy(Qt.StrongFocus)
            unwatched.clicked.connect(lambda: self._mark_unwatched(media))
            buttons.addWidget(unwatched)
            self.controller_actions.append(unwatched)

        self.trailer = self._pick_trailer(media)
        if self.trailer:
            trailer_button = QPushButton("▶  Watch Trailer")
            self.trailer_button = trailer_button
            trailer_button.setObjectName("secondaryButton")
            trailer_button.setProperty("controllerSelected", False)
            trailer_button.setFocusPolicy(Qt.StrongFocus)
            trailer_button.clicked.connect(self._watch_trailer_clicked)
            buttons.addWidget(trailer_button)
            self.controller_actions.append(trailer_button)
        buttons.addStretch(1)
        info.addLayout(buttons)
        info.addStretch(1)
        hero_layout.addLayout(info, 1)
        self.body.addWidget(hero)

        if media.get("media_type") == "tv":
            self._build_episode_picker(media)
        self.body.addStretch(1)

        if self.preferred_controller_widget is not None:
            self.controlsReady.emit(self.preferred_controller_widget)

    def controller_focusables(self) -> list[QWidget]:
        player_dialog = self._active_player_dialog()
        if player_dialog is not None:
            return player_dialog.controller_focusables()
        if self._episode_picker_open:
            widgets: list[QWidget] = [
                button
                for button in self.season_buttons + self.episode_buttons
                if button is not None and button.isVisible() and button.isEnabled()
            ]
            if (
                self.episode_panel_close_button is not None
                and self.episode_panel_close_button.isVisible()
            ):
                widgets.append(self.episode_panel_close_button)
            return widgets
        widgets = [
            button
            for button in self.controller_actions
            if button is not None and button.isVisible() and button.isEnabled()
        ]
        if getattr(self, "close_button", None) is not None and self.close_button.isVisible():
            widgets.append(self.close_button)
        return widgets

    def controller_current_target(self, current: Optional[QWidget] = None) -> Optional[QWidget]:
        player_dialog = self._active_player_dialog()
        if player_dialog is not None:
            return player_dialog.controller_current_target(current)
        focusables = self.controller_focusables()
        if current in focusables:
            return current
        if self._episode_picker_open:
            target = self._find_episode_button(*self._resume_episode_position)
            if target in focusables:
                return target
            active_season = self._active_season_button()
            if active_season in focusables:
                return active_season
            return focusables[0] if focusables else None
        if self.preferred_controller_widget in focusables:
            return self.preferred_controller_widget
        return focusables[0] if focusables else None

    def controller_move_target(
        self,
        direction: str,
        current: Optional[QWidget] = None,
    ) -> Optional[QWidget]:
        """Return the next details control without relying on WebEngine focus."""
        player_dialog = self._active_player_dialog()
        if player_dialog is not None:
            return player_dialog.controller_move_target(direction, current)
        if self._episode_picker_open:
            focusables = self.controller_focusables()
            current = self.controller_current_target(current)
            panel_close = self.episode_panel_close_button
            seasons = [button for button in self.season_buttons if button in focusables]
            episodes = [button for button in self.episode_buttons if button in focusables]

            if current is panel_close:
                if direction == "down":
                    active = self._active_season_button()
                    return active if active in seasons else (seasons[0] if seasons else current)
                return current

            if current in seasons:
                index = seasons.index(current)
                if direction == "left":
                    return seasons[(index - 1) % len(seasons)]
                if direction == "right":
                    return seasons[(index + 1) % len(seasons)]
                if direction == "up" and panel_close is not None:
                    return panel_close
                if direction == "down" and episodes:
                    target = self._find_episode_button(*self._resume_episode_position)
                    return target if target in episodes else episodes[0]
                return current

            if current in episodes:
                index = episodes.index(current)
                if direction == "up":
                    if index > 0:
                        return episodes[index - 1]
                    active = self._active_season_button()
                    return active if active in seasons else current
                if direction == "down":
                    return episodes[min(index + 1, len(episodes) - 1)]
                return current

            return self.controller_current_target()

        actions = [
            button
            for button in self.controller_actions
            if button is not None and button.isVisible() and button.isEnabled()
        ]
        close_button = getattr(self, "close_button", None)
        current = self.controller_current_target(current)

        if current is close_button:
            if actions and direction in {"down", "left", "right"}:
                return actions[0]
            return close_button

        if current in actions:
            index = actions.index(current)
            if direction == "left":
                return actions[(index - 1) % len(actions)]
            if direction == "right":
                return actions[(index + 1) % len(actions)]
            if direction == "up" and close_button is not None:
                return close_button
            return current

        return self.controller_current_target()

    @staticmethod
    def _pick_trailer(media: dict[str, Any]) -> Optional[dict[str, Any]]:
        videos = media.get("videos", {}).get("results", [])
        youtube = [v for v in videos if v.get("site") == "YouTube" and v.get("key")]
        trailers = [v for v in youtube if v.get("type") == "Trailer"]
        official = [v for v in trailers if v.get("official")]
        return (official or trailers or youtube)[0] if youtube else None

    def _watch_clicked(self, media: dict[str, Any]) -> None:
        if media.get("media_type") == "tv":
            self.toggle_episode_picker()
            return
        saved = self.watch_state.entry(self.profile_id, media) or {}
        start_seconds = int(float(saved.get("position_seconds", 0.0) or 0.0))
        try:
            embed_url = getmovie(int(media.get("id", 0) or 0))
        except (PlaybackAPIError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Playback unavailable", str(exc))
            return

        self.watch_state.mark_started(self.profile_id, media)
        self.stateChanged.emit()
        self._render(media)
        media_for_progress = dict(media)
        self._open_playback_dialog(
            str(media.get("title") or "Movie"),
            embed_url,
            lambda position, duration: self.watch_state.set_position(
                self.profile_id,
                media_for_progress,
                position,
                duration,
            ),
            resume_seconds=start_seconds,
        )

    def _mark_finished(self, media: dict[str, Any]) -> None:
        self.watch_state.mark_finished(self.profile_id, media)
        self.stateChanged.emit()
        self._render(media)

    def _mark_current_episode_finished(self, media: dict[str, Any]) -> None:
        season_number, episode_number = self.watch_state.resume_episode(self.profile_id, media)
        episode = self._episode_for_position(season_number, episode_number)
        self.watch_state.mark_episode_finished(self.profile_id, media, episode)
        self.stateChanged.emit()
        self._render(media)
        QTimer.singleShot(0, self.open_episode_picker)

    def _mark_unwatched(self, media: dict[str, Any]) -> None:
        self.watch_state.mark_unwatched(self.profile_id, media)
        self.stateChanged.emit()
        self._render(media)

    def _show_error(self, error: str) -> None:
        self._discard_playback_state()
        self._discard_trailer_state()
        self._clear_body()
        label = QLabel(f"Could not load this title.\n\n{error}")
        label.setObjectName("errorLabel")
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        self.body.addWidget(label, 1)


class HeroBanner(QFrame):
    def __init__(
        self,
        media: dict[str, Any],
        thread_pool: QThreadPool,
        open_details: Callable[[dict[str, Any]], None],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("heroBanner")
        self.setMinimumHeight(400)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(36, 34, 20, 34)
        layout.setSpacing(24)

        text_col = QVBoxLayout()
        text_col.addStretch(1)
        badge = QLabel("FEATURED")
        badge.setObjectName("heroBadge")
        text_col.addWidget(badge, 0, Qt.AlignLeft)

        title = QLabel(media.get("title", "Featured"))
        title.setObjectName("heroTitle")
        title.setWordWrap(True)
        title.setMaximumWidth(580)
        text_col.addWidget(title)

        overview = QLabel(media.get("overview") or "Discover something worth watching.")
        overview.setObjectName("heroOverview")
        overview.setWordWrap(True)
        overview.setMaximumWidth(580)
        overview.setMaximumHeight(100)
        text_col.addWidget(overview)

        meta = []
        if media.get("year"):
            meta.append(media["year"])
        if media.get("vote_average"):
            meta.append(f"★ {media['vote_average']:.1f}")
        meta.append("Series" if media.get("media_type") == "tv" else "Movie")
        meta_label = QLabel("   •   ".join(meta))
        meta_label.setObjectName("heroMeta")
        text_col.addWidget(meta_label)

        more = QPushButton("ⓘ  More Info")
        more.setObjectName("heroButton")
        more.setProperty("controllerSelected", False)
        more.setCursor(Qt.PointingHandCursor)
        more.clicked.connect(lambda: open_details(media))
        text_col.addWidget(more, 0, Qt.AlignLeft)
        text_col.addStretch(1)
        layout.addLayout(text_col, 3)

        backdrop = RemoteImage(thread_pool, 520, 330, radius=14)
        path = media.get("backdrop_path") or media.get("poster_path")
        if path:
            size = "w780" if media.get("backdrop_path") else "w500"
            backdrop.load(f"{TMDB_IMAGE_BASE}/{size}{path}")
        layout.addWidget(backdrop, 2, Qt.AlignRight | Qt.AlignVCenter)


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.client: Optional[TMDBClient] = TMDBClient(config.tmdb_read_token) if config.tmdb_read_token else None
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(RUNTIME.api_threads)
        self.thread_pool.setExpiryTimeout(3000)
        QPixmapCache.setCacheLimit(max(4096, RUNTIME.image_memory_cache_bytes // 1024))
        self.watch_state = WatchStateStore()
        self.active_profile_id: Optional[str] = None
        self.home_sections: list[tuple[str, list[dict[str, Any]]]] = []
        self.home_payload: Optional[dict[str, Any]] = None
        self._home_content_layout: Optional[QVBoxLayout] = None
        self._continue_row: Optional[MediaRow] = None
        self._continue_insert_index = 0
        self.profile_manage_mode = False
        self._controller_selected: Optional[QWidget] = None
        self._controller_keyboard: Optional[OnScreenKeyboard] = None
        self._profile_edit_dialog: Optional[QDialog] = None
        self._detail_dialog: Optional[DetailDialog] = None
        self._detail_state_dirty = False
        self._mouse_drag_scroll: Optional[HorizontalMediaScrollArea] = None
        self._search_generation = 0
        self._search_inflight_query = ""
        self._home_loading = False
        self._focusable_cache_root: Optional[QWidget] = None
        self._focusable_cache: list[QWidget] = []
        self._focusable_cache_dirty = True

        self.setWindowTitle(APP_NAME)
        self.resize(1440, 900)
        self.setMinimumSize(980, 650)
        _ensure_app_stylesheet()

        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.header = self._build_header()
        root_layout.addWidget(self.header)

        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack, 1)

        self.profile_page = QWidget()
        self.profile_page.setObjectName("profilePage")
        self.profile_layout = QVBoxLayout(self.profile_page)
        self.profile_layout.setContentsMargins(40, 40, 40, 40)
        self.profile_layout.setSpacing(16)
        self.stack.addWidget(self.profile_page)

        self.home_page = QWidget()
        self.home_page.setObjectName("page")
        self.home_layout = QVBoxLayout(self.home_page)
        self.home_layout.setContentsMargins(0, 0, 0, 0)
        self.home_layout.setSpacing(0)
        self.stack.addWidget(self.home_page)

        self.search_page = QWidget()
        self.search_page.setObjectName("page")
        self.search_layout = QVBoxLayout(self.search_page)
        self.search_layout.setContentsMargins(28, 20, 28, 28)
        self.search_layout.setSpacing(16)
        self.stack.addWidget(self.search_page)

        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(380)
        self.search_timer.timeout.connect(self._perform_search)

        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._focus_search)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self._escape_action)

        self.controller = ControllerManager(self)
        self.controller.connectionChanged.connect(self._controller_connection_changed)
        self.controller.navigate.connect(self._controller_navigate)
        self.controller.activate.connect(self._controller_activate)
        self.controller.back.connect(self._controller_back)
        self.controller.pause.connect(self._controller_pause)

        QApplication.instance().installEventFilter(self)

        # Always boot into the profile chooser. Setting the stack page here makes
        # it explicit even before the window is shown; rebuilding it on the next
        # event-loop tick ensures all profile widgets are laid out visibly.
        self.header.hide()
        self.stack.setCurrentWidget(self.profile_page)
        QTimer.singleShot(0, self._show_profile_picker)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        # Mouse/physical keyboard input turns off controller-only selection visuals.
        # ControllerManager does not generate Qt key events, so it will not trip this.
        if event.spontaneous() and event.type() in {
            QEvent.MouseButtonPress,
            QEvent.MouseMove,
            QEvent.Wheel,
            QEvent.TouchBegin,
        }:
            self._clear_controller_selection()
        elif event.spontaneous() and event.type() == QEvent.KeyPress:
            self._clear_controller_selection()

        # Left-dragging anywhere on a row is mouse-native horizontal scrolling.
        # A movement past the threshold consumes the release so the poster does
        # not accidentally open after a drag.
        if event.type() == QEvent.MouseButtonPress and isinstance(watched, QWidget):
            if event.button() == Qt.LeftButton:
                row_scroll = self._find_ancestor(watched, HorizontalMediaScrollArea)
                if row_scroll is not None:
                    self._mouse_drag_scroll = row_scroll
                    row_scroll.begin_mouse_drag(event)
        elif event.type() == QEvent.MouseMove and self._mouse_drag_scroll is not None:
            try:
                if self._mouse_drag_scroll.update_mouse_drag(event):
                    return True
            except RuntimeError:
                self._mouse_drag_scroll = None
        elif event.type() == QEvent.MouseButtonRelease and self._mouse_drag_scroll is not None:
            active_drag = self._mouse_drag_scroll
            self._mouse_drag_scroll = None
            try:
                if event.button() == Qt.LeftButton and active_drag.end_mouse_drag():
                    event.accept()
                    return True
            except RuntimeError:
                pass

        # Intercept wheel/trackpad input before poster/title child widgets can
        # consume it. Any vertical component over a movie row scrolls the main
        # page; the carousel itself has no vertical movement.
        if event.type() == QEvent.Wheel and isinstance(watched, QWidget):
            row_scroll = self._find_ancestor(watched, HorizontalMediaScrollArea)
            if row_scroll is not None and row_scroll.handle_filtered_wheel(event):
                return True

        return super().eventFilter(watched, event)

    @staticmethod
    def _find_ancestor(widget: QWidget, cls):
        current: Optional[QWidget] = widget
        while current is not None:
            if isinstance(current, cls):
                return current
            current = current.parentWidget()
        return None

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(28, 13, 28, 13)
        layout.setSpacing(16)

        brand = QLabel("PISTICK")
        brand.setObjectName("brand")
        brand.setCursor(Qt.PointingHandCursor)
        brand.mousePressEvent = lambda _e: self._show_home()
        layout.addWidget(brand)

        home = QPushButton("Home")
        home.setObjectName("navButton")
        home.setProperty("controllerSelected", False)
        home.setMinimumWidth(72)
        home.clicked.connect(self._show_home)
        layout.addWidget(home)

        movies = QPushButton("Movies")
        movies.setObjectName("navButton")
        movies.setProperty("controllerSelected", False)
        movies.setMinimumWidth(78)
        movies.clicked.connect(lambda: self._quick_search_type("movie"))
        layout.addWidget(movies)

        shows = QPushButton("TV Shows")
        shows.setObjectName("navButton")
        shows.setProperty("controllerSelected", False)
        shows.setMinimumWidth(94)
        shows.clicked.connect(lambda: self._quick_search_type("tv"))
        layout.addWidget(shows)

        layout.addStretch(1)

        self.search_box = QLineEdit()
        self.search_box.setObjectName("searchBox")
        self.search_box.setProperty("controllerSelected", False)
        self.search_box.setPlaceholderText("Search movies and shows…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setFixedWidth(330)
        self.search_box.textChanged.connect(self._on_search_changed)
        self.search_box.returnPressed.connect(self._perform_search)
        layout.addWidget(self.search_box)

        self.controller_status = QLabel("🎮 Controller")
        self.controller_status.setObjectName("controllerStatus")
        self.controller_status.hide()
        layout.addWidget(self.controller_status)

        self.profile_button = QPushButton("Profile")
        self.profile_button.setObjectName("profileButton")
        self.profile_button.setProperty("controllerSelected", False)
        self.profile_button.clicked.connect(self._show_profile_picker)
        layout.addWidget(self.profile_button)
        return header

    # ------------------------------------------------------------------ profiles
    def _show_profile_picker(self) -> None:
        self._clear_controller_selection()
        self.header.hide()
        self._clear_layout(self.profile_layout)

        self.profile_layout.addStretch(2)
        title = QLabel("Manage Profiles" if self.profile_manage_mode else "Who's watching?")
        title.setObjectName("profilesTitle")
        title.setAlignment(Qt.AlignCenter)
        self.profile_layout.addWidget(title)

        grid_host = QWidget()
        grid_layout = QHBoxLayout(grid_host)
        grid_layout.setContentsMargins(0, 22, 0, 16)
        grid_layout.setSpacing(22)
        grid_layout.addStretch(1)

        for profile in self.watch_state.profiles()[:5]:
            card = ProfileCard(
                profile,
                on_activate=lambda p=profile: self._profile_card_clicked(p),
                manage_mode=self.profile_manage_mode,
            )
            grid_layout.addWidget(card)

        if len(self.watch_state.profiles()) < 5 and not self.profile_manage_mode:
            add_card = ProfileCard(None, self._add_profile_clicked, add_profile=True)
            grid_layout.addWidget(add_card)

        grid_layout.addStretch(1)
        self.profile_layout.addWidget(grid_host)

        manage = QPushButton("Done" if self.profile_manage_mode else "Manage Profiles")
        manage.setObjectName("manageProfilesButton")
        manage.setProperty("controllerSelected", False)
        manage.setFixedHeight(46)
        manage.clicked.connect(self._toggle_manage_profiles)
        self.profile_layout.addWidget(manage, 0, Qt.AlignCenter)
        self.profile_layout.addStretch(3)

        self.stack.setCurrentWidget(self.profile_page)

    def _profile_card_clicked(self, profile: dict[str, Any]) -> None:
        if self.profile_manage_mode:
            self._open_profile_edit(profile)
        else:
            self._select_profile(profile)

    def _select_profile(self, profile: dict[str, Any]) -> None:
        self.active_profile_id = profile.get("id")
        self.watch_state.set_active_profile(self.active_profile_id)
        self.profile_manage_mode = False
        self.profile_button.setText(f"●  {profile.get('name', 'Profile')}")
        self.header.show()
        self.controller_status.setVisible(self.controller.connected)
        if not self.config.tmdb_read_token:
            self._show_setup_screen()
        elif self.home_payload is None:
            self._load_home()
        else:
            self._render_home(self.home_payload)

    def _toggle_manage_profiles(self) -> None:
        self.profile_manage_mode = not self.profile_manage_mode
        self._show_profile_picker()

    def _add_profile_clicked(self) -> None:
        if self.controller.connected:
            self._open_text_keyboard("Add Profile", "", "Add", self._create_profile)
            return
        name, ok = TextInputDialog.get_text(self, "Add Profile", "Profile name:")
        if ok and name.strip():
            self._create_profile(name.strip())

    def _create_profile(self, name: str) -> None:
        if name.strip():
            self.watch_state.add_profile(name.strip())
            self._show_profile_picker()

    def _open_profile_edit(self, profile: dict[str, Any]) -> None:
        if self._profile_edit_dialog is not None:
            return
        dialog = QDialog(self)
        self._profile_edit_dialog = dialog
        dialog.setObjectName("profileEditDialog")
        dialog.setModal(True)
        dialog.setWindowTitle("Edit Profile")
        dialog.setFixedSize(430, 300)
        _ensure_app_stylesheet()

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(34, 30, 34, 30)
        layout.setSpacing(15)
        title = QLabel(profile.get("name", "Profile"))
        title.setObjectName("profileEditTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        rename = QPushButton("Rename Profile")
        rename.setObjectName("secondaryButton")
        rename.setProperty("controllerSelected", False)
        rename.clicked.connect(lambda: self._rename_profile_clicked(profile, dialog))
        layout.addWidget(rename)

        delete = QPushButton("Delete Profile")
        delete.setObjectName("dangerButton")
        delete.setProperty("controllerSelected", False)
        delete.clicked.connect(lambda: self._delete_profile_clicked(profile, dialog))
        layout.addWidget(delete)

        done = QPushButton("Done")
        done.setObjectName("watchButton")
        done.setProperty("controllerSelected", False)
        done.clicked.connect(dialog.accept)
        layout.addWidget(done)

        dialog.finished.connect(lambda _r: self._profile_edit_closed())
        dialog.open()
        QTimer.singleShot(60, lambda: self._select_first_controller_widget(dialog) if self.controller.connected else None)

    def _profile_edit_closed(self) -> None:
        self._profile_edit_dialog = None
        self._clear_controller_selection()
        self._show_profile_picker()

    def _rename_profile_clicked(self, profile: dict[str, Any], edit_dialog: QDialog) -> None:
        if self.controller.connected:
            edit_dialog.hide()

            def finish(name: str) -> None:
                self.watch_state.rename_profile(profile["id"], name)
                edit_dialog.accept()

            self._open_text_keyboard("Rename Profile", profile.get("name", ""), "Save", finish)
            return
        name, ok = TextInputDialog.get_text(self, "Rename Profile", "Profile name:", profile.get("name", ""))
        if ok and name.strip():
            self.watch_state.rename_profile(profile["id"], name)
            edit_dialog.accept()

    def _delete_profile_clicked(self, profile: dict[str, Any], dialog: QDialog) -> None:
        if len(self.watch_state.profiles()) <= 1:
            QMessageBox.information(self, "PiStick", "You need at least one profile.")
            return
        reply = QMessageBox.question(
            self,
            "Delete Profile",
            f"Delete {profile.get('name', 'this profile')} and its watch history?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.watch_state.delete_profile(profile["id"])
            dialog.accept()

    # ------------------------------------------------------------------- setup/home
    def _show_setup_screen(self) -> None:
        self._clear_layout(self.home_layout)
        wrapper = QWidget()
        box = QVBoxLayout(wrapper)
        box.setContentsMargins(70, 70, 70, 70)
        box.addStretch(1)
        title = QLabel("Connect TMDB")
        title.setObjectName("setupTitle")
        title.setAlignment(Qt.AlignCenter)
        box.addWidget(title)
        body = QLabel(
            "PiStick uses TMDB for titles, posters, descriptions, search results, and trailers.\n\n"
            "1. Create a free TMDB account and request API access.\n"
            "2. Copy your API Read Access Token.\n"
            "3. Copy config.example.json to config.json and paste your token and legal playback base URL.\n"
            "4. Restart this app.\n\n"
            "You can also set the TMDB_READ_TOKEN environment variable instead."
        )
        body.setObjectName("setupBody")
        body.setAlignment(Qt.AlignCenter)
        body.setWordWrap(True)
        box.addWidget(body)
        open_docs = QPushButton("Open TMDB API page")
        open_docs.setObjectName("watchButton")
        open_docs.setProperty("controllerSelected", False)
        open_docs.clicked.connect(lambda: webbrowser.open("https://www.themoviedb.org/settings/api"))
        box.addWidget(open_docs, 0, Qt.AlignCenter)
        box.addStretch(1)
        self.home_layout.addWidget(wrapper)
        self.stack.setCurrentWidget(self.home_page)

    def _load_home(self) -> None:
        if not self.client or self._home_loading:
            return
        self._home_loading = True
        self._home_content_layout = None
        self._continue_row = None
        self._clear_layout(self.home_layout)
        loading = QLabel("Loading your home screen…")
        loading.setObjectName("loadingLabel")
        loading.setAlignment(Qt.AlignCenter)
        self.home_layout.addWidget(loading, 1)

        def fetch_home():
            trending = self.client.trending()
            return {
                "trending": trending,
                "sections": [
                    ("Trending Now", trending),
                    ("Popular Movies", self.client.popular_movies()),
                    ("Popular TV Shows", self.client.popular_tv()),
                    ("Top Rated Movies", self.client.top_rated_movies()),
                    ("Coming Soon", self.client.upcoming_movies()),
                ],
            }

        worker = FunctionWorker(fetch_home)
        worker.signals.success.connect(self._render_home)
        worker.signals.error.connect(self._show_home_error)
        _start_worker(self.thread_pool, worker)

    def _watch_entry(self, media: dict[str, Any]) -> Optional[dict[str, Any]]:
        return self.watch_state.entry(self.active_profile_id, media)

    def _render_home(self, payload: dict[str, Any]) -> None:
        self._home_loading = False
        self.home_payload = payload
        self._home_content_layout = None
        self._continue_row = None
        self._clear_layout(self.home_layout)
        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("mainScroll")

        content = QWidget()
        content.setObjectName("page")
        content_layout = QVBoxLayout(content)
        self._home_content_layout = content_layout
        content_layout.setContentsMargins(24, 14, 24, 36)
        content_layout.setSpacing(28)

        trending = payload.get("trending") or []
        if trending:
            hero_candidates = [x for x in trending if x.get("backdrop_path")]
            content_layout.addWidget(HeroBanner((hero_candidates or trending)[0], self.thread_pool, self.open_details))

        self._continue_insert_index = content_layout.count()

        # Keep each in-progress title visible exactly once. A short personal
        # list should not be padded with infinite-row clones of the same movie.
        continue_items = self.watch_state.continue_watching(self.active_profile_id)
        if continue_items:
            self._continue_row = MediaRow(
                "Continue Watching",
                continue_items,
                self.thread_pool,
                self.open_details,
                state_lookup=self._watch_entry,
                show_progress=True,
                infinite=False,
            )
            content_layout.addWidget(self._continue_row)

        self.home_sections = payload.get("sections", [])
        for title, items in self.home_sections:
            items = [x for x in items if x.get("poster_path")]
            if items:
                content_layout.addWidget(
                    MediaRow(
                        title,
                        items,
                        self.thread_pool,
                        self.open_details,
                        state_lookup=self._watch_entry,
                    )
                )

        tmdb_credit = QLabel("This product uses the TMDB API but is not endorsed or certified by TMDB.")
        tmdb_credit.setObjectName("tmdbCredit")
        tmdb_credit.setAlignment(Qt.AlignCenter)
        content_layout.addWidget(tmdb_credit)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        self.home_layout.addWidget(scroll)
        self.stack.setCurrentWidget(self.home_page)

    def _refresh_home_from_state(self) -> None:
        if self.home_payload is None or self.stack.currentWidget() != self.home_page:
            return
        layout = self._home_content_layout
        if layout is None:
            self._render_home(self.home_payload)
            return

        # Rebuild only the small profile-specific row and update badges in
        # place. Reconstructing all five infinite discovery carousels after a
        # single watch-state change briefly doubled hundreds of card widgets.
        old_continue = self._continue_row
        self._continue_row = None
        if old_continue is not None:
            layout.removeWidget(old_continue)
            old_continue.hide()
            old_continue.deleteLater()

        continue_items = self.watch_state.continue_watching(self.active_profile_id)
        if continue_items:
            row = MediaRow(
                "Continue Watching",
                continue_items,
                self.thread_pool,
                self.open_details,
                state_lookup=self._watch_entry,
                show_progress=True,
                infinite=False,
            )
            self._continue_row = row
            layout.insertWidget(self._continue_insert_index, row)

        for card in self.home_page.findChildren(MediaCard):
            if old_continue is not None and self._find_ancestor(card, MediaRow) is old_continue:
                continue
            card.refresh_watch_state()
        self._invalidate_controller_focusables()
        _notify_image_view_changed()

    def _show_home_error(self, error: str) -> None:
        self._home_loading = False
        self._clear_layout(self.home_layout)
        panel = QVBoxLayout()
        title = QLabel("Couldn’t load TMDB")
        title.setObjectName("setupTitle")
        title.setAlignment(Qt.AlignCenter)
        panel.addStretch(1)
        panel.addWidget(title)
        msg = QLabel("Check your internet connection and TMDB Read Access Token.\n\n" + error)
        msg.setObjectName("errorLabel")
        msg.setAlignment(Qt.AlignCenter)
        msg.setWordWrap(True)
        panel.addWidget(msg)
        retry = QPushButton("Retry")
        retry.setObjectName("watchButton")
        retry.setProperty("controllerSelected", False)
        retry.clicked.connect(self._load_home)
        panel.addWidget(retry, 0, Qt.AlignCenter)
        panel.addStretch(1)
        host = QWidget()
        host.setLayout(panel)
        self.home_layout.addWidget(host)

    # ------------------------------------------------------------------------ search
    def _on_search_changed(self, text: str) -> None:
        if not text.strip():
            self.search_timer.stop()
            self._search_generation += 1
            self._search_inflight_query = ""
            if self.active_profile_id:
                self._show_home()
            return
        self.search_timer.start()

    def _perform_search(self) -> None:
        self.search_timer.stop()
        query = self.search_box.text().strip()
        if not query or not self.client:
            return
        if query == self._search_inflight_query:
            return
        self._search_generation += 1
        generation = self._search_generation
        self._search_inflight_query = query
        self._clear_layout(self.search_layout)
        heading = QLabel(f'Searching for “{query}”…')
        heading.setObjectName("searchHeading")
        self.search_layout.addWidget(heading)
        self.search_layout.addStretch(1)
        self.stack.setCurrentWidget(self.search_page)

        def fetch_search() -> dict[str, Any]:
            try:
                return {
                    "generation": generation,
                    "query": query,
                    "items": self.client.search(query),
                    "error": None,
                }
            except Exception as exc:
                return {
                    "generation": generation,
                    "query": query,
                    "items": [],
                    "error": str(exc),
                }

        worker = FunctionWorker(fetch_search)
        # A bound QObject method guarantees that all widget creation happens on
        # the GUI thread. A bare lambda here can run in the worker thread.
        worker.signals.success.connect(self._render_search_payload)
        worker.signals.error.connect(self._render_search_error)
        _start_worker(self.thread_pool, worker)

    def _render_search_payload(self, payload: dict[str, Any]) -> None:
        query = str(payload.get("query", ""))
        if int(payload.get("generation", -1)) != self._search_generation:
            if self._search_inflight_query == query:
                self._search_inflight_query = ""
            return
        if self.search_box.text().strip() != query:
            if self._search_inflight_query == query:
                self._search_inflight_query = ""
            return
        self._search_inflight_query = ""
        error = payload.get("error")
        if error:
            self._render_search_error(str(error))
            return
        self._render_search(query, list(payload.get("items") or []))

    def _render_search(self, query: str, items: list[dict[str, Any]]) -> None:
        if self.search_box.text().strip() != query:
            return
        self._clear_layout(self.search_layout)
        heading = QLabel(f'Results for “{query}”')
        heading.setObjectName("searchHeading")
        self.search_layout.addWidget(heading)

        if not items:
            empty = QLabel("No movies or TV shows found.")
            empty.setObjectName("mutedLabel")
            self.search_layout.addWidget(empty)
            self.search_layout.addStretch(1)
            return

        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("mainScroll")
        grid_host = QWidget()
        grid_host.setObjectName("page")
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 4, 0, 24)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(18)
        columns = max(3, min(7, self.width() // 200))
        for i, media in enumerate(items[:40]):
            card = MediaCard(media, self.thread_pool, self.open_details, width=172, state_lookup=self._watch_entry)
            grid.addWidget(card, i // columns, i % columns, Qt.AlignTop)
        grid.setColumnStretch(columns, 1)
        scroll.setWidget(grid_host)
        self.search_layout.addWidget(scroll, 1)

    def _render_search_error(self, error: str) -> None:
        self._search_inflight_query = ""
        self._clear_layout(self.search_layout)
        label = QLabel(f"Search failed.\n\n{error}")
        label.setObjectName("errorLabel")
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        self.search_layout.addWidget(label, 1)

    def _quick_search_type(self, media_type: str) -> None:
        self.search_timer.stop()
        self._search_generation += 1
        self._search_inflight_query = ""
        self.search_box.blockSignals(True)
        self.search_box.clear()
        self.search_box.blockSignals(False)
        self._clear_layout(self.search_layout)
        heading = QLabel("Movies" if media_type == "movie" else "TV Shows")
        heading.setObjectName("searchHeading")
        self.search_layout.addWidget(heading)

        items: list[dict[str, Any]] = []
        for _title, section_items in self.home_sections:
            items.extend(x for x in section_items if x.get("media_type") == media_type)

        seen = set()
        deduped = []
        for item in items:
            key = (item.get("media_type"), item.get("id"))
            if key in seen or not item.get("poster_path"):
                continue
            seen.add(key)
            deduped.append(item)

        if deduped:
            scroll = SmoothScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            host = QWidget()
            host.setObjectName("page")
            grid = QGridLayout(host)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(18)
            columns = max(3, min(7, self.width() // 200))
            for i, media in enumerate(deduped[:42]):
                grid.addWidget(
                    MediaCard(media, self.thread_pool, self.open_details, width=172, state_lookup=self._watch_entry),
                    i // columns,
                    i % columns,
                    Qt.AlignTop,
                )
            scroll.setWidget(host)
            self.search_layout.addWidget(scroll, 1)
        else:
            label = QLabel("Home is still loading. Try again in a moment.")
            label.setObjectName("mutedLabel")
            self.search_layout.addWidget(label)
            self.search_layout.addStretch(1)
        self.stack.setCurrentWidget(self.search_page)

    # ---------------------------------------------------------------------- details
    def open_details(self, media: dict[str, Any]) -> None:
        if not self.client:
            return
        active_detail = self._active_detail_dialog()
        if active_detail is not None:
            active_detail.raise_()
            active_detail.activateWindow()
            return
        dialog = DetailDialog(
            self.client,
            media,
            self.thread_pool,
            self.watch_state,
            self.active_profile_id,
            self,
        )
        self._detail_dialog = dialog
        self._detail_state_dirty = False
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        dialog.stateChanged.connect(self._detail_state_changed)
        dialog.controlsReady.connect(
            lambda preferred, d=dialog: self._detail_controls_ready(d, preferred)
        )
        dialog.finished.connect(lambda _result, d=dialog: self._detail_finished(d))
        self._clear_controller_selection()
        dialog.open()
        if self.controller.connected:
            QTimer.singleShot(
                0,
                lambda d=dialog: self._focus_detail_control(d, d.close_button),
            )

    def _detail_finished(self, dialog: DetailDialog) -> None:
        self._clear_controller_selection()
        if self._detail_dialog is dialog:
            self._detail_dialog = None
        if self._detail_state_dirty:
            self._detail_state_dirty = False
            self._refresh_home_from_state()

    def _detail_state_changed(self) -> None:
        # The details window is modal, so rebuilding hundreds of hidden home
        # widgets after every click cannot be seen. Coalesce all changes into a
        # single refresh when the dialog closes.
        self._detail_state_dirty = True

    def _detail_controls_ready(self, dialog: DetailDialog, preferred: QWidget) -> None:
        QTimer.singleShot(
            0,
            lambda d=dialog, widget=preferred: self._focus_detail_control(d, widget),
        )

    def _focus_detail_control(self, dialog: DetailDialog, preferred: Optional[QWidget]) -> None:
        if not self.controller.connected:
            return
        if self._active_detail_dialog() is not dialog:
            return
        target = dialog.controller_current_target(preferred)
        if target is None:
            return
        self._focus_controller_widget(target)

    def _show_home(self) -> None:
        if not self.active_profile_id:
            self._show_profile_picker()
            return
        self.header.show()
        self.controller_status.setVisible(self.controller.connected)
        self.stack.setCurrentWidget(self.home_page)

    def _focus_search(self) -> None:
        if not self.active_profile_id:
            return
        self.search_box.setFocus()
        self.search_box.selectAll()

    def _escape_action(self) -> None:
        if self.stack.currentWidget() == self.profile_page and self.active_profile_id:
            self.profile_manage_mode = False
            self.header.show()
            self._show_home()
        elif self.search_box.hasFocus():
            self.search_box.clearFocus()
        elif self.stack.currentWidget() == self.search_page:
            self.search_box.blockSignals(True)
            self.search_box.clear()
            self.search_box.blockSignals(False)
            self._show_home()

    # --------------------------------------------------------------- controller/UI
    @staticmethod
    def _refresh_style(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _set_controller_selected(self, widget: Optional[QWidget]) -> None:
        if widget is self._controller_selected:
            return
        self._clear_controller_selection()
        if widget is None or not self.controller.connected:
            return
        self._controller_selected = widget
        if hasattr(widget, "set_controller_selected"):
            widget.set_controller_selected(True)
        else:
            widget.setProperty("controllerSelected", True)
            self._refresh_style(widget)

    def _clear_controller_selection(self) -> None:
        widget = self._controller_selected
        self._controller_selected = None
        if widget is None:
            return
        try:
            if hasattr(widget, "set_controller_selected"):
                widget.set_controller_selected(False)
            else:
                widget.setProperty("controllerSelected", False)
                self._refresh_style(widget)
        except RuntimeError:
            pass

    def _controller_connection_changed(self, connected: bool, name: str) -> None:
        self.controller_status.setVisible(connected and self.header.isVisible())
        if connected:
            self.controller_status.setToolTip(name or "Controller connected")
            detail = self._active_detail_dialog()
            if detail is not None:
                QTimer.singleShot(
                    0,
                    lambda d=detail: self._focus_detail_control(d, d.controller_current_target()),
                )
        else:
            self._clear_controller_selection()

    def _active_detail_dialog(self) -> Optional[DetailDialog]:
        if self._detail_dialog is not None:
            try:
                return self._detail_dialog
            except RuntimeError:
                self._detail_dialog = None
        modal = QApplication.activeModalWidget()
        if isinstance(modal, DetailDialog):
            return modal
        active = QApplication.activeWindow()
        return active if isinstance(active, DetailDialog) and active.isModal() else None

    def _controller_root(self) -> QWidget:
        detail = self._active_detail_dialog()
        if detail is not None:
            return detail
        modal = QApplication.activeModalWidget()
        if isinstance(modal, QWidget):
            return modal
        active = QApplication.activeWindow()
        if isinstance(active, QWidget):
            return active
        return self

    def _controller_focusables(self, root: QWidget) -> list[QWidget]:
        if isinstance(root, DetailDialog):
            return root.controller_focusables()

        if self._focusable_cache_dirty or self._focusable_cache_root is not root:
            self._focusable_cache_root = root
            self._focusable_cache = [
                widget
                for widget in root.findChildren(QWidget)
                if isinstance(widget, (QAbstractButton, QLineEdit, MediaCard, ProfileCard))
                and not (
                    isinstance(widget, MediaCard)
                    and bool(widget.property("controllerClone"))
                )
                and not (isinstance(widget, QLineEdit) and widget.isReadOnly())
                and widget.focusPolicy() != Qt.NoFocus
            ]
            self._focusable_cache_dirty = False

        widgets: list[QWidget] = []
        stale = False
        for widget in self._focusable_cache:
            try:
                if widget.isVisibleTo(root) and widget.isEnabled():
                    widgets.append(widget)
            except RuntimeError:
                stale = True
                continue
        if stale:
            self._focusable_cache_dirty = True
        return widgets

    @staticmethod
    def _widget_center_global(widget: QWidget):
        return widget.mapToGlobal(widget.rect().center())

    def _select_first_controller_widget(self, root: Optional[QWidget] = None) -> None:
        if not self.controller.connected:
            return
        root = root or self._controller_root()
        candidates = self._controller_focusables(root)
        if candidates:
            candidates[0].setFocus(Qt.OtherFocusReason)
            self._set_controller_selected(candidates[0])
            self._ensure_focus_visible(candidates[0])

    def _controller_navigate(self, direction: str) -> None:
        detail = self._active_detail_dialog()
        if detail is not None:
            current = self._controller_selected
            if current not in detail.controller_focusables():
                focus = QApplication.focusWidget()
                current = focus if focus in detail.controller_focusables() else None
            target = detail.controller_move_target(direction, current)
            if target is not None:
                self._focus_controller_widget(target)
            return

        root = self._controller_root()
        candidates = self._controller_focusables(root)
        if not candidates:
            return
        current = QApplication.focusWidget()
        if current not in candidates:
            self._select_first_controller_widget(root)
            return

        self._set_controller_selected(current)

        # Left/right inside a movie row is row-local and circular. It never
        # falls through to the next/previous row at the edge.
        if isinstance(current, MediaCard) and direction in ("left", "right"):
            media_row = getattr(current, "controller_row", None)
            if isinstance(media_row, MediaRow):
                if media_row.controller_move(current, direction, self._select_media_card_from_row):
                    return

        current_center = self._widget_center_global(current)
        best = None
        best_score = float("inf")
        for candidate in candidates:
            if candidate is current:
                continue
            center = self._widget_center_global(candidate)
            dx = center.x() - current_center.x()
            dy = center.y() - current_center.y()
            if direction == "left" and dx >= -4:
                continue
            if direction == "right" and dx <= 4:
                continue
            if direction == "up" and dy >= -4:
                continue
            if direction == "down" and dy <= 4:
                continue

            if direction in ("left", "right"):
                primary, secondary = abs(dx), abs(dy)
            else:
                primary, secondary = abs(dy), abs(dx)
            score = primary + secondary * 2.75
            if score < best_score:
                best_score = score
                best = candidate

        if best is not None:
            best.setFocus(Qt.OtherFocusReason)
            self._set_controller_selected(best)
            self._ensure_focus_visible(best)

    def _select_media_card_from_row(self, card: MediaCard, ensure_visible: bool = True) -> None:
        if card is None:
            return
        card.setFocus(Qt.OtherFocusReason)
        self._set_controller_selected(card)
        if ensure_visible:
            self._ensure_focus_visible(card)

    def _focus_controller_widget(self, widget: QWidget) -> None:
        if widget is None or not widget.isVisible() or not widget.isEnabled():
            return
        widget.setFocus(Qt.OtherFocusReason)
        self._set_controller_selected(widget)
        self._ensure_focus_visible(widget)

    def _ensure_focus_visible(self, widget: QWidget) -> None:
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, SmoothScrollArea):
                parent.smooth_ensure_widget_visible(widget, 48, 48)
            elif isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(widget, 48, 48)
            parent = parent.parentWidget()

    def _controller_activate(self) -> None:
        detail = self._active_detail_dialog()
        if detail is not None:
            current = self._controller_selected
            if current not in detail.controller_focusables():
                focus = QApplication.focusWidget()
                current = focus if focus in detail.controller_focusables() else None
            target = detail.controller_current_target(current)
            if target is not None:
                self._focus_controller_widget(target)
                target.click()
            return

        root = self._controller_root()
        focus = QApplication.focusWidget()
        if focus is None or not focus.isVisibleTo(root):
            self._select_first_controller_widget(root)
            return

        self._set_controller_selected(focus)
        if focus is self.search_box:
            # Schedule instead of entering a nested event loop inside the pygame
            # poll callback. This is what keeps the controller alive in keyboard.
            QTimer.singleShot(0, self._open_controller_search_keyboard)
            return
        if isinstance(focus, QAbstractButton):
            focus.click()
            return
        if isinstance(focus, (MediaCard, ProfileCard)):
            focus.activate()

    def _controller_back(self) -> None:
        detail = self._active_detail_dialog()
        if detail is not None:
            if detail.controller_back():
                return
            self._clear_controller_selection()
            detail.reject()
            return
        self._clear_controller_selection()
        modal = QApplication.activeModalWidget()
        if isinstance(modal, QDialog):
            modal.reject()
            return
        self._escape_action()

    def _controller_pause(self) -> None:
        detail = self._active_detail_dialog()
        if detail is not None and detail.is_player_fullscreen():
            detail.pause_fullscreen_player()

    def _open_controller_search_keyboard(self) -> None:
        if not self.controller.connected or self._controller_keyboard is not None:
            return

        def accept_text(text: str) -> None:
            self.search_box.setText(text)
            self.search_timer.stop()
            if text:
                self._perform_search()

        self._open_text_keyboard("Search PiStick", self.search_box.text(), "Search", accept_text)

    def _open_text_keyboard(
        self,
        title: str,
        initial: str,
        submit_text: str,
        on_accept: Callable[[str], None],
    ) -> None:
        if self._controller_keyboard is not None:
            return
        keyboard = OnScreenKeyboard(initial, title, submit_text, self)
        self._controller_keyboard = keyboard

        def accepted() -> None:
            text = keyboard.text()
            if text:
                on_accept(text)

        def finished(_result: int) -> None:
            self._controller_keyboard = None
            self._clear_controller_selection()

        keyboard.accepted.connect(accepted)
        keyboard.finished.connect(finished)
        keyboard.open()  # non-blocking: controller timer keeps polling
        QTimer.singleShot(70, lambda: self._select_first_controller_widget(keyboard))

    def _invalidate_controller_focusables(self) -> None:
        self._focusable_cache_dirty = True
        self._focusable_cache_root = None
        self._focusable_cache = []

    def _clear_layout(self, layout) -> None:
        self._invalidate_controller_focusables()
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            child = item.layout()
            if child:
                self._clear_layout(child)


APP_STYLESHEET = r"""
* {
    font-family: "Segoe UI", "Inter", Arial, sans-serif;
    color: #f5f5f5;
}
QMainWindow, QDialog, #appRoot, #page, #profilePage, #transparentWidget {
    background: #0b0b0d;
}
#header {
    background: rgba(12, 12, 14, 248);
    border-bottom: 1px solid #242428;
}
#brand {
    color: #e50914;
    font-size: 26px;
    font-weight: 900;
    letter-spacing: 1.5px;
}
#navButton {
    background: transparent;
    border: 2px solid transparent;
    border-radius: 8px;
    padding: 8px 10px;
    color: #d7d7da;
    font-size: 14px;
    font-weight: 600;
}
#navButton:hover { color: #ffffff; }
#navButton[controllerSelected="true"] {
    color: #ffffff;
    background: #2a2a2f;
    border: 2px solid #ffffff;
}
#searchBox {
    background: #18181c;
    border: 1px solid #3b3b40;
    border-radius: 9px;
    padding: 10px 13px;
    color: #ffffff;
    font-size: 14px;
    selection-background-color: #e50914;
}
#searchBox:focus { border: 1px solid #5a5a61; background: #202024; }
#searchBox[controllerSelected="true"] { border: 2px solid #ffffff; }
#profileButton {
    background: #202024;
    border: 2px solid transparent;
    border-radius: 8px;
    padding: 9px 12px;
    font-size: 13px;
    font-weight: 700;
}
#profileButton:hover { background:#2c2c31; }
#profileButton[controllerSelected="true"] { border:2px solid #ffffff; }
#mainScroll, QScrollArea { background: transparent; border: none; }
#remoteImage8, #remoteImage10, #remoteImage14 {
    background:#242424;
    color:#777777;
    font-size:12px;
}
#remoteImage8 { border-radius:8px; }
#remoteImage10 { border-radius:10px; }
#remoteImage14 { border-radius:14px; }
QScrollBar:vertical { background: #0b0b0d; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #45454c; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar:horizontal { background: transparent; height: 9px; }
QScrollBar::handle:horizontal { background: #3a3a40; border-radius: 4px; min-width: 35px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
#heroBanner { background: #131316; border: 1px solid #29292e; border-radius: 18px; }
#heroBadge { color: #ff5d65; font-size: 12px; font-weight: 800; letter-spacing: 2px; }
#heroTitle { color: white; font-size: 38px; font-weight: 850; margin-top: 6px; }
#heroOverview { color: #c9c9cc; font-size: 15px; line-height: 1.4; }
#heroMeta, #detailMeta { color: #b9b9be; font-size: 13px; }
#heroButton, #watchButton {
    background: #f4f4f4;
    color: #0a0a0b;
    border: 3px solid transparent;
    border-radius: 8px;
    padding: 9px 16px;
    font-size: 14px;
    font-weight: 800;
}
#heroButton:hover, #watchButton:hover { background: white; }
#heroButton[controllerSelected="true"], #watchButton[controllerSelected="true"],
#secondaryButton[controllerSelected="true"], #iconButton[controllerSelected="true"],
#dangerButton[controllerSelected="true"], #manageProfilesButton[controllerSelected="true"],
#watchButton:focus, #secondaryButton:focus, #iconButton:focus, #dangerButton:focus {
    border: 3px solid #ffffff;
}
#rowHeading, #searchHeading { font-size: 22px; font-weight: 800; color: #ffffff; }
#searchHeading { font-size: 27px; }
#mediaCard {
    background: transparent;
    border: 3px solid transparent;
    border-radius: 11px;
}
#mediaCard:hover { background: #17171a; }
#mediaCard[controllerSelected="true"], #mediaCard:focus {
    background: #17171a;
    border: 3px solid #ffffff;
}
#cardTitle { font-size: 14px; font-weight: 700; color: #f2f2f3; }
#cardMeta { font-size: 11px; color: #929298; }
#watchedBadge {
    background: rgba(0, 0, 0, 205);
    color: #ffffff;
    border-radius: 5px;
    padding: 5px 7px;
    font-size: 10px;
    font-weight: 900;
}
#watchProgress { background:#35353a; border:none; border-radius:2px; }
#watchProgress::chunk { background:#e50914; border-radius:2px; }
#iconButton {
    border: 3px solid transparent;
    border-radius: 21px;
    background: #27272c;
    color: white;
    font-size: 18px;
    font-weight: 700;
}
#iconButton:hover { background: #3a3a40; }
#detailTitle { font-size: 35px; font-weight: 900; }
#episodePicker {
    background:#17171b;
    border:1px solid #3b3b42;
    border-radius:16px;
}
#episodePickerTitle {
    color:#ffffff;
    font-size:27px;
    font-weight:900;
}
#episodePickerHint {
    color:#9d9da4;
    font-size:12px;
    font-weight:700;
    letter-spacing:1px;
}
#episodePickerClose {
    background:#2b2b31;
    color:#ffffff;
    border:3px solid transparent;
    border-radius:19px;
    font-size:16px;
    font-weight:800;
}
#episodePickerClose:hover { background:#3b3b42; }
#seasonButton {
    background:#29292e;
    color:#f3f3f4;
    border:3px solid transparent;
    border-radius:8px;
    padding:8px 15px;
    font-size:14px;
    font-weight:800;
}
#seasonButton:hover { background:#38383e; }
#seasonButton[activeSeason="true"] {
    background:#f3f3f3;
    color:#101012;
}
#episodeButton {
    background:#222227;
    color:#f4f4f5;
    border:3px solid transparent;
    border-radius:10px;
    padding:11px 14px;
    text-align:left;
    font-size:13px;
    font-weight:650;
}
#episodeButton:hover { background:#303036; }
#episodeButton[episodeStatus="finished"] { color:#b8b8bd; }
#episodeButton[resumeTarget="true"] {
    background:#2a2023;
    border:3px solid #e50914;
}
#seasonButton[controllerSelected="true"],
#episodeButton[controllerSelected="true"],
#episodePickerClose[controllerSelected="true"],
#seasonButton:focus, #episodeButton:focus, #episodePickerClose:focus {
    border:3px solid #ffffff;
}
#detailWatchedBadge {
    background:#1e7e47;
    color:#ffffff;
    border-radius:7px;
    padding:6px 10px;
    font-size:12px;
    font-weight:900;
}
#continueLabel { color:#f0f0f0; font-size:13px; font-weight:700; }
#genreLabel { color: #efeff1; font-size: 13px; font-weight: 700; }
#overviewLabel { color: #d0d0d4; font-size: 15px; }
#castLabel { color: #9f9fa5; font-size: 13px; }
#secondaryButton {
    background: #2c2c31;
    color: #ffffff;
    border: 3px solid transparent;
    border-radius: 8px;
    padding: 9px 16px;
    font-size: 14px;
    font-weight: 700;
}
#secondaryButton:hover { background: #3a3a40; }
#dangerButton {
    background:#40181c;
    color:#ffb4b8;
    border:3px solid transparent;
    border-radius:8px;
    padding:9px 16px;
    font-size:14px;
    font-weight:800;
}
#dangerButton:hover { background:#572027; }
#loadingLabel, #mutedLabel { color: #9b9ba1; font-size: 16px; }
#errorLabel { color: #ff8d93; font-size: 15px; }
#tmdbCredit { color: #66666d; font-size: 11px; padding: 20px 0 4px 0; }
#setupTitle { color: #ffffff; font-size: 38px; font-weight: 900; }
#setupBody { color: #bdbdc2; font-size: 16px; line-height: 1.5; }
#controllerStatus {
    color: #d7d7da;
    background: #1d1d22;
    border: 1px solid #3c3c43;
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 12px;
    font-weight: 700;
}
#keyboardDialog { background: #0b0b0d; }
#keyboardTitle { font-size: 30px; font-weight: 900; color: #ffffff; }
#keyboardText {
    background: #18181c;
    border: 2px solid #55555d;
    border-radius: 12px;
    padding: 10px 16px;
    color: #ffffff;
    font-size: 22px;
}
#keyboardKey, #keyboardAction {
    background: #25252a;
    border: 3px solid transparent;
    border-radius: 10px;
    color: #ffffff;
    font-size: 18px;
    font-weight: 800;
    padding: 8px 12px;
}
#keyboardKey:hover, #keyboardAction:hover { background: #34343a; }
#keyboardKey[controllerSelected="true"], #keyboardAction[controllerSelected="true"] {
    background: #ffffff;
    color: #0b0b0d;
    border: 3px solid #e50914;
}
#controllerHint { color: #8f8f96; font-size: 12px; }

/* Netflix-style profile chooser */
#profilePage { background:#141414; }
#profilesTitle {
    color:#ffffff;
    font-size:48px;
    font-weight:500;
    padding-bottom:8px;
}
#profileCard { background:transparent; border:0; }
#profileAvatar {
    border:3px solid transparent;
    border-radius:6px;
    color:#ffffff;
    font-size:46px;
    font-weight:800;
}
#profileAvatar[avatarColor="red"] { background:#c9192e; }
#profileAvatar[avatarColor="blue"] { background:#1672d4; }
#profileAvatar[avatarColor="green"] { background:#15995c; }
#profileAvatar[avatarColor="purple"] { background:#7b45b6; }
#profileAvatar[avatarColor="orange"] { background:#d46b21; }
#profileAvatar[avatarColor="teal"] { background:#148e99; }
#profileAvatar[avatarColor="add"] { background:#222225; }
#profileAvatar[addProfile="true"] { color:#9b9b9b; font-size:72px; font-weight:300; }
#profileAvatar[mouseHover="true"], #profileAvatar[controllerSelected="true"] { border:3px solid #ffffff; }
#profileName { color:#8c8c8c; font-size:17px; font-weight:500; }
#profileCard:hover #profileName { color:#ffffff; }
#profileEditOverlay {
    background:rgba(0,0,0,145);
    color:#ffffff;
    font-size:46px;
    font-weight:700;
}
#manageProfilesButton {
    background:transparent;
    color:#808080;
    border:1px solid #808080;
    border-radius:0;
    padding:9px 24px;
    font-size:16px;
    letter-spacing:2px;
}
#manageProfilesButton:hover { color:#ffffff; border-color:#ffffff; }
#profileEditDialog { background:#141414; }
#profileEditTitle { font-size:28px; font-weight:800; }
QToolTip { background: #242429; color: white; border: 1px solid #4c4c52; }

#textInputDialog {
    background:#141414;
}
#textInputTitle {
    color:#ffffff;
    font-size:24px;
    font-weight:800;
}
#textInputLabel {
    color:#e8e8e8;
    font-size:14px;
    font-weight:600;
}
#textInputField {
    background:#232327;
    color:#ffffff;
    selection-background-color:#e50914;
    selection-color:#ffffff;
    border:2px solid #66666e;
    border-radius:7px;
    padding:8px 10px;
    font-size:16px;
}
#textInputField:focus {
    border:2px solid #ffffff;
}
#textDialogAccept, #textDialogCancel {
    min-width:100px;
    min-height:38px;
    border-radius:6px;
    font-size:14px;
    font-weight:800;
    padding:6px 14px;
}
#textDialogAccept {
    background:#ffffff;
    color:#111111;
    border:2px solid #ffffff;
}
#textDialogCancel {
    background:#2b2b30;
    color:#ffffff;
    border:2px solid #55555d;
}
QMessageBox {
    background:#141414;
}
QMessageBox QLabel {
    color:#ffffff;
    background:transparent;
}
QMessageBox QPushButton {
    background:#2b2b30;
    color:#ffffff;
    border:1px solid #66666e;
    border-radius:5px;
    min-width:90px;
    min-height:32px;
    padding:4px 10px;
}
"""


def _ensure_app_stylesheet() -> None:
    """Parse the large application stylesheet once, not once per dialog."""
    app = QApplication.instance()
    if app is None or bool(app.property("pistickStylesheetApplied")):
        return
    app.setStyleSheet(APP_STYLESHEET)
    app.setProperty("pistickStylesheetApplied", True)


def main() -> int:
    print(f"Starting {APP_NAME} {APP_VERSION}")
    # Qt WebView must select its native Windows backend before QApplication
    # creates a platform graphics context.
    _initialize_windows_playback_webview()
    application_attributes = getattr(Qt, "ApplicationAttribute", Qt)
    share_contexts = getattr(application_attributes, "AA_ShareOpenGLContexts", None)
    if share_contexts is not None:
        QApplication.setAttribute(share_contexts, True)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    _ensure_app_stylesheet()
    window = MainWindow(AppConfig.load())
    window.show()
    run_event_loop = getattr(app, "exec", None) or getattr(app, "exec_")
    return run_event_loop()


if __name__ == "__main__":
    raise SystemExit(main())
