import json
import os
import sys
import time
import uuid
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import requests
from PySide6.QtCore import (
    QEvent,
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
    QUrl,
)
from PySide6.QtGui import QFont, QImage, QKeySequence, QPixmap, QShortcut
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
    QInputDialog,
)

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
except Exception:
    QWebEngineView = None

try:
    import pygame
except Exception:
    pygame = None


APP_NAME = "PiStick"
APP_VERSION = "2.2-profiles"
TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"
CONFIG_PATH = Path(__file__).with_name("config.json")
STATE_PATH = Path(__file__).with_name("pistick_state.json")


# Keep background QRunnables alive until their run() methods finish.
# Without this, PySide can garbage-collect the Python signal owner while a
# download is still running, causing "Signal source has been deleted".
_ACTIVE_WORKERS: set[QRunnable] = set()


def _start_worker(pool: QThreadPool, worker: QRunnable) -> None:
    _ACTIVE_WORKERS.add(worker)
    pool.start(worker)


def _release_worker(worker: QRunnable) -> None:
    _ACTIVE_WORKERS.discard(worker)


# -----------------------------------------------------------------------------
# JELLYFIN HOOK
# -----------------------------------------------------------------------------
def watch_title(media: dict[str, Any]) -> None:
    """
    Replace this function with your Jellyfin API / playback code later.

    PiStick records that the current profile started this title before calling
    this function. When you wire in Jellyfin, you can also update real playback
    progress through WatchStateStore.set_progress().
    """
    pass


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
    """Local per-profile watch history used until Jellyfin provides real progress."""

    DEFAULT_AVATARS = ["red", "blue", "green", "purple", "orange", "teal"]

    def __init__(self, path: Path = STATE_PATH):
        self.path = path
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
                    return raw
            except Exception:
                pass
        data = self._default_data()
        self._save_data(data)
        return data

    def _save_data(self, data: dict[str, Any]) -> None:
        try:
            self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
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
        )
        return {key: media.get(key) for key in keys if media.get(key) is not None}

    def _profile_history(self, profile_id: Optional[str]) -> dict[str, Any]:
        if not profile_id:
            return {}
        return self.data.setdefault("watch_state", {}).setdefault(profile_id, {})

    def entry(self, profile_id: Optional[str], media: dict[str, Any]) -> Optional[dict[str, Any]]:
        return self._profile_history(profile_id).get(self.media_key(media))

    def mark_started(self, profile_id: Optional[str], media: dict[str, Any]) -> None:
        if not profile_id:
            return
        history = self._profile_history(profile_id)
        key = self.media_key(media)
        previous = history.get(key, {})
        progress = float(previous.get("progress", 0.0) or 0.0)
        # Without Jellyfin progress yet, 3% simply means "started".
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
        """Call this later from Jellyfin with a 0.0-1.0 playback fraction."""
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

    def continue_watching(self, profile_id: Optional[str]) -> list[dict[str, Any]]:
        entries = [
            entry
            for entry in self._profile_history(profile_id).values()
            if entry.get("status") == "in_progress" and entry.get("media")
        ]
        entries.sort(key=lambda x: float(x.get("updated_at", 0.0)), reverse=True)
        return [dict(entry.get("media", {})) for entry in entries]


class TMDBClient:
    def __init__(self, read_token: str):
        self.read_token = read_token
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {read_token}",
                "Accept": "application/json",
                "User-Agent": f"{APP_NAME}/2.0",
            }
        )

    def get(self, endpoint: str, **params: Any) -> dict[str, Any]:
        params.setdefault("language", "en-US")
        response = self.session.get(f"{TMDB_API_BASE}{endpoint}", params=params, timeout=15)
        response.raise_for_status()
        return response.json()

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
        data = self.get("/trending/all/week")
        return [
            self.normalize(x)
            for x in data.get("results", [])
            if x.get("media_type") in {"movie", "tv"} and x.get("poster_path")
        ]

    def popular_movies(self) -> list[dict[str, Any]]:
        return [self.normalize(x, "movie") for x in self.get("/movie/popular").get("results", [])]

    def popular_tv(self) -> list[dict[str, Any]]:
        return [self.normalize(x, "tv") for x in self.get("/tv/popular").get("results", [])]

    def top_rated_movies(self) -> list[dict[str, Any]]:
        return [self.normalize(x, "movie") for x in self.get("/movie/top_rated").get("results", [])]

    def upcoming_movies(self) -> list[dict[str, Any]]:
        return [self.normalize(x, "movie") for x in self.get("/movie/upcoming").get("results", [])]

    def search(self, query: str) -> list[dict[str, Any]]:
        data = self.get("/search/multi", query=query, include_adult="false")
        items = []
        for item in data.get("results", []):
            if item.get("media_type") not in {"movie", "tv"} or not item.get("poster_path"):
                continue
            items.append(self.normalize(item))
        return items

    def details(self, media_type: str, media_id: int) -> dict[str, Any]:
        data = self.get(f"/{media_type}/{media_id}", append_to_response="videos,credits")
        return self.normalize(data, media_type)


