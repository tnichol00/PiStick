"""Thread-safe, server-side profile and watch-state storage.

The web client never owns the durable PiStick state.  It sends changes to the
same-origin API and this module writes them atomically to the server data folder.
The JSON schema intentionally remains compatible with the desktop PiStick
``pistick_state.json`` file so an existing Windows install can be migrated by
copying that file into the server edition's data directory.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import threading
import time
import uuid
from typing import Any, Optional


class StateError(ValueError):
    """Raised when a requested profile or media mutation is invalid."""


class WatchStateStore:
    """Persist PiStick profiles and playback progress on the server."""

    DEFAULT_AVATARS = ("red", "blue", "green", "purple", "orange", "teal")
    MAX_PROFILES = 8

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.data = self._load()

    @staticmethod
    def _default_data() -> dict[str, Any]:
        profile_id = "profile-1"
        return {
            "active_profile": None,
            "profiles": [
                {"id": profile_id, "name": "Profile 1", "avatar": "red"},
            ],
            "watch_state": {profile_id: {}},
        }

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raw = self._default_data()

        if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), list):
            raw = self._default_data()

        profiles: list[dict[str, str]] = []
        seen: set[str] = set()
        for index, candidate in enumerate(raw.get("profiles", [])):
            if not isinstance(candidate, dict):
                continue
            profile_id = str(candidate.get("id", "")).strip()
            if not profile_id or profile_id in seen:
                profile_id = f"profile-{uuid.uuid4().hex[:10]}"
            seen.add(profile_id)
            name = str(candidate.get("name", "")).strip()[:40]
            avatar = str(candidate.get("avatar", "")).strip().lower()
            if avatar not in self.DEFAULT_AVATARS:
                avatar = self.DEFAULT_AVATARS[index % len(self.DEFAULT_AVATARS)]
            profiles.append(
                {
                    "id": profile_id,
                    "name": name or f"Profile {index + 1}",
                    "avatar": avatar,
                }
            )
            if len(profiles) >= self.MAX_PROFILES:
                break

        if not profiles:
            profiles = self._default_data()["profiles"]

        watch_state = raw.get("watch_state")
        if not isinstance(watch_state, dict):
            watch_state = {}
        normalized_watch = {
            profile["id"]: (
                watch_state.get(profile["id"], {})
                if isinstance(watch_state.get(profile["id"]), dict)
                else {}
            )
            for profile in profiles
        }

        active = str(raw.get("active_profile") or "")
        if active not in normalized_watch:
            active = None

        normalized = {
            "active_profile": active,
            "profiles": profiles,
            "watch_state": normalized_watch,
        }
        self._write(normalized)
        return normalized

    @staticmethod
    def _payload(data: dict[str, Any]) -> str:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(self._payload(data), encoding="utf-8")
        os.replace(temporary, self.path)

    def save(self) -> None:
        with self._lock:
            self._write(self.data)

    def profiles_payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                "profiles": deepcopy(self.data["profiles"]),
                "active_profile": self.data.get("active_profile"),
                "max_profiles": self.MAX_PROFILES,
            }

    def profiles(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self.data["profiles"])

    def _profile_unlocked(self, profile_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not profile_id:
            return None
        return next(
            (profile for profile in self.data["profiles"] if profile["id"] == profile_id),
            None,
        )

    def profile(self, profile_id: Optional[str]) -> Optional[dict[str, Any]]:
        with self._lock:
            profile = self._profile_unlocked(profile_id)
            return deepcopy(profile) if profile else None

    def _require_profile_unlocked(self, profile_id: Optional[str]) -> dict[str, Any]:
        profile = self._profile_unlocked(profile_id)
        if profile is None:
            raise StateError("Choose a valid profile first.")
        return profile

    def activate_profile(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            profile = self._require_profile_unlocked(profile_id)
            self.data["active_profile"] = profile_id
            self.data["watch_state"].setdefault(profile_id, {})
            self._write(self.data)
            return deepcopy(profile)

    def add_profile(self, name: str) -> dict[str, Any]:
        with self._lock:
            profiles = self.data["profiles"]
            if len(profiles) >= self.MAX_PROFILES:
                raise StateError(f"PiStick supports up to {self.MAX_PROFILES} profiles.")
            cleaned = str(name or "").strip()[:40]
            if not cleaned:
                cleaned = f"Profile {len(profiles) + 1}"
            profile = {
                "id": f"profile-{uuid.uuid4().hex[:10]}",
                "name": cleaned,
                "avatar": self.DEFAULT_AVATARS[len(profiles) % len(self.DEFAULT_AVATARS)],
            }
            profiles.append(profile)
            self.data["watch_state"][profile["id"]] = {}
            self._write(self.data)
            return deepcopy(profile)

    def rename_profile(self, profile_id: str, name: str) -> dict[str, Any]:
        with self._lock:
            profile = self._require_profile_unlocked(profile_id)
            cleaned = str(name or "").strip()[:40]
            if not cleaned:
                raise StateError("Profile names cannot be blank.")
            profile["name"] = cleaned
            self._write(self.data)
            return deepcopy(profile)

    def delete_profile(self, profile_id: str) -> None:
        with self._lock:
            self._require_profile_unlocked(profile_id)
            if len(self.data["profiles"]) <= 1:
                raise StateError("PiStick must keep at least one profile.")
            self.data["profiles"] = [
                profile
                for profile in self.data["profiles"]
                if profile["id"] != profile_id
            ]
            self.data["watch_state"].pop(profile_id, None)
            if self.data.get("active_profile") == profile_id:
                self.data["active_profile"] = None
            self._write(self.data)

    @staticmethod
    def _media_identity(media: dict[str, Any]) -> tuple[str, int]:
        media_type = str(media.get("media_type") or "movie").strip().lower()
        if media_type not in {"movie", "tv"}:
            raise StateError("Media type must be movie or tv.")
        try:
            media_id = int(media.get("id"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise StateError("Media must contain a valid TMDB ID.") from exc
        if media_id < 1:
            raise StateError("Media must contain a valid TMDB ID.")
        return media_type, media_id

    @classmethod
    def media_key(cls, media: dict[str, Any]) -> str:
        media_type, media_id = cls._media_identity(media)
        return f"{media_type}:{media_id}"

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
        return {
            key: deepcopy(media.get(key))
            for key in keys
            if media.get(key) is not None
        }

    def _history_unlocked(self, profile_id: str) -> dict[str, Any]:
        self._require_profile_unlocked(profile_id)
        return self.data["watch_state"].setdefault(profile_id, {})

    def entry(self, profile_id: str, media: dict[str, Any]) -> Optional[dict[str, Any]]:
        with self._lock:
            entry = self._history_unlocked(profile_id).get(self.media_key(media))
            return deepcopy(entry) if isinstance(entry, dict) else None

    def decorate(self, profile_id: str, media: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(media)
        entry = self.entry(profile_id, media)
        if entry:
            result["watch"] = {
                key: deepcopy(entry.get(key))
                for key in (
                    "status",
                    "progress",
                    "position_seconds",
                    "duration_seconds",
                    "updated_at",
                    "last_episode",
                )
                if entry.get(key) is not None
            }
        return result

    def mark_started(self, profile_id: str, media: dict[str, Any]) -> None:
        with self._lock:
            history = self._history_unlocked(profile_id)
            key = self.media_key(media)
            previous = history.get(key, {})
            previous_progress = float(previous.get("progress", 0.0) or 0.0)
            progress = 0.03 if previous.get("status") == "finished" else max(0.03, previous_progress)
            history[key] = {
                "status": "in_progress",
                "progress": min(progress, 0.97),
                "updated_at": time.time(),
                "media": self.snapshot(media),
            }
            for name in ("position_seconds", "duration_seconds"):
                if name in previous:
                    history[key][name] = previous[name]
            self._write(self.data)

    def mark_finished(self, profile_id: str, media: dict[str, Any]) -> None:
        with self._lock:
            history = self._history_unlocked(profile_id)
            key = self.media_key(media)
            previous = history.get(key, {})
            history[key] = {
                "status": "finished",
                "progress": 1.0,
                "updated_at": time.time(),
                "media": self.snapshot(media) or deepcopy(previous.get("media", {})),
            }
            if isinstance(previous.get("episodes"), dict):
                history[key]["episodes"] = deepcopy(previous["episodes"])
            self._write(self.data)

    def mark_unwatched(self, profile_id: str, media: dict[str, Any]) -> None:
        with self._lock:
            self._history_unlocked(profile_id).pop(self.media_key(media), None)
            self._write(self.data)

    def set_position(
        self,
        profile_id: str,
        media: dict[str, Any],
        position_seconds: float,
        duration_seconds: float,
    ) -> None:
        with self._lock:
            duration = max(0.0, float(duration_seconds))
            position = max(0.0, float(position_seconds))
            if duration > 0:
                position = min(position, duration)
            progress = position / duration if duration > 0 else 0.03
            if progress >= 0.98:
                self.mark_finished(profile_id, media)
                history = self._history_unlocked(profile_id)
                entry = history[self.media_key(media)]
            else:
                history = self._history_unlocked(profile_id)
                entry = {
                    "status": "in_progress",
                    "progress": max(0.03, min(0.97, progress)),
                    "updated_at": time.time(),
                    "media": self.snapshot(media),
                }
                history[self.media_key(media)] = entry
            entry["position_seconds"] = round(position, 1)
            entry["duration_seconds"] = round(duration, 1)
            self._write(self.data)

    @staticmethod
    def episode_key(season_number: int, episode_number: int) -> str:
        return f"{int(season_number)}:{int(episode_number)}"

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
        return {
            key: deepcopy(episode.get(key))
            for key in keys
            if episode.get(key) is not None
        }

    @staticmethod
    def available_seasons(media: dict[str, Any]) -> list[dict[str, Any]]:
        seasons = [
            deepcopy(season)
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
        numbers = [int(season.get("season_number", 0) or 0) for season in seasons]
        counts = {
            int(season.get("season_number", 0) or 0): int(
                season.get("episode_count", 0) or 0
            )
            for season in seasons
        }
        current = int(season_number)
        episode = int(episode_number)
        if counts.get(current, 0) and episode < counts[current]:
            return current, episode + 1
        try:
            index = numbers.index(current)
        except ValueError:
            return None
        for next_season in numbers[index + 1 :]:
            if next_season > 0 and counts.get(next_season, 0) > 0:
                return next_season, 1
        return None

    def episode_entry(
        self,
        profile_id: str,
        media: dict[str, Any],
        season_number: int,
        episode_number: int,
    ) -> Optional[dict[str, Any]]:
        with self._lock:
            show = self._history_unlocked(profile_id).get(self.media_key(media), {})
            episodes = show.get("episodes", {}) if isinstance(show, dict) else {}
            entry = episodes.get(self.episode_key(season_number, episode_number))
            return deepcopy(entry) if isinstance(entry, dict) else None

    def resume_episode(self, profile_id: str, media: dict[str, Any]) -> tuple[int, int]:
        with self._lock:
            show = self._history_unlocked(profile_id).get(self.media_key(media), {})
            episodes = show.get("episodes", {}) if isinstance(show, dict) else {}
            candidates = [entry for entry in episodes.values() if isinstance(entry, dict)]
            if not candidates:
                regular = [
                    int(season.get("season_number", 0) or 0)
                    for season in self.available_seasons(media)
                    if int(season.get("season_number", 0) or 0) > 0
                ]
                return (regular[0] if regular else 1), 1
            latest = max(
                candidates,
                key=lambda entry: float(entry.get("updated_at", 0.0) or 0.0),
            )
            season_number = int(latest.get("season_number", 1) or 1)
            episode_number = int(latest.get("episode_number", 1) or 1)
            if latest.get("status") == "finished":
                following = self.next_episode_position(media, season_number, episode_number)
                if following:
                    return following
            return season_number, episode_number

    def set_episode_position(
        self,
        profile_id: str,
        media: dict[str, Any],
        episode: dict[str, Any],
        position_seconds: float,
        duration_seconds: float,
    ) -> None:
        duration = max(0.0, float(duration_seconds))
        position = max(0.0, float(position_seconds))
        if duration > 0:
            position = min(position, duration)
        progress = position / duration if duration > 0 else 0.03
        self._set_episode_progress(
            profile_id,
            media,
            episode,
            progress,
            position=position,
            duration=duration,
        )

    def mark_episode_started(
        self,
        profile_id: str,
        media: dict[str, Any],
        episode: dict[str, Any],
    ) -> None:
        season = int(episode.get("season_number", 1) or 1)
        number = int(episode.get("episode_number", 1) or 1)
        previous = self.episode_entry(profile_id, media, season, number) or {}
        progress = float(previous.get("progress", 0.0) or 0.0)
        if previous.get("status") == "finished":
            progress = 0.03
        self._set_episode_progress(profile_id, media, episode, max(0.03, progress))

    def mark_episode_finished(
        self,
        profile_id: str,
        media: dict[str, Any],
        episode: dict[str, Any],
    ) -> None:
        self._set_episode_progress(profile_id, media, episode, 1.0)

    def _set_episode_progress(
        self,
        profile_id: str,
        media: dict[str, Any],
        episode: dict[str, Any],
        progress: float,
        *,
        position: Optional[float] = None,
        duration: Optional[float] = None,
    ) -> None:
        with self._lock:
            if str(media.get("media_type")) != "tv":
                raise StateError("Episode progress requires a TV show.")
            season = int(episode.get("season_number", 1) or 1)
            number = int(episode.get("episode_number", 1) or 1)
            if season < 0 or number < 1:
                raise StateError("Episode numbers are invalid.")
            progress = max(0.0, min(1.0, float(progress)))
            now = time.time()
            history = self._history_unlocked(profile_id)
            key = self.media_key(media)
            previous_show = history.get(key, {})
            episodes = deepcopy(previous_show.get("episodes", {}))
            episode_data = {
                "status": "finished" if progress >= 0.98 else "in_progress",
                "progress": 1.0 if progress >= 0.98 else max(0.03, progress),
                "updated_at": now,
                "season_number": season,
                "episode_number": number,
                "episode": self.episode_snapshot(episode),
            }
            if position is not None:
                episode_data["position_seconds"] = round(max(0.0, position), 1)
            if duration is not None:
                episode_data["duration_seconds"] = round(max(0.0, duration), 1)
            episodes[self.episode_key(season, number)] = episode_data

            following = self.next_episode_position(media, season, number)
            show_finished = episode_data["status"] == "finished" and following is None
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
                "media": self.snapshot(media) or deepcopy(previous_show.get("media", {})),
                "episodes": episodes,
                "last_episode": {"season_number": season, "episode_number": number},
            }
            self._write(self.data)

    def continue_watching(self, profile_id: str) -> list[dict[str, Any]]:
        with self._lock:
            history = self._history_unlocked(profile_id)
            entries = [
                entry
                for entry in history.values()
                if isinstance(entry, dict)
                and entry.get("status") == "in_progress"
                and isinstance(entry.get("media"), dict)
            ]
            entries.sort(
                key=lambda entry: float(entry.get("updated_at", 0.0) or 0.0),
                reverse=True,
            )
            result: list[dict[str, Any]] = []
            for entry in entries:
                media = deepcopy(entry["media"])
                media["watch"] = {
                    key: deepcopy(entry.get(key))
                    for key in (
                        "status",
                        "progress",
                        "position_seconds",
                        "duration_seconds",
                        "updated_at",
                        "last_episode",
                    )
                    if entry.get(key) is not None
                }
                result.append(media)
            return result
