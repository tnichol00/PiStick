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
    def _default_data() -> dict[str, object]:
        profile_id = "profile-1"
        return {
            "active_profile": None,
            "profiles": [
                {"id": profile_id, "name": "Profile 1", "avatar": "red"},
            ],
            "watch_state": {profile_id: {}},
        }

    def _load(self) -> dict[str, object]:
        source_exists = self.path.is_file()
        repair_needed = not source_exists
        try:
            raw = json.loads(self.path.read_bytes())
        except (OSError, ValueError, TypeError):
            raw = self._default_data()
            repair_needed = True

        if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), list):
            raw = self._default_data()
            repair_needed = True

        profiles: list[dict[str, str]] = []
        seen: set[str] = set()
        for index, candidate in enumerate(raw.get("profiles", [])):
            if not isinstance(candidate, dict):
                continue
            profile_id = str(candidate.get("id", "")).strip()
            if not profile_id or profile_id in seen:
                profile_id = f"profile-{os.urandom(5).hex()}"
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
        # Normal boots should not rewrite the SD card. Persist only a new or
        # repaired schema; mutations below remain atomic as before.
        if repair_needed or raw != normalized:
            self._write(normalized)
        return normalized

    @staticmethod
    def _payload(data: dict[str, object]) -> bytes:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )

    def _write(self, data: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(self._payload(data))
        os.replace(temporary, self.path)

    def save(self) -> None:
        with self._lock:
            self._write(self.data)

    def profiles_payload(self) -> dict[str, object]:
        with self._lock:
            return {
                "profiles": deepcopy(self.data["profiles"]),
                "active_profile": self.data.get("active_profile"),
                "max_profiles": self.MAX_PROFILES,
            }

    def profiles(self) -> list[dict[str, object]]:
        with self._lock:
            return deepcopy(self.data["profiles"])

    def _profile_unlocked(
        self, profile_id: str | None
    ) -> dict[str, object] | None:
        if not profile_id:
            return None
        return next(
            (profile for profile in self.data["profiles"] if profile["id"] == profile_id),
            None,
        )

    def profile(self, profile_id: str | None) -> dict[str, object] | None:
        with self._lock:
            profile = self._profile_unlocked(profile_id)
            return deepcopy(profile) if profile else None

    def resolve_profile_id(self, candidate: str | None = None) -> str:
        """Resolve and validate a profile with one lock and no deep copies."""
        with self._lock:
            profile_id = str(candidate or self.data.get("active_profile") or "")
            if self._profile_unlocked(profile_id) is None:
                raise StateError("Choose a profile first.")
            return profile_id

    def _require_profile_unlocked(self, profile_id: str | None) -> dict[str, object]:
        profile = self._profile_unlocked(profile_id)
        if profile is None:
            raise StateError("Choose a valid profile first.")
        return profile

    def activate_profile(self, profile_id: str) -> dict[str, object]:
        with self._lock:
            profile = self._require_profile_unlocked(profile_id)
            self.data["active_profile"] = profile_id
            self.data["watch_state"].setdefault(profile_id, {})
            self._write(self.data)
            return deepcopy(profile)

    def add_profile(self, name: str) -> dict[str, object]:
        with self._lock:
            profiles = self.data["profiles"]
            if len(profiles) >= self.MAX_PROFILES:
                raise StateError(f"PiStick supports up to {self.MAX_PROFILES} profiles.")
            cleaned = str(name or "").strip()[:40]
            if not cleaned:
                cleaned = f"Profile {len(profiles) + 1}"
            profile = {
                "id": f"profile-{os.urandom(5).hex()}",
                "name": cleaned,
                "avatar": self.DEFAULT_AVATARS[len(profiles) % len(self.DEFAULT_AVATARS)],
            }
            profiles.append(profile)
            self.data["watch_state"][profile["id"]] = {}
            self._write(self.data)
            return deepcopy(profile)

    def rename_profile(self, profile_id: str, name: str) -> dict[str, object]:
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
    def _media_identity(media: dict[str, object]) -> tuple[str, int]:
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
    def media_key(cls, media: dict[str, object]) -> str:
        media_type, media_id = cls._media_identity(media)
        return f"{media_type}:{media_id}"

    @staticmethod
    def snapshot(media: dict[str, object]) -> dict[str, object]:
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
        return deepcopy({key: media[key] for key in keys if media.get(key) is not None})

    def _history_unlocked(self, profile_id: str) -> dict[str, object]:
        self._require_profile_unlocked(profile_id)
        return self.data["watch_state"].setdefault(profile_id, {})

    def entry(
        self, profile_id: str, media: dict[str, object]
    ) -> dict[str, object] | None:
        with self._lock:
            entry = self._history_unlocked(profile_id).get(self.media_key(media))
            return deepcopy(entry) if isinstance(entry, dict) else None

    @staticmethod
    def _watch_payload(entry: dict[str, object]) -> dict[str, object]:
        return {
            key: deepcopy(entry[key])
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

    def _decorate_unlocked(
        self,
        history: dict[str, object],
        media: dict[str, object],
        *,
        shallow: bool = False,
    ) -> dict[str, object]:
        # TMDB list items contain only scalar display fields, so a shallow copy
        # avoids thousands of recursive copy calls while preserving the input.
        result = dict(media) if shallow else deepcopy(media)
        entry = history.get(self.media_key(media))
        if isinstance(entry, dict):
            result["watch"] = self._watch_payload(entry)
        return result

    def decorate(
        self, profile_id: str, media: dict[str, object]
    ) -> dict[str, object]:
        with self._lock:
            history = self._history_unlocked(profile_id)
            return self._decorate_unlocked(history, media)

    def decorate_many(
        self,
        profile_id: str,
        items: object,
    ) -> list[dict[str, object]]:
        """Decorate a metadata row under one state lock."""
        if not isinstance(items, list):
            return []
        with self._lock:
            history = self._history_unlocked(profile_id)
            return [
                self._decorate_unlocked(history, media, shallow=True)
                for media in items
                if isinstance(media, dict)
            ]

    def mark_started(self, profile_id: str, media: dict[str, object]) -> None:
        with self._lock:
            history = self._history_unlocked(profile_id)
            key = self.media_key(media)
            previous = history.get(key, {})
            previous_progress = float(previous.get("progress", 0.0) or 0.0)
            progress = (
                0.03
                if previous.get("status") == "finished"
                else max(0.03, previous_progress)
            )
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

    def mark_finished(self, profile_id: str, media: dict[str, object]) -> None:
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
                history[key]["episodes"] = previous["episodes"]
            self._write(self.data)

    def mark_unwatched(self, profile_id: str, media: dict[str, object]) -> None:
        with self._lock:
            self._history_unlocked(profile_id).pop(self.media_key(media), None)
            self._write(self.data)

    def set_position(
        self,
        profile_id: str,
        media: dict[str, object],
        position_seconds: float,
        duration_seconds: float,
    ) -> None:
        with self._lock:
            duration = max(0.0, float(duration_seconds))
            position = max(0.0, float(position_seconds))
            if duration > 0:
                position = min(position, duration)
            progress = position / duration if duration > 0 else 0.03
            history = self._history_unlocked(profile_id)
            key = self.media_key(media)
            previous = history.get(key, {})
            saved_media = previous.get("media") if isinstance(previous, dict) else None
            entry = {
                "status": "finished" if progress >= 0.98 else "in_progress",
                "progress": 1.0 if progress >= 0.98 else max(0.03, min(0.97, progress)),
                "updated_at": time.time(),
                "media": saved_media if isinstance(saved_media, dict) else self.snapshot(media),
            }
            entry["position_seconds"] = round(position, 1)
            entry["duration_seconds"] = round(duration, 1)
            history[key] = entry
            self._write(self.data)

    @staticmethod
    def episode_key(season_number: int, episode_number: int) -> str:
        return f"{int(season_number)}:{int(episode_number)}"

    @staticmethod
    def episode_snapshot(episode: dict[str, object]) -> dict[str, object]:
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
        return deepcopy(
            {key: episode[key] for key in keys if episode.get(key) is not None}
        )

    @staticmethod
    def available_seasons(media: dict[str, object]) -> list[dict[str, object]]:
        seasons = [
            season
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
        media: dict[str, object],
        season_number: int,
        episode_number: int,
    ) -> tuple[int, int] | None:
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
        media: dict[str, object],
        season_number: int,
        episode_number: int,
    ) -> dict[str, object] | None:
        with self._lock:
            show = self._history_unlocked(profile_id).get(self.media_key(media), {})
            episodes = show.get("episodes", {}) if isinstance(show, dict) else {}
            entry = episodes.get(self.episode_key(season_number, episode_number))
            return deepcopy(entry) if isinstance(entry, dict) else None

    def decorate_episodes(
        self,
        profile_id: str,
        media: dict[str, object],
        source_episodes: object,
    ) -> list[dict[str, object]]:
        """Apply episode watch badges with one lock and one history lookup."""
        if not isinstance(source_episodes, list):
            return []
        with self._lock:
            show = self._history_unlocked(profile_id).get(self.media_key(media), {})
            saved_episodes = show.get("episodes", {}) if isinstance(show, dict) else {}
            result = []
            for source in source_episodes:
                if not isinstance(source, dict):
                    continue
                episode = deepcopy(source)
                saved = saved_episodes.get(
                    self.episode_key(
                        int(episode["season_number"]),
                        int(episode["episode_number"]),
                    )
                )
                if isinstance(saved, dict):
                    episode["watch"] = {
                        key: saved[key]
                        for key in (
                            "status",
                            "progress",
                            "position_seconds",
                            "duration_seconds",
                        )
                        if saved.get(key) is not None
                    }
                result.append(episode)
            return result

    def resume_episode(
        self, profile_id: str, media: dict[str, object]
    ) -> tuple[int, int]:
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
        media: dict[str, object],
        episode: dict[str, object],
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
        media: dict[str, object],
        episode: dict[str, object],
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
        media: dict[str, object],
        episode: dict[str, object],
    ) -> None:
        self._set_episode_progress(profile_id, media, episode, 1.0)

    def _set_episode_progress(
        self,
        profile_id: str,
        media: dict[str, object],
        episode: dict[str, object],
        progress: float,
        *,
        position: float | None = None,
        duration: float | None = None,
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
            if not isinstance(previous_show, dict):
                previous_show = {}
            episodes = previous_show.get("episodes", {})
            if not isinstance(episodes, dict):
                episodes = {}
            episode_key = self.episode_key(season, number)
            previous_episode = episodes.get(episode_key, {})
            saved_episode = (
                previous_episode.get("episode")
                if isinstance(previous_episode, dict)
                else None
            )
            episode_data = {
                "status": "finished" if progress >= 0.98 else "in_progress",
                "progress": 1.0 if progress >= 0.98 else max(0.03, progress),
                "updated_at": now,
                "season_number": season,
                "episode_number": number,
                "episode": (
                    saved_episode
                    if isinstance(saved_episode, dict)
                    else self.episode_snapshot(episode)
                ),
            }
            if position is not None:
                episode_data["position_seconds"] = round(max(0.0, position), 1)
            if duration is not None:
                episode_data["duration_seconds"] = round(max(0.0, duration), 1)
            episodes[episode_key] = episode_data

            show_finished = False
            if episode_data["status"] == "finished":
                show_finished = self.next_episode_position(media, season, number) is None
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
                "media": (
                    previous_show.get("media")
                    if isinstance(previous_show.get("media"), dict)
                    else self.snapshot(media)
                ),
                "episodes": episodes,
                "last_episode": {"season_number": season, "episode_number": number},
            }
            self._write(self.data)

    def continue_watching(self, profile_id: str) -> list[dict[str, object]]:
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
            result: list[dict[str, object]] = []
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