class WorkerSignals(QObject):
    success = Signal(object)
    error = Signal(str)


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
            _release_worker(self)


class ImageSignals(QObject):
    loaded = Signal(bytes)
    failed = Signal()


class ImageWorker(QRunnable):
    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self.signals = ImageSignals()

    def run(self) -> None:
        try:
            response = requests.get(self.url, timeout=15)
            response.raise_for_status()
            try:
                self.signals.loaded.emit(response.content)
            except RuntimeError:
                pass
        except Exception:
            try:
                self.signals.failed.emit()
            except RuntimeError:
                pass
        finally:
            _release_worker(self)


class RemoteImage(QLabel):
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
        self.setFixedSize(width, height)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            f"background:#242424; border-radius:{radius}px; color:#777; font-size:12px;"
        )
        self.setText("Loading…")

    def load(self, url: str) -> None:
        if not url:
            self.setText("No image")
            return
        worker = ImageWorker(url)
        worker.signals.loaded.connect(self._set_image)
        worker.signals.failed.connect(lambda: self.setText("No image"))
        _start_worker(self.thread_pool, worker)

    def _set_image(self, data: bytes) -> None:
        image = QImage.fromData(data)
        if image.isNull():
            self.setText("No image")
            return
        mode = Qt.KeepAspectRatioByExpanding if self.crop else Qt.KeepAspectRatio
        pixmap = QPixmap.fromImage(image).scaled(self.target_size, mode, Qt.SmoothTransformation)
        if self.crop:
            x = max(0, (pixmap.width() - self.target_size.width()) // 2)
            y = max(0, (pixmap.height() - self.target_size.height()) // 2)
            pixmap = pixmap.copy(x, y, self.target_size.width(), self.target_size.height())
        self.setPixmap(pixmap)
        self.setText("")


class SmoothScrollArea(QScrollArea):
    """QScrollArea with animated wheel and controller scrolling."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._v_animation = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._h_animation = QPropertyAnimation(self.horizontalScrollBar(), b"value", self)
        for animation in (self._v_animation, self._h_animation):
            animation.setDuration(230)
            animation.setEasingCurve(QEasingCurve.OutCubic)

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
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.media = media
        self.state_lookup = state_lookup
        self.setObjectName("mediaCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setProperty("controllerSelected", False)

        # Give the controller outline real breathing room around the poster/text.
        outline_space = 6
        self.setFixedWidth(width + outline_space * 2)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(outline_space, outline_space, outline_space, outline_space)
        layout.setSpacing(7)

        poster_height = int(width * 1.5)
        poster_holder = QFrame()
        poster_holder.setObjectName("posterHolder")
        poster_holder.setFixedSize(width, poster_height)
        self.poster = RemoteImage(thread_pool, width, poster_height, radius=8, parent=poster_holder)
        self.poster.move(0, 0)
        poster_path = media.get("poster_path")
        if poster_path:
            self.poster.load(f"{TMDB_IMAGE_BASE}/w342{poster_path}")

        entry = state_lookup(media) if state_lookup else None
        if entry and entry.get("status") == "finished":
            watched = QLabel("✓  WATCHED", poster_holder)
            watched.setObjectName("watchedBadge")
            watched.adjustSize()
            watched.move(8, 8)
            watched.raise_()

        layout.addWidget(poster_holder)

        if show_progress and entry and entry.get("status") == "in_progress":
            progress = QProgressBar()
            progress.setObjectName("watchProgress")
            progress.setRange(0, 1000)
            progress.setValue(int(float(entry.get("progress", 0.0)) * 1000))
            progress.setTextVisible(False)
            progress.setFixedHeight(5)
            layout.addWidget(progress)

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

    def set_controller_selected(self, selected: bool) -> None:
        self.setProperty("controllerSelected", bool(selected))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def activate(self) -> None:
        self.clicked.emit()

    def keyPressEvent(self, event):
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
    def __init__(
        self,
        title: str,
        items: list[dict[str, Any]],
        thread_pool: QThreadPool,
        open_details: Callable[[dict[str, Any]], None],
        state_lookup: Optional[Callable[[dict[str, Any]], Optional[dict[str, Any]]]] = None,
        show_progress: bool = False,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        heading = QLabel(title)
        heading.setObjectName("rowHeading")
        outer.addWidget(heading)

        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        # The old 310px height clipped the card's bottom/controller outline.
        scroll.setFixedHeight(355 if show_progress else 346)
        scroll.viewport().setStyleSheet("background: transparent;")

        content = QWidget()
        content.setObjectName("transparentWidget")
        row = QHBoxLayout(content)
        row.setContentsMargins(0, 0, 18, 8)
        row.setSpacing(12)
        for item in items[:20]:
            row.addWidget(
                MediaCard(
                    item,
                    thread_pool,
                    open_details,
                    state_lookup=state_lookup,
                    show_progress=show_progress,
                )
            )
        row.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)


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

        if pygame is not None:
            try:
                pygame.init()
                pygame.joystick.init()
            except Exception:
                pass

        self.timer = QTimer(self)
        self.timer.setInterval(35)
        self.timer.timeout.connect(self._poll)
        self.timer.start()

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
                self.connectionChanged.emit(True, joystick.get_name() or "Controller")
            elif count == 0 and self.connected:
                self.joystick = None
                self.connected = False
                self.last_button_state.clear()
                self.connectionChanged.emit(False, "")
        except Exception:
            if self.connected:
                self.joystick = None
                self.connected = False
                self.last_button_state.clear()
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
        if pygame is None:
            return
        now = time.monotonic()
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
        self.setStyleSheet(APP_STYLESHEET)
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


class DetailDialog(QDialog):
    stateChanged = Signal()

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
        self.setWindowTitle(initial_media.get("title", APP_NAME))
        self.resize(1080, 760)
        self.setMinimumSize(860, 620)
        self.setModal(True)
        self.setStyleSheet(APP_STYLESHEET)

        self.main = QVBoxLayout(self)
        self.main.setContentsMargins(0, 0, 0, 0)
        self.main.setSpacing(0)

        topbar = QHBoxLayout()
        topbar.setContentsMargins(24, 18, 24, 12)
        topbar.addStretch(1)
        close_btn = QPushButton("✕")
        close_btn.setObjectName("iconButton")
        close_btn.setProperty("controllerSelected", False)
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

        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.reject)
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

    def _render(self, media: dict[str, Any]) -> None:
        self.media = media
        self._clear_body()

        hero = QFrame()
        hero.setObjectName("detailHero")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(0, 0, 0, 0)
        hero_layout.setSpacing(24)

        poster = RemoteImage(self.thread_pool, 230, 345, radius=10)
        if media.get("poster_path"):
            poster.load(f"{TMDB_IMAGE_BASE}/w500{media['poster_path']}")
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
            progress_pct = max(1, int(float(entry.get("progress", 0.0)) * 100))
            progress_label = QLabel(f"Continue watching  •  {progress_pct}%")
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
        watch_button.clicked.connect(lambda: self._watch_clicked(media))
        buttons.addWidget(watch_button)

        entry = self.watch_state.entry(self.profile_id, media)
        if entry and entry.get("status") == "in_progress":
            finished = QPushButton("✓  Mark as Finished")
            finished.setObjectName("secondaryButton")
            finished.setProperty("controllerSelected", False)
            finished.clicked.connect(lambda: self._mark_finished(media))
            buttons.addWidget(finished)
        elif entry and entry.get("status") == "finished":
            unwatched = QPushButton("Mark as Unwatched")
            unwatched.setObjectName("secondaryButton")
            unwatched.setProperty("controllerSelected", False)
            unwatched.clicked.connect(lambda: self._mark_unwatched(media))
            buttons.addWidget(unwatched)

        trailer = self._pick_trailer(media)
        if trailer:
            external_button = QPushButton("↗  Open trailer")
            external_button.setObjectName("secondaryButton")
            external_button.setProperty("controllerSelected", False)
            external_button.clicked.connect(lambda: webbrowser.open(f"https://www.youtube.com/watch?v={trailer['key']}"))
            buttons.addWidget(external_button)
        buttons.addStretch(1)
        info.addLayout(buttons)
        info.addStretch(1)
        hero_layout.addLayout(info, 1)
        self.body.addWidget(hero)

        trailer_heading = QLabel("Trailer")
        trailer_heading.setObjectName("rowHeading")
        self.body.addWidget(trailer_heading)

        if trailer and QWebEngineView is not None:
            web = QWebEngineView()
            web.setMinimumHeight(330)
            web.setHtml(
                f"""
                <!doctype html><html><body style='margin:0;background:#000;overflow:hidden;'>
                <iframe width='100%' height='100%' style='position:absolute;inset:0;border:0;'
                    src='https://www.youtube.com/embed/{trailer['key']}?rel=0&modestbranding=1'
                    title='Trailer' allow='accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share'
                    allowfullscreen></iframe></body></html>
                """,
                QUrl("https://www.youtube.com/"),
            )
            self.body.addWidget(web, 1)
        elif trailer:
            note = QLabel("Embedded trailer playback is unavailable. Use “Open trailer” above.")
            note.setObjectName("mutedLabel")
            note.setAlignment(Qt.AlignCenter)
            self.body.addWidget(note, 1)
        else:
            note = QLabel("No trailer was returned by TMDB for this title.")
            note.setObjectName("mutedLabel")
            note.setAlignment(Qt.AlignCenter)
            self.body.addWidget(note, 1)

    @staticmethod
    def _pick_trailer(media: dict[str, Any]) -> Optional[dict[str, Any]]:
        videos = media.get("videos", {}).get("results", [])
        youtube = [v for v in videos if v.get("site") == "YouTube" and v.get("key")]
        trailers = [v for v in youtube if v.get("type") == "Trailer"]
        official = [v for v in trailers if v.get("official")]
        return (official or trailers or youtube)[0] if youtube else None

    def _watch_clicked(self, media: dict[str, Any]) -> None:
        self.watch_state.mark_started(self.profile_id, media)
        self.stateChanged.emit()
        # Intentionally blank until you add Jellyfin logic to watch_title().
        watch_title(media)
        self._render(media)

    def _mark_finished(self, media: dict[str, Any]) -> None:
        self.watch_state.mark_finished(self.profile_id, media)
        self.stateChanged.emit()
        self._render(media)

    def _mark_unwatched(self, media: dict[str, Any]) -> None:
        self.watch_state.mark_unwatched(self.profile_id, media)
        self.stateChanged.emit()
        self._render(media)

    def _show_error(self, error: str) -> None:
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
        self.thread_pool = QThreadPool.globalInstance()
        self.thread_pool.setMaxThreadCount(12)
        self.watch_state = WatchStateStore()
        self.active_profile_id: Optional[str] = None
        self.home_sections: list[tuple[str, list[dict[str, Any]]]] = []
        self.home_payload: Optional[dict[str, Any]] = None
        self.profile_manage_mode = False
        self._controller_selected: Optional[QWidget] = None
        self._controller_keyboard: Optional[OnScreenKeyboard] = None
        self._profile_edit_dialog: Optional[QDialog] = None

        self.setWindowTitle(APP_NAME)
        self.resize(1440, 900)
        self.setMinimumSize(980, 650)
        self.setStyleSheet(APP_STYLESHEET)

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
        if event.type() in {
            QEvent.MouseButtonPress,
            QEvent.MouseMove,
            QEvent.Wheel,
            QEvent.TouchBegin,
        }:
            self._clear_controller_selection()
        elif event.type() == QEvent.KeyPress:
            self._clear_controller_selection()
        return super().eventFilter(watched, event)

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
        name, ok = QInputDialog.getText(self, "Add Profile", "Profile name:")
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
        dialog.setStyleSheet(APP_STYLESHEET)

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
        name, ok = QInputDialog.getText(self, "Rename Profile", "Profile name:", text=profile.get("name", ""))
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
            "3. Copy config.example.json to config.json and paste your token.\n"
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
        if not self.client:
            return
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
        self.home_payload = payload
        self._clear_layout(self.home_layout)
        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("mainScroll")

        content = QWidget()
        content.setObjectName("page")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 14, 24, 36)
        content_layout.setSpacing(28)

        # Profile-specific row only appears when something was started but not finished.
        continue_items = self.watch_state.continue_watching(self.active_profile_id)
        if continue_items:
            content_layout.addWidget(
                MediaRow(
                    "Continue Watching",
                    continue_items,
                    self.thread_pool,
                    self.open_details,
                    state_lookup=self._watch_entry,
                    show_progress=True,
                )
            )

        trending = payload.get("trending") or []
        if trending:
            hero_candidates = [x for x in trending if x.get("backdrop_path")]
            content_layout.addWidget(HeroBanner((hero_candidates or trending)[0], self.thread_pool, self.open_details))

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
        if self.home_payload is not None and self.stack.currentWidget() == self.home_page:
            self._render_home(self.home_payload)

    def _show_home_error(self, error: str) -> None:
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
            if self.active_profile_id:
                self._show_home()
            return
        self.search_timer.start()

    def _perform_search(self) -> None:
        query = self.search_box.text().strip()
        if not query or not self.client:
            return
        self._clear_layout(self.search_layout)
        heading = QLabel(f'Searching for “{query}”…')
        heading.setObjectName("searchHeading")
        self.search_layout.addWidget(heading)
        self.search_layout.addStretch(1)
        self.stack.setCurrentWidget(self.search_page)

        worker = FunctionWorker(lambda: self.client.search(query))
        worker.signals.success.connect(lambda items, q=query: self._render_search(q, items))
        worker.signals.error.connect(self._render_search_error)
        _start_worker(self.thread_pool, worker)

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
        self._clear_layout(self.search_layout)
        label = QLabel(f"Search failed.\n\n{error}")
        label.setObjectName("errorLabel")
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        self.search_layout.addWidget(label, 1)

    def _quick_search_type(self, media_type: str) -> None:
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
        dialog = DetailDialog(
            self.client,
            media,
            self.thread_pool,
            self.watch_state,
            self.active_profile_id,
            self,
        )
        dialog.stateChanged.connect(self._refresh_home_from_state)
        dialog.exec()
        self._refresh_home_from_state()

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
        else:
            self._clear_controller_selection()

    def _controller_root(self) -> QWidget:
        modal = QApplication.activeModalWidget()
        if isinstance(modal, QWidget):
            return modal
        active = QApplication.activeWindow()
        if isinstance(active, QWidget):
            return active
        return self

    @staticmethod
    def _controller_focusables(root: QWidget) -> list[QWidget]:
        widgets: list[QWidget] = []
        for widget in root.findChildren(QWidget):
            if not widget.isVisibleTo(root) or not widget.isEnabled():
                continue
            if isinstance(widget, (QAbstractButton, QLineEdit, MediaCard, ProfileCard)):
                if isinstance(widget, QLineEdit) and widget.isReadOnly():
                    continue
                if widget.focusPolicy() != Qt.NoFocus:
                    widgets.append(widget)
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
        root = self._controller_root()
        candidates = self._controller_focusables(root)
        if not candidates:
            return
        current = QApplication.focusWidget()
        if current not in candidates:
            self._select_first_controller_widget(root)
            return

        self._set_controller_selected(current)
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

    def _ensure_focus_visible(self, widget: QWidget) -> None:
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, SmoothScrollArea):
                parent.smooth_ensure_widget_visible(widget, 48, 48)
            elif isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(widget, 48, 48)
            parent = parent.parentWidget()

    def _controller_activate(self) -> None:
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
        self._clear_controller_selection()
        modal = QApplication.activeModalWidget()
        if isinstance(modal, QDialog):
            modal.reject()
            return
        self._escape_action()

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

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            child = item.layout()
            if child:
                MainWindow._clear_layout(child)


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
#dangerButton[controllerSelected="true"], #manageProfilesButton[controllerSelected="true"] {
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
#mediaCard[controllerSelected="true"] {
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
"""


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow(AppConfig.load())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
