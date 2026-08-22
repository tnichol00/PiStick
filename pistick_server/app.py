"""HTTP application for PiStick's local screen and optional home-LAN clients."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from playback_api import PlaybackAPIError, getmovie, getshow

from . import __version__
from .config import ConfigStore
from .state import StateError, WatchStateStore
from .system_control import SystemControlError, SystemController
from .tmdb import TMDBClient, TMDBError


MAX_JSON_BODY = 1_000_000
LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1"}
LAN_NAMES = {"pistick", "pistick.local"}
NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
IMMUTABLE_ASSET_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
}
STATIC_CONTENT_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
}
PROFILE_RE = re.compile(r"/api/profiles/([A-Za-z0-9_-]+)")
PROFILE_ACTIVATE_RE = re.compile(r"/api/profiles/([A-Za-z0-9_-]+)/activate")
DISCOVER_RE = re.compile(r"/api/discover/(movie|tv)")
MEDIA_RE = re.compile(r"/api/media/(movie|tv)/(\d+)")
MEDIA_EXTRAS_RE = re.compile(r"/api/media/(movie|tv)/(\d+)/extras")
SEASON_RE = re.compile(r"/api/tv/(\d+)/season/(\d+)")
SYSTEM_ACTIONS = {
    ("/api/system/wifi/scan", "POST"): "wifi-scan",
    ("/api/system/wifi/connect", "POST"): "wifi-connect",
    ("/api/system/bluetooth/scan", "POST"): "bluetooth-scan",
    ("/api/system/bluetooth/pair", "POST"): "bluetooth-pair",
}


class _Logger:
    """Tiny stderr logger; full logging machinery is wasteful on a Zero W."""

    verbose = False

    @staticmethod
    def _write(level: str, message: str, args: tuple[object, ...]) -> None:
        rendered = message % args if args else message
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"{timestamp} {level} {rendered}", file=sys.stderr, flush=True)

    def debug(self, message: str, *args: object) -> None:
        if self.verbose:
            self._write("DEBUG", message, args)

    def info(self, message: str, *args: object) -> None:
        self._write("INFO", message, args)

    def error(self, message: str, *args: object) -> None:
        self._write("ERROR", message, args)

    def exception(self, message: str, *args: object) -> None:
        self.error(message, *args)
        import traceback

        traceback.print_exc()


LOGGER = _Logger()


class Response:
    """Small immutable-by-convention HTTP response without dataclass overhead."""

    __slots__ = ("status", "body", "content_type", "headers")

    def __init__(
        self,
        status: int,
        body: bytes,
        content_type: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.body = body
        self.content_type = content_type
        self.headers = headers or {}

    @classmethod
    def json(cls, payload: object, status: int = 200) -> "Response":
        return cls(
            status=status,
            body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            ),
            content_type="application/json; charset=utf-8",
            headers=NO_STORE_HEADERS,
        )

    @classmethod
    def text(cls, value: str, status: int = 200) -> "Response":
        return cls(
            status=status,
            body=value.encode("utf-8"),
            content_type="text/plain; charset=utf-8",
            headers=NO_STORE_HEADERS,
        )


def _default_data_dir() -> Path:
    configured = os.getenv("PISTICK_SERVER_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path("/var/lib/pistick/data")


def _installed_release_id() -> str:
    release_root = Path(__file__).resolve().parents[1]
    if release_root.parent.name == "releases" and re.fullmatch(
        r"\d{14}-(?:[0-9a-f]{7,40}|local)(?:-\d+)?", release_root.name
    ):
        return release_root.name
    return "development"


def _integer(value: object, name: str, minimum: int = 1, maximum: int = 2_147_483_647) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a whole number.")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a whole number.") from exc
    if number < minimum or number > maximum:
        raise ValueError(f"{name} is outside the allowed range.")
    return number


def _number(value: object, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a number.") from exc
    if not 0 <= number <= 10_000_000:
        raise ValueError(f"{name} is outside the allowed range.")
    return number


def _address_scope(value: str) -> tuple[str, str] | None:
    """Classify numeric addresses without importing the large ipaddress module."""
    cleaned = str(value or "").split("%", 1)[0]
    try:
        packed = socket.inet_pton(socket.AF_INET, cleaned)
    except OSError:
        packed = b""
    if packed:
        first, second = packed[0], packed[1]
        normalized = socket.inet_ntop(socket.AF_INET, packed)
        if packed == b"\0\0\0\0":
            return "unspecified", normalized
        if first == 127:
            return "loopback", normalized
        if (
            first == 10
            or (first == 172 and 16 <= second <= 31)
            or (first == 192 and second == 168)
            or (first == 169 and second == 254)
            or (first == 100 and 64 <= second <= 127)
        ):
            return "lan", normalized
        return "other", normalized

    try:
        packed = socket.inet_pton(socket.AF_INET6, cleaned)
    except OSError:
        return None
    normalized = socket.inet_ntop(socket.AF_INET6, packed)
    if packed == b"\0" * 16:
        return "unspecified", normalized
    if packed == b"\0" * 15 + b"\1":
        return "loopback", normalized
    if packed[:12] == b"\0" * 10 + b"\xff\xff":
        mapped = _address_scope(socket.inet_ntop(socket.AF_INET, packed[-4:]))
        return (mapped[0], normalized) if mapped else None
    if (packed[0] & 0xFE) == 0xFC or (
        packed[0] == 0xFE and (packed[1] & 0xC0) == 0x80
    ):
        return "lan", normalized
    return "other", normalized


class PiStickApplication:
    """Route same-origin browser requests to state and TMDB services."""

    def __init__(
        self,
        data_dir: Path,
        *,
        static_dir: Path | None = None,
        tmdb: TMDBClient | None = None,
        system_controller: SystemController | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config = ConfigStore(self.data_dir / "config.json")
        self.advertised_port = self.config.port
        self.release_id = _installed_release_id()
        self.state = WatchStateStore(self.data_dir / "state.json")
        self.static_dir = Path(static_dir or Path(__file__).with_name("static")).resolve()
        self.static_assets, self.asset_version = self._load_static_assets()
        # This branch exclusively targets the original 512 MB ARMv6 Pi Zero W.
        self.low_memory = True
        self.tmdb = tmdb or TMDBClient(
            lambda: self.config.tmdb_read_token,
            self.data_dir / "cache" / "tmdb",
        )
        self.system_controller = system_controller or SystemController()
        self.shutdown_callback = None

    def _load_static_assets(self) -> tuple[dict[str, bytes], str]:
        """Load the three UI files once and fingerprint the executable assets."""
        assets: dict[str, bytes] = {}
        for name in STATIC_CONTENT_TYPES:
            try:
                assets[name] = (self.static_dir / name).read_bytes()
            except OSError:
                # Missing assets remain ordinary 404s and fail installer checks.
                continue

        digest = hashlib.sha256()
        for name in ("styles.css", "app.js"):
            digest.update(name.encode("ascii"))
            digest.update(assets.get(name, b""))
        version = digest.hexdigest()[:12]
        if "index.html" in assets:
            assets["index.html"] = assets["index.html"].replace(
                b"__PISTICK_ASSET_VERSION__", version.encode("ascii")
            )
        return assets, version

    @property
    def lan_url(self) -> str:
        suffix = "" if self.advertised_port == 80 else f":{self.advertised_port}"
        return f"http://pistick.local{suffix}"

    @staticmethod
    def _json_body(body: bytes) -> dict[str, object]:
        if not body:
            return {}
        try:
            payload = json.loads(body)
        except (UnicodeError, ValueError, TypeError) as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def _profile_id(self, candidate: str | None = None) -> str:
        return self.state.resolve_profile_id(candidate)

    @staticmethod
    def _media(payload: object) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise StateError("A media object is required.")
        media = dict(payload)
        WatchStateStore.media_key(media)
        return media

    @staticmethod
    def _query_value(query: dict[str, list[str]], name: str, default: str = "") -> str:
        values = query.get(name)
        return values[0] if values else default

    def _decorate_home(
        self, profile_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        continue_items = self.state.continue_watching(profile_id)[:12]
        rows = []
        if continue_items:
            rows.append({"title": "Continue Watching", "items": continue_items})
        for row in payload.get("rows", []):
            source_items = row.get("items", [])[:12]
            items = self.state.decorate_many(profile_id, source_items)
            rows.append({"title": row.get("title", "Explore"), "items": items})
        hero = payload.get("hero")
        return {
            "hero": self.state.decorate(profile_id, hero) if isinstance(hero, dict) else None,
            "rows": rows,
        }

    def dispatch(
        self,
        method: str,
        raw_target: str,
        headers: object,
        body: bytes = b"",
        *,
        local_request: bool = True,
    ) -> Response:
        method = method.upper()
        parsed = urlsplit(raw_target)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        try:
            if path == "/health" and method == "GET":
                return Response.json(
                    {
                        "ok": True,
                        "version": __version__,
                        "release": self.release_id,
                    }
                )

            if path == "/api/status" and method == "GET":
                return Response.json(
                    {
                        "name": "PiStick Server",
                        "version": __version__,
                        "release": self.release_id,
                        "low_memory": self.low_memory,
                        "local_control": bool(
                            local_request and self.system_controller.available
                        ),
                        "lan_url": self.lan_url,
                        **self.config.public_payload(),
                        "port": self.advertised_port,
                    }
                )

            if path == "/api/profiles" and method == "GET":
                return Response.json(self.state.profiles_payload())

            if path == "/api/profiles" and method == "POST":
                payload = self._json_body(body)
                profile = self.state.add_profile(str(payload.get("name") or ""))
                return Response.json({"profile": profile}, 201)

            match = PROFILE_RE.fullmatch(path)
            if match and method == "PATCH":
                payload = self._json_body(body)
                profile = self.state.rename_profile(match.group(1), str(payload.get("name") or ""))
                return Response.json({"profile": profile})
            if match and method == "DELETE":
                self.state.delete_profile(match.group(1))
                return Response.json({"ok": True})

            match = PROFILE_ACTIVATE_RE.fullmatch(path)
            if match and method == "POST":
                profile = self.state.activate_profile(match.group(1))
                return Response.json({"profile": profile})

            if path == "/api/settings/tmdb":
                return Response.json(
                    {"error": "The TMDB token can only be changed over SSH."}, 403
                )

            if path.startswith("/api/system/"):
                if not local_request:
                    return Response.json(
                        {"error": "System settings are available only on the Pi HDMI screen."},
                        403,
                    )
                if not self.system_controller.available:
                    return Response.json(
                        {"error": "System controls are not installed on this device."}, 503
                    )
                if path == "/api/system/status" and method == "GET":
                    result = self.system_controller.run("status")
                    result.update(
                        {
                            "lan_enabled": self.config.lan_enabled,
                            "lan_url": self.lan_url,
                        }
                    )
                    return Response.json(result)
                if path == "/api/system/lan" and method == "POST":
                    payload = self._json_body(body)
                    enabled = payload.get("enabled")
                    if not isinstance(enabled, bool):
                        raise ValueError("LAN access must be turned on or off.")
                    self.config.set_lan_enabled(enabled)
                    return Response.json(
                        {
                            "ok": True,
                            "lan_enabled": self.config.lan_enabled,
                            "lan_url": self.lan_url,
                        }
                    )
                action = SYSTEM_ACTIONS.get((path, method))
                if action:
                    return Response.json(
                        self.system_controller.run(action, self._json_body(body))
                    )

            if path == "/api/home" and method == "GET":
                profile_id = self._profile_id(self._query_value(query, "profile_id"))
                return Response.json(self._decorate_home(profile_id, self.tmdb.home()))

            match = DISCOVER_RE.fullmatch(path)
            if match and method == "GET":
                page = _integer(self._query_value(query, "page", "1"), "Page", 1, 500)
                profile_id = self._profile_id(self._query_value(query, "profile_id"))
                payload = self.tmdb.discover(match.group(1), page)
                payload["items"] = payload["items"][:12]
                payload["items"] = self.state.decorate_many(
                    profile_id, payload["items"]
                )
                return Response.json(payload)

            if path == "/api/search" and method == "GET":
                profile_id = self._profile_id(self._query_value(query, "profile_id"))
                page = _integer(self._query_value(query, "page", "1"), "Page", 1, 500)
                payload = self.tmdb.search(self._query_value(query, "q"), page)
                payload["items"] = payload["items"][:12]
                payload["items"] = self.state.decorate_many(
                    profile_id, payload["items"]
                )
                return Response.json(payload)

            match = MEDIA_EXTRAS_RE.fullmatch(path)
            if match and method == "GET":
                profile_id = self._profile_id(self._query_value(query, "profile_id"))
                media_type = match.group(1)
                media_id = _integer(match.group(2), "TMDB ID")
                # List cards already carry every visible movie field. Fetch only
                # the missing trailer metadata instead of downloading the full
                # title record again, then attach the latest watch status.
                result = self.state.decorate(
                    profile_id,
                    {
                        "id": media_id,
                        "media_type": media_type,
                        "videos": self.tmdb.videos(media_type, media_id),
                    },
                )
                return Response.json({"media": result})

            match = MEDIA_RE.fullmatch(path)
            if match and method == "GET":
                profile_id = self._profile_id(self._query_value(query, "profile_id"))
                media = self.tmdb.details(match.group(1), _integer(match.group(2), "TMDB ID"))
                result = self.state.decorate(profile_id, media)
                if media["media_type"] == "tv":
                    season, episode = self.state.resume_episode(profile_id, media)
                    result["resume_episode"] = {
                        "season_number": season,
                        "episode_number": episode,
                    }
                return Response.json({"media": result})

            match = SEASON_RE.fullmatch(path)
            if match and method == "GET":
                profile_id = self._profile_id(self._query_value(query, "profile_id"))
                show_id = _integer(match.group(1), "TMDB ID")
                season_number = _integer(match.group(2), "Season", 0, 10_000)
                show = self.tmdb.details("tv", show_id)
                season = self.tmdb.season(show_id, season_number)
                season["episodes"] = self.state.decorate_episodes(
                    profile_id, show, season.get("episodes", [])
                )
                return Response.json({"season": season})

            if path == "/api/continue" and method == "GET":
                profile_id = self._profile_id(self._query_value(query, "profile_id"))
                return Response.json({"items": self.state.continue_watching(profile_id)})

            if path == "/api/play" and method == "POST":
                payload = self._json_body(body)
                profile_id = self._profile_id(str(payload.get("profile_id") or ""))
                media = self._media(payload.get("media"))
                if media["media_type"] == "movie":
                    saved = self.state.entry(profile_id, media) or {}
                    resume = float(saved.get("position_seconds", 0.0) or 0.0)
                    self.state.mark_started(profile_id, media)
                    url = getmovie(int(media["id"]), progress_seconds=resume)
                    return Response.json(
                        {"embed_url": url, "resume_seconds": resume, "kind": "movie"}
                    )

                episode_payload = payload.get("episode")
                if not isinstance(episode_payload, dict):
                    raise StateError("Choose an episode first.")
                episode = dict(episode_payload)
                season_number = _integer(episode.get("season_number"), "Season", 0, 10_000)
                episode_number = _integer(episode.get("episode_number"), "Episode", 1, 100_000)
                saved = self.state.episode_entry(
                    profile_id, media, season_number, episode_number
                ) or {}
                resume = float(saved.get("position_seconds", 0.0) or 0.0)
                self.state.mark_episode_started(profile_id, media, episode)
                url = getshow(
                    int(media["id"]),
                    season_number,
                    episode_number,
                    progress_seconds=resume,
                )
                return Response.json(
                    {
                        "embed_url": url,
                        "resume_seconds": resume,
                        "kind": "episode",
                        "season_number": season_number,
                        "episode_number": episode_number,
                    }
                )

            if path == "/api/watch/progress" and method == "POST":
                payload = self._json_body(body)
                profile_id = self._profile_id(str(payload.get("profile_id") or ""))
                media = self._media(payload.get("media"))
                position = _number(payload.get("position_seconds"), "Position")
                duration = _number(payload.get("duration_seconds"), "Duration")
                episode = payload.get("episode")
                if episode is not None:
                    if not isinstance(episode, dict):
                        raise StateError("Episode data is invalid.")
                    self.state.set_episode_position(
                        profile_id, media, episode, position, duration
                    )
                else:
                    self.state.set_position(profile_id, media, position, duration)
                return Response.json({"ok": True})

            if path == "/api/watch/action" and method == "POST":
                payload = self._json_body(body)
                profile_id = self._profile_id(str(payload.get("profile_id") or ""))
                media = self._media(payload.get("media"))
                action = str(payload.get("action") or "")
                if action in {"finished", "show_finished"}:
                    self.state.mark_finished(profile_id, media)
                elif action == "unwatched":
                    self.state.mark_unwatched(profile_id, media)
                elif action == "started":
                    self.state.mark_started(profile_id, media)
                elif action == "episode_finished":
                    episode = payload.get("episode")
                    if not isinstance(episode, dict):
                        raise StateError("Episode data is invalid.")
                    self.state.mark_episode_finished(profile_id, media, episode)
                else:
                    raise StateError("Watch-state action is invalid.")
                return Response.json({"ok": True})

            if path == "/api/admin/shutdown" and method == "POST":
                supplied = str(headers.get("X-PiStick-Shutdown-Token") or "")
                if not supplied or supplied != self.config.shutdown_token:
                    return Response.json({"error": "Shutdown token is invalid."}, 403)
                if self.shutdown_callback is not None:
                    threading.Thread(target=self.shutdown_callback, daemon=True).start()
                return Response.json({"ok": True})

            if path.startswith("/api/"):
                return Response.json({"error": "API route not found."}, 404)
            return self._static(path, query)
        except TMDBError as exc:
            return Response.json({"error": str(exc)}, exc.status)
        except SystemControlError as exc:
            return Response.json({"error": str(exc)}, 400)
        except PlaybackAPIError as exc:
            return Response.json({"error": str(exc)}, 400)
        except (StateError, ValueError, TypeError) as exc:
            return Response.json({"error": str(exc)}, 400)
        except Exception:
            LOGGER.exception("Unhandled request error for %s %s", method, path)
            return Response.json({"error": "PiStick Server hit an unexpected error."}, 500)

    def _static(self, path: str, query: dict[str, list[str]]) -> Response:
        relative = path.lstrip("/")
        if not relative or path.endswith("/"):
            relative = "index.html"
        if relative not in self.static_assets:
            if "." not in Path(relative).name:
                relative = "index.html"
            else:
                return Response.text("Not found", 404)
        content = self.static_assets.get(relative)
        if content is None:
            return Response.text("Not found", 404)
        versioned = (
            relative != "index.html"
            and self._query_value(query, "v") == self.asset_version
        )
        return Response(
            status=200,
            body=content,
            content_type=STATIC_CONTENT_TYPES[relative],
            # Fingerprinted CSS and JavaScript are safe to reuse across Cog
            # restarts. HTML and unversioned assets must always be revalidated.
            headers=IMMUTABLE_ASSET_HEADERS if versioned else NO_STORE_HEADERS,
        )


class LoopbackHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32


class PiStickRequestHandler(BaseHTTPRequestHandler):
    server_version = "PiStickServer"
    protocol_version = "HTTP/1.1"

    @property
    def application(self) -> PiStickApplication:
        return self.server.application  # type: ignore[attr-defined]

    @staticmethod
    def _host_name(host_header: str) -> str:
        value = host_header.strip().lower()
        if value.startswith("[") and "]" in value:
            return value[1 : value.index("]")]
        return value.split(":", 1)[0]

    def _trusted_request(self) -> bool:
        local_request = self._local_request()
        host = self._host_name(self.headers.get("Host", ""))
        if local_request:
            return host in LOOPBACK_NAMES or host in LAN_NAMES
        if not self.application.config.lan_enabled:
            return False
        client = _address_scope(self.client_address[0])
        if client is None or client[0] not in {"loopback", "lan"}:
            return False
        if host in LAN_NAMES:
            return True
        requested = _address_scope(host)
        return requested is not None and requested[0] in {"loopback", "lan"}

    def _local_request(self) -> bool:
        cached = getattr(self, "_pistick_local_request", None)
        if cached is not None:
            return cached
        address = _address_scope(self.client_address[0])
        local = address is not None and address[0] == "loopback"
        self._pistick_local_request = local
        return local

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            raise ValueError("Content-Length is invalid.")
        if length < 0 or length > MAX_JSON_BODY:
            raise ValueError("Request body is too large.")
        return self.rfile.read(length) if length else b""

    def _handle(self, method: str, *, send_body: bool = True) -> None:
        if not self._trusted_request():
            self._send(
                Response.json(
                    {
                        "error": "LAN access is turned off or this device is not "
                        "on the local network."
                    },
                    403,
                ),
                send_body,
            )
            return
        if method in {"POST", "PATCH", "DELETE"} and self.path.startswith("/api/"):
            if self.headers.get("X-PiStick-Request") != "1":
                self._send(Response.json({"error": "Cross-site request blocked."}, 403), send_body)
                return
        try:
            body = self._read_body() if method in {"POST", "PATCH", "DELETE"} else b""
        except ValueError as exc:
            self._send(Response.json({"error": str(exc)}, 400), send_body)
            return
        response = self.application.dispatch(
            method,
            self.path,
            self.headers,
            body,
            local_request=self._local_request(),
        )
        self._send(response, send_body)

    def _send(self, response: Response, send_body: bool = True) -> None:
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
            "img-src 'self' data: https://image.tmdb.org; connect-src 'self'; "
            "style-src 'self'; script-src 'self'; "
            "frame-src https://player.videasy.to https://player.videasy.net "
            "https://www.youtube-nocookie.com",
        )
        for name, value in response.headers.items():
            self.send_header(name, value)
        self.end_headers()
        if send_body and response.body:
            self.wfile.write(response.body)

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle("GET", send_body=False)

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(Response.json({"error": "Cross-origin requests are not allowed."}, 405))

    def log_message(self, message: str, *args: object) -> None:
        # Successful API polling and playback progress used to be written to
        # both journald and a rotating file. Keep it available in verbose mode
        # without spending SD-card I/O during normal appliance operation.
        LOGGER.debug("%s - %s", self.client_address[0], message % args)


def _configure_logging(verbose: bool = False) -> None:
    LOGGER.verbose = bool(verbose)


def _server_host(value: str) -> str:
    cleaned = str(value or "").strip().lower()
    if cleaned == "localhost":
        return "127.0.0.1"
    address = _address_scope(cleaned)
    if address is None:
        raise ValueError("PiStick Server needs a valid bind address.")
    if address[0] == "loopback":
        return address[1]
    allow_lan = os.getenv("PISTICK_ALLOW_LAN_BIND", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not allow_lan or address[0] != "unspecified":
        raise ValueError(
            "PiStick Server may bind to the LAN only in the Pi appliance service."
        )
    return address[1]


def run_server(
    data_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
    verbose: bool = False,
) -> None:
    data_dir = Path(data_dir)
    _configure_logging(verbose)
    application = PiStickApplication(data_dir)
    selected_port = int(port or application.config.port)
    application.advertised_port = selected_port
    try:
        # Request handlers do not recurse deeply. A smaller stack prevents a
        # burst of LAN connections from reserving multi-megabyte stacks in the
        # Pi Zero W's 32-bit address space.
        threading.stack_size(512 * 1024)
    except (RuntimeError, ValueError):
        pass
    server = LoopbackHTTPServer((host, selected_port), PiStickRequestHandler)
    server.application = application  # type: ignore[attr-defined]
    application.shutdown_callback = server.shutdown

    def stop(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    for signum in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if signum is not None:
            try:
                signal.signal(signum, stop)
            except (OSError, ValueError):
                pass

    url = f"http://127.0.0.1:{selected_port}/"
    LOGGER.info("PiStick Server %s listening at %s", __version__, url)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        LOGGER.info("PiStick Server stopped.")


SERVER_USAGE = """usage: server.py [--host ADDRESS] [--port PORT] [--data-dir PATH] [--verbose]

