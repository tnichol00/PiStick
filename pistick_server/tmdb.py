"""Small server-side TMDB client with a private disk cache."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TMDB_API_BASE = "https://api.themoviedb.org/3"


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
        self._memory: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.RLock()
        self.low_memory = os.getenv("PISTICK_LOW_MEMORY", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

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
        return 6 * 60 * 60

    @staticmethod
    def _key(endpoint: str, params: dict[str, Any]) -> str:
        canonical = json.dumps([endpoint, sorted(params.items())], separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _remember(
        self,
        key: str,
        payload: dict[str, Any],
        timestamp: Optional[float] = None,
    ) -> None:
        with self._lock:
            self._memory[key] = (timestamp if timestamp is not None else time.time(), payload)
            memory_limit = 24 if self.low_memory else 96
            while len(self._memory) > memory_limit:
                oldest = min(self._memory, key=lambda candidate: self._memory[candidate][0])
                self._memory.pop(oldest, None)

    def _read_cache(self, key: str, ttl: int) -> Optional[dict[str, Any]]:
        now = time.time()
        with self._lock:
            memory = self._memory.get(key)
            if memory and now - memory[0] <= ttl:
                return memory[1]
        path = self._cache_path(key)
        try:
            if now - path.stat().st_mtime > ttl:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
        except (OSError, ValueError, TypeError):
            return None
        self._remember(key, payload, now)
        return payload

    def _read_stale_cache(self, key: str) -> Optional[dict[str, Any]]:
        path = self._cache_path(key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _write_cache(self, key: str, payload: dict[str, Any]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._cache_path(key)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        self._remember(key, payload)

    def _request(
        self,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
        *,
        cache: bool = True,
        token_override: Optional[str] = None,
    ) -> dict[str, Any]:
        token = str(token_override or self._token_provider() or "").strip()
        if not token:
            raise TMDBError("Add your TMDB API Read Access Token in Settings.", 503)
        query = {"language": "en-CA", **(params or {})}
        key = self._key(endpoint, query)
        ttl = self._cache_ttl(endpoint)
        if cache:
            cached = self._read_cache(key, ttl)
            if cached is not None:
                return cached

        url = f"{TMDB_API_BASE}{endpoint}?{urlencode(query)}"
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": "PiStick-LocalServer/0.1",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read()
        except HTTPError as exc:
            stale = self._read_stale_cache(key) if cache else None
            if stale is not None and exc.code >= 500:
                return stale
            if exc.code in {401, 403}:
                raise TMDBError("TMDB rejected that API Read Access Token.", 401) from exc
            if exc.code == 404:
                raise TMDBError("TMDB could not find that title.", 404) from exc
            raise TMDBError(f"TMDB request failed ({exc.code}).", 502) from exc
        except (URLError, TimeoutError, OSError) as exc:
            stale = self._read_stale_cache(key) if cache else None
            if stale is not None:
                return stale
            raise TMDBError("Could not reach TMDB. Check the internet connection.", 502) from exc

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError) as exc:
            raise TMDBError("TMDB returned an invalid response.", 502) from exc
        if not isinstance(payload, dict):
            raise TMDBError("TMDB returned an invalid response.", 502)
        if cache:
            self._write_cache(key, payload)
        return payload

    def validate_token(self, token: str) -> None:
        # Do not temporarily replace the shared token provider: validation can
        # run while background discovery requests are still in flight.
        self._request(
            "/configuration",
            {"language": "en-CA"},
            cache=False,
            token_override=token,
        )

    @staticmethod
    def normalize_item(item: dict[str, Any], media_type: Optional[str] = None) -> dict[str, Any]:
        result = dict(item)
        kind = str(media_type or result.get("media_type") or "").lower()
        if kind not in {"movie", "tv"}:
            if result.get("title") is not None:
                kind = "movie"
            elif result.get("name") is not None:
                kind = "tv"
        result["media_type"] = kind
        date = str(result.get("release_date") or result.get("first_air_date") or "")
        result["year"] = date[:4] if len(date) >= 4 else ""
        return result

    @classmethod
    def _items(cls, payload: dict[str, Any], media_type: Optional[str] = None) -> list[dict[str, Any]]:
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
        return items

    def home(self) -> dict[str, Any]:
        definitions = (
            ("Trending Now", "/trending/all/week", None),
            ("Popular Movies", "/movie/popular", "movie"),
            ("Top Rated Movies", "/movie/top_rated", "movie"),
            ("Popular TV Shows", "/tv/popular", "tv"),
            ("Top Rated TV", "/tv/top_rated", "tv"),
        )
        rows: dict[int, dict[str, Any]] = {}
        errors: list[str] = []
        worker_count = 2 if self.low_memory else len(definitions)
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {
                pool.submit(self._request, endpoint, {"page": 1}): (index, title, kind)
                for index, (title, endpoint, kind) in enumerate(definitions)
            }
            for future in as_completed(futures):
                index, title, kind = futures[future]
                try:
                    rows[index] = {"title": title, "items": self._items(future.result(), kind)}
                except TMDBError as exc:
                    errors.append(str(exc))
                    rows[index] = {"title": title, "items": []}
        ordered = [rows[index] for index in range(len(definitions))]
        hero = next(
            (
                item
                for row in ordered
                for item in row["items"]
                if item.get("backdrop_path")
            ),
            None,
        )
        if hero is None and errors:
            raise TMDBError(errors[0])
        return {"hero": hero, "rows": ordered}

    def discover(self, media_type: str, page: int = 1) -> dict[str, Any]:
        if media_type not in {"movie", "tv"}:
            raise TMDBError("Discover type must be movie or tv.", 400)
        payload = self._request(f"/{media_type}/popular", {"page": max(1, min(page, 500))})
        return {
            "page": int(payload.get("page", page) or page),
            "total_pages": int(payload.get("total_pages", 1) or 1),
            "items": self._items(payload, media_type),
        }

    def search(self, query: str, page: int = 1) -> dict[str, Any]:
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
            "items": self._items(payload),
        }

    def details(self, media_type: str, media_id: int) -> dict[str, Any]:
        if media_type not in {"movie", "tv"}:
            raise TMDBError("Media type must be movie or tv.", 400)
        if int(media_id) < 1:
            raise TMDBError("TMDB ID is invalid.", 400)
        payload = self._request(
            f"/{media_type}/{int(media_id)}",
            {"append_to_response": "videos,credits"},
        )
        return self.normalize_item(payload, media_type)

    def season(self, show_id: int, season_number: int) -> dict[str, Any]:
        if int(show_id) < 1 or int(season_number) < 0:
            raise TMDBError("Show or season number is invalid.", 400)
        payload = self._request(f"/tv/{int(show_id)}/season/{int(season_number)}")
        episodes = []
        for candidate in payload.get("episodes", []):
            if not isinstance(candidate, dict):
                continue
            episode = dict(candidate)
            episode["season_number"] = int(
                episode.get("season_number", season_number) or season_number
            )
            episode["episode_number"] = int(episode.get("episode_number", 0) or 0)
            if episode["episode_number"] > 0:
                episodes.append(episode)
        result = dict(payload)
        result["season_number"] = int(payload.get("season_number", season_number) or season_number)
        result["episodes"] = episodes
        return result
