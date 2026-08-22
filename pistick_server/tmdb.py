"""Small server-side TMDB client with a private disk cache."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import threading
import time
from collections.abc import Callable
from http.client import HTTPException, HTTPSConnection
from urllib.parse import urlencode


TMDB_API_HOST = "api.themoviedb.org"
TMDB_API_TIMEOUT = 12
MEMORY_CACHE_LIMIT = 12
DISK_CACHE_LIMIT = 128
LIST_ITEM_FIELDS = (
    "id",
    "title",
    "name",
    "release_date",
    "first_air_date",
    "poster_path",
    "backdrop_path",
    "overview",
    "vote_average",
)
LIST_CACHE_FIELDS = LIST_ITEM_FIELDS + ("media_type",)
VIDEO_FIELDS = ("key", "site", "type", "official")
SEASON_FIELDS = ("id", "name", "season_number", "episode_count", "poster_path")
EPISODE_FIELDS = (
    "id",
    "name",
    "overview",
    "air_date",
    "still_path",
    "runtime",
    "season_number",
    "episode_number",
)


def _connect_ipv4(
    address: tuple[str, int],
    timeout: object = socket._GLOBAL_DEFAULT_TIMEOUT,
    source_address: tuple[str, int] | None = None,
) -> socket.socket:
    """Open the TMDB socket over IPv4 without a slow unusable-IPv6 fallback."""
    host, port = address
    last_error: OSError | None = None
    for family, socktype, protocol, _canonical, target in socket.getaddrinfo(
        host,
        port,
        socket.AF_INET,
        socket.SOCK_STREAM,
    ):
        connection = None
        try:
            connection = socket.socket(family, socktype, protocol)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                connection.settimeout(timeout)
            if source_address:
                connection.bind(source_address)
            connection.connect(target)
            return connection
        except OSError as exc:
            last_error = exc
            if connection is not None:
                connection.close()
    if last_error is not None:
        raise last_error
    raise OSError(f"Could not resolve an IPv4 address for {host}.")


class TMDBError(RuntimeError):
    """A user-safe TMDB request error."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class TMDBClient:
    """Fetch TMDB metadata without exposing the read token to the browser."""

    def __init__(self, token_provider: Callable[[], str], cache_dir: Path):
        self._token_provider = token_provider
        self.cache_dir = Path(cache_dir)
        self._memory: dict[str, tuple[float, dict[str, object]]] = {}
        self._lock = threading.RLock()
        self._network_lock = threading.Lock()
        self._connection: HTTPSConnection | None = None
        self._writes_since_prune = 0
        self._prune_disk_cache()

    @staticmethod
    def _cache_ttl(endpoint: str) -> int:
        if endpoint.startswith("/search/"):
            return 5 * 60
        if endpoint.startswith("/trending/"):
            return 15 * 60
        if "/season/" in endpoint:
            return 24 * 60 * 60
        if endpoint.endswith("/videos"):
            return 7 * 24 * 60 * 60
        parts = endpoint.strip("/").split("/")
        if len(parts) == 2 and parts[0] in {"movie", "tv"} and parts[1].isdigit():
            return 7 * 24 * 60 * 60
        if endpoint.endswith("/popular") or endpoint.endswith("/upcoming"):
            return 30 * 60
        return 6 * 60 * 60

    @staticmethod
    def _key(endpoint: str, params: dict[str, object]) -> str:
        canonical = json.dumps([endpoint, sorted(params.items())], separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    @staticmethod
    def _fields(source: object, names: tuple[str, ...]) -> dict[str, object]:
        if not isinstance(source, dict):
            return {}
        return {name: source[name] for name in names if source.get(name) is not None}

    @classmethod
    def _compact_payload(
        cls, endpoint: str, payload: dict[str, object]
    ) -> dict[str, object]:
        """Keep only fields consumed by this branch before caching a response."""
        if (
            endpoint.startswith("/trending/")
            or endpoint.startswith("/search/")
            or endpoint in {"/movie/popular", "/tv/popular"}
        ):
            results = payload.get("results")
            return {
                "page": payload.get("page", 1),
                "total_pages": payload.get("total_pages", 1),
                "results": [
                    cls._fields(item, LIST_CACHE_FIELDS)
                    for item in results
                    if isinstance(item, dict)
                ]
                if isinstance(results, list)
                else [],
            }

        if "/season/" in endpoint:
            episodes = payload.get("episodes")
            return {
                "season_number": payload.get("season_number"),
                "episodes": [
                    cls._fields(episode, EPISODE_FIELDS)
                    for episode in episodes
                    if isinstance(episode, dict)
                ]
                if isinstance(episodes, list)
                else [],
            }

        if endpoint.endswith("/videos"):
            results = payload.get("results")
            return {
                "results": [
                    cls._fields(video, VIDEO_FIELDS)
                    for video in results
                    if isinstance(video, dict)
                ]
                if isinstance(results, list)
                else []
            }

        parts = endpoint.strip("/").split("/")
        if len(parts) == 2 and parts[0] in {"movie", "tv"} and parts[1].isdigit():
            result = cls._fields(payload, LIST_ITEM_FIELDS)
            if payload.get("number_of_seasons") is not None:
                result["number_of_seasons"] = payload["number_of_seasons"]
            seasons = payload.get("seasons")
            result["seasons"] = [
                cls._fields(season, SEASON_FIELDS)
                for season in seasons
                if isinstance(season, dict)
            ] if isinstance(seasons, list) else []
            videos = payload.get("videos")
            video_results = videos.get("results") if isinstance(videos, dict) else None
            result["videos"] = {
                "results": [
                    cls._fields(video, VIDEO_FIELDS)
                    for video in video_results
                    if isinstance(video, dict)
                ]
                if isinstance(video_results, list)
                else []
            }
            return result

        return payload

    def _remember(
        self,
        key: str,
        payload: dict[str, object],
        timestamp: float | None = None,
    ) -> None:
        with self._lock:
            self._memory[key] = (timestamp if timestamp is not None else time.time(), payload)
            while len(self._memory) > MEMORY_CACHE_LIMIT:
                oldest = min(self._memory, key=lambda candidate: self._memory[candidate][0])
                self._memory.pop(oldest, None)

    def _read_cache(self, key: str, ttl: int) -> dict[str, object] | None:
        now = time.time()
        with self._lock:
            memory = self._memory.get(key)
            if memory and now - memory[0] <= ttl:
                return memory[1]
        path = self._cache_path(key)
        try:
            if now - path.stat().st_mtime > ttl:
                return None
            payload = json.loads(path.read_bytes())
            if not isinstance(payload, dict):
                return None
        except (OSError, ValueError, TypeError):
            return None
        self._remember(key, payload, now)
        return payload

    def _read_stale_cache(self, key: str) -> dict[str, object] | None:
        path = self._cache_path(key)
        try:
            payload = json.loads(path.read_bytes())
        except (OSError, ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _write_cache(self, key: str, payload: dict[str, object]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._cache_path(key)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_bytes(
                json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            )
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        self._remember(key, payload)
        self._writes_since_prune += 1
        if self._writes_since_prune >= 16:
            self._writes_since_prune = 0
            self._prune_disk_cache()

    def _prune_disk_cache(self) -> None:
        """Bound persistent metadata so old searches cannot fill the SD card."""
        try:
            entries = []
            for path in self.cache_dir.iterdir():
                if path.is_file() and path.suffix == ".json":
                    entries.append((path.stat().st_mtime, path))
                elif path.is_file() and path.name.endswith(".tmp"):
                    path.unlink(missing_ok=True)
            if len(entries) <= DISK_CACHE_LIMIT:
                return
            entries.sort(key=lambda item: item[0], reverse=True)
            for _modified, path in entries[DISK_CACHE_LIMIT:]:
                path.unlink(missing_ok=True)
        except OSError:
            # A cache cleanup failure must never stop playback or browsing.
            return

    def _close_connection_unlocked(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def _new_connection_unlocked(self) -> HTTPSConnection:
        connection = HTTPSConnection(TMDB_API_HOST, timeout=TMDB_API_TIMEOUT)
        # The original Pi Zero W commonly has IPv6 enabled without working IPv6
        # internet. Python otherwise tries that dead route before IPv4 for every
        # new TLS connection, which can add an entire socket timeout.
        connection._create_connection = _connect_ipv4
        self._connection = connection
        return connection

    def _network_get(
        self,
        target: str,
        token: str,
    ) -> tuple[int, bytes, str]:
        """Use one serialized keep-alive connection and retry only a stale socket."""
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "Connection": "keep-alive",
            "User-Agent": "PiStick-LocalServer/0.1",
        }
        with self._network_lock:
            reused = self._connection is not None
            connection = self._connection or self._new_connection_unlocked()
            for attempt in range(2):
                try:
                    connection.request("GET", target, headers=headers)
                    response = connection.getresponse()
                    raw = response.read()
                    status = int(response.status)
                    content_encoding = str(
                        response.getheader("Content-Encoding") or ""
                    )
                    should_close = str(
                        response.getheader("Connection") or ""
                    ).lower() == "close"
                    if should_close or status >= 400:
                        self._close_connection_unlocked()
                    return status, raw, content_encoding
                except (HTTPException, TimeoutError, OSError) as exc:
                    self._close_connection_unlocked()
                    # Retrying a timeout can double a genuine outage to 24 s.
                    # Retry only a reused socket that failed immediately.
                    if not reused or attempt or isinstance(exc, TimeoutError):
                        raise
                    connection = self._new_connection_unlocked()
                    reused = False
        raise OSError("TMDB connection failed.")

    def _request(
        self,
        endpoint: str,
        params: dict[str, object] | None = None,
        *,
        cache: bool = True,
        token_override: str | None = None,
    ) -> dict[str, object]:
        token = str(token_override or self._token_provider() or "").strip()
        if not token:
            raise TMDBError("Add your TMDB API Read Access Token in Settings.", 503)
        query = {"language": "en-CA", **(params or {})}
        key = self._key(endpoint, query) if cache else ""
        ttl = self._cache_ttl(endpoint) if cache else 0
        if cache:
            cached = self._read_cache(key, ttl)
            if cached is not None:
                compact = self._compact_payload(endpoint, cached)
                if compact is not cached:
                    self._remember(key, compact)
                return compact

        target = f"/3{endpoint}?{urlencode(query)}"
        try:
            status, raw, content_encoding = self._network_get(target, token)
        except (HTTPException, TimeoutError, OSError) as exc:
            stale = self._read_stale_cache(key) if cache else None
            if stale is not None:
                return self._compact_payload(endpoint, stale)
            raise TMDBError(
                "Could not reach TMDB. Check the internet connection.", 502
            ) from exc
        if status >= 400:
            stale = self._read_stale_cache(key) if cache else None
            if stale is not None and status >= 500:
                return self._compact_payload(endpoint, stale)
            if status in {401, 403}:
                raise TMDBError("TMDB rejected that API Read Access Token.", 401)
            if status == 404:
                raise TMDBError("TMDB could not find that title.", 404)
            raise TMDBError(f"TMDB request failed ({status}).", 502)

        if content_encoding.lower() == "gzip":
            try:
                import gzip

                raw = gzip.decompress(raw)
            except (OSError, EOFError) as exc:
                raise TMDBError("TMDB returned an invalid compressed response.", 502) from exc
        try:
            payload = json.loads(raw)
        except (UnicodeError, ValueError, TypeError) as exc:
            raise TMDBError("TMDB returned an invalid response.", 502) from exc
        if not isinstance(payload, dict):
            raise TMDBError("TMDB returned an invalid response.", 502)
        payload = self._compact_payload(endpoint, payload)
        if cache:
            self._write_cache(key, payload)
        return payload

    def validate_token(self, token: str) -> None:
        # Do not temporarily replace the shared token provider: validation can
        # run while background discovery requests are still in flight.
        self._request(
            "/configuration",
            cache=False,
            token_override=token,
        )

    @staticmethod
    def normalize_item(
        item: dict[str, object], media_type: str | None = None
    ) -> dict[str, object]:
        kind = str(media_type or item.get("media_type") or "").lower()
        if kind not in {"movie", "tv"}:
            if item.get("title") is not None:
                kind = "movie"
            elif item.get("name") is not None:
                kind = "tv"
        result = {key: item[key] for key in LIST_ITEM_FIELDS if item.get(key) is not None}
        result["media_type"] = kind
        date = str(result.get("release_date") or result.get("first_air_date") or "")
        result["year"] = date[:4] if len(date) >= 4 else ""
        return result

    @classmethod
    def _items(
        cls,
        payload: dict[str, object],
        media_type: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, object]]:
        items = []
        for candidate in payload.get("results", []):
            if not isinstance(candidate, dict):
                continue
            normalized = cls.normalize_item(candidate, media_type)
            if normalized.get("media_type") not in {"movie", "tv"}:
                continue
            if not normalized.get("id"):
                continue
            items.append(normalized)
            if limit is not None and len(items) >= limit:
                break
        return items

    def home(self) -> dict[str, object]:
        # One TMDB round trip is much more reliable on the single-core ARMv6
        # Zero W than holding the home request open for five feeds. The same
        # three visible rows are derived from that one compact response.
        payload = self._request("/trending/all/week", {"page": 1})
        trending = self._items(payload)
        rows = [
            {"title": "Trending Now", "items": trending},
            {
                "title": "Trending Movies",
                "items": [item for item in trending if item.get("media_type") == "movie"],
            },
            {
                "title": "Trending TV Shows",
                "items": [item for item in trending if item.get("media_type") == "tv"],
            },
        ]
        hero = next((item for item in trending if item.get("backdrop_path")), None)
        return {"hero": hero, "rows": rows}

    def discover(self, media_type: str, page: int = 1) -> dict[str, object]:
        if media_type not in {"movie", "tv"}:
            raise TMDBError("Discover type must be movie or tv.", 400)
        payload = self._request(f"/{media_type}/popular", {"page": max(1, min(page, 500))})
        return {
            "page": int(payload.get("page", page) or page),
            "total_pages": int(payload.get("total_pages", 1) or 1),
            "items": self._items(payload, media_type, 12),
        }

    def search(self, query: str, page: int = 1) -> dict[str, object]:
        cleaned = str(query or "").strip()[:120]
        if not cleaned:
            return {"page": 1, "total_pages": 1, "items": []}
        payload = self._request(
            "/search/multi",
            {"query": cleaned, "page": max(1, min(page, 500)), "include_adult": "false"},
        )
        return {
            "page": int(payload.get("page", page) or page),
            "total_pages": int(payload.get("total_pages", 1) or 1),
            "items": self._items(payload, limit=12),
        }

    def details(self, media_type: str, media_id: int) -> dict[str, object]:
        if media_type not in {"movie", "tv"}:
            raise TMDBError("Media type must be movie or tv.", 400)
        if int(media_id) < 1:
            raise TMDBError("TMDB ID is invalid.", 400)
        payload = self._request(
            f"/{media_type}/{int(media_id)}",
            {"append_to_response": "videos"},
        )
        result = self.normalize_item(payload, media_type)
        if payload.get("number_of_seasons") is not None:
            result["number_of_seasons"] = payload["number_of_seasons"]
        result["seasons"] = [
            {key: season[key] for key in SEASON_FIELDS if season.get(key) is not None}
            for season in payload.get("seasons", [])
            if isinstance(season, dict)
        ]
        videos = payload.get("videos", {})
        video_results = videos.get("results", []) if isinstance(videos, dict) else []
        result["videos"] = {
            "results": [
                {key: video[key] for key in VIDEO_FIELDS if video.get(key) is not None}
                for video in video_results
                if isinstance(video, dict)
            ]
        }
        return result

    def videos(self, media_type: str, media_id: int) -> dict[str, object]:
        """Fetch only the small metadata block a movie card does not have."""
        if media_type not in {"movie", "tv"}:
            raise TMDBError("Media type must be movie or tv.", 400)
        if int(media_id) < 1:
            raise TMDBError("TMDB ID is invalid.", 400)
        payload = self._request(f"/{media_type}/{int(media_id)}/videos")
        return {
            "results": [
                {key: video[key] for key in VIDEO_FIELDS if video.get(key) is not None}
                for video in payload.get("results", [])
                if isinstance(video, dict)
            ]
        }

    def season(self, show_id: int, season_number: int) -> dict[str, object]:
        if int(show_id) < 1 or int(season_number) < 0:
            raise TMDBError("Show or season number is invalid.", 400)
        payload = self._request(f"/tv/{int(show_id)}/season/{int(season_number)}")
        episodes = []
        for candidate in payload.get("episodes", []):
            if not isinstance(candidate, dict):
                continue
            episode = {
                key: candidate[key]
                for key in EPISODE_FIELDS
                if candidate.get(key) is not None
            }
            episode["season_number"] = int(
                episode.get("season_number", season_number) or season_number
            )
            episode["episode_number"] = int(episode.get("episode_number", 0) or 0)
            if episode["episode_number"] > 0:
                episodes.append(episode)
        return {
            "season_number": int(
                payload.get("season_number", season_number) or season_number
            ),
            "episodes": episodes,
        }