Run the PiStick Pi Zero W local application server.
"""


def _parse_server_args(
    argv: list[str],
) -> tuple[str, int | None, Path, bool] | None:
    host = "127.0.0.1"
    port = None
    data_dir = _default_data_dir()
    verbose = False
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument in {"-h", "--help"}:
            return None
        if argument == "--verbose":
            verbose = True
            index += 1
            continue
        if argument.startswith("--host="):
            value = argument.split("=", 1)[1]
        elif argument.startswith("--port="):
            value = argument.split("=", 1)[1]
        elif argument.startswith("--data-dir="):
            value = argument.split("=", 1)[1]
        elif argument in {"--host", "--port", "--data-dir"}:
            index += 1
            if index >= len(argv):
                raise ValueError(f"{argument} needs a value.")
            value = argv[index]
        else:
            raise ValueError(f"unknown argument: {argument}")

        if argument == "--host" or argument.startswith("--host="):
            host = _server_host(value)
        elif argument == "--port" or argument.startswith("--port="):
            try:
                port = int(value)
            except ValueError as exc:
                raise ValueError("--port must be a whole number.") from exc
        else:
            data_dir = Path(value).expanduser()
        index += 1
    return host, port, data_dir, verbose


def main(argv: list[str] | None = None) -> int:
    try:
        parsed = _parse_server_args(list(sys.argv[1:] if argv is None else argv))
    except ValueError as exc:
        print(f"server.py: error: {exc}", file=sys.stderr)
        return 2
    if parsed is None:
        print(SERVER_USAGE, end="")
        return 0
    host, port, data_dir, verbose = parsed
    if port is not None:
        allow_http_port = (
            port == 80
            and os.getenv("PISTICK_ALLOW_HTTP_PORT", "").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        if not (1024 <= port <= 65535 or allow_http_port):
            print(
                "server.py: error: --port must be between 1024 and 65535 "
                "(or the Pi appliance's port 80).",
                file=sys.stderr,
            )
            return 2
    try:
        run_server(
            data_dir,
            host=host,
            port=port,
            verbose=verbose,
        )
    except OSError as exc:
        LOGGER.error("Could not start PiStick Server: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
