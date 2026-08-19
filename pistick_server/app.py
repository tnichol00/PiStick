"""HTTP application for PiStick's local screen and optional home-LAN clients."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import mimetypes
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping, Optional
from urllib.parse import parse_qs, unquote, urlsplit
import webbrowser

from playback_api import PlaybackAPIError, getmovie, getshow

from . import __version__
from .config import ConfigStore
from .state import StateError, WatchStateStore
from .system_control import SystemControlError, SystemController
from .tmdb import TMDBClient, TMDBError


LOGGER = logging.getLogger("pistick-server")
MAX_JSON_BODY = 1_000_000
LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1"}
LAN_NAMES = {"pistick", "pistick.local"}


def _low_memory_enabled() -> bool:
    return os.getenv("PISTICK_LOW_MEMORY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, payload: Any, status: int = 200) -> "Response":
        return cls(
            status=status,
            body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            ),
            content_type="application/json; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @classmethod
    def text(cls, value: str, status: int = 200) -> "Response":
        return cls(
            status=status,
            body=value.encode("utf-8"),
            content_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )


def _default_data_dir() -> Path:
    configured = os.getenv("PISTICK_SERVER_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    if sys.platform == "win32" and os.getenv("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "PiStickServer" / "data"
    return Path.home() / ".local" / "share" / "pistick-server"


def _integer(value: Any, name: str, minimum: int = 1, maximum: int = 2_147_483_647) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a whole number.")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a whole number.") from exc
    if number < minimum or number > maximum:
        raise ValueError(f"{name} is outside the allowed range.")
    return number


def _number(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a number.") from exc
    if not 0 <= number <= 10_000_000:
        raise ValueError(f"{name} is outside the allowed range.")
    return number


class PiStickApplication:
    """Route same-origin browser requests to state and TMDB services."""

    def __init__(
        self,
        data_dir: Path,
        *,
        static_dir: Optional[Path] = None,
        tmdb: Optional[TMDBClient] = None,
        system_controller: Optional[SystemController] = None,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config = ConfigStore(self.data_dir / "config.json")
        self.advertised_port = self.config.port
        self.state = WatchStateStore(self.data_dir / "state.json")
        self.static_dir = Path(static_dir or Path(__file__).with_name("static")).resolve()
        self.asset_version = self._asset_version()
        self.low_memory = _low_memory_enabled()
        self.tmdb = tmdb or TMDBClient(
            lambda: self.config.tmdb_read_token,
            self.data_dir / "cache" / "tmdb",
        )
        self.system_controller = system_controller or SystemController()
        self.shutdown_callback: Optional[Callable[[], None]] = None

    def _asset_version(self) -> str:
        """Return a content version that invalidates persistent kiosk caches."""
        digest = hashlib.sha256()
        for name in ("styles.css", "app.js"):
            digest.update(name.encode("ascii"))
            try:
                digest.update((self.static_dir / name).read_bytes())
            except OSError:
                # Missing assets are handled as normal 404s by _static().
                pass
        return digest.hexdigest()[:12]

    @property
    def lan_url(self) -> str:
        suffix = "" if self.advertised_port == 80 else f":{self.advertised_port}"
        return f"http://pistick.local{suffix}"

    @staticmethod
    def _json_body(body: bytes) -> dict[str, Any]:
        if not body:
            return {}
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError) as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def _profile_id(self, candidate: Optional[str] = None) -> str:
        profile_id = str(candidate or self.state.profiles_payload().get("active_profile") or "")
        if self.state.profile(profile_id) is None:
            raise StateError("Choose a profile first.")
        return profile_id

    @staticmethod
    def _media(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise StateError("A media object is required.")
        media = dict(payload)
        WatchStateStore.media_key(media)
        return media

    @staticmethod
    def _query_value(query: Mapping[str, list[str]], name: str, default: str = "") -> str:
        values = query.get(name)
        return values[0] if values else default

    def _decorate_home(self, profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        continue_items = self.state.continue_watching(profile_id)
        item_limit = 12 if self.low_memory else None
        if item_limit is not None:
            continue_items = continue_items[:item_limit]
        rows = []
        if continue_items:
            rows.append({"title": "Continue Watching", "items": continue_items})
        for row in payload.get("rows", []):
            source_items = row.get("items", [])
            if item_limit is not None:
                source_items = source_items[:item_limit]
            items = [
                self.state.decorate(profile_id, media)
                for media in source_items
                if isinstance(media, dict)
            ]
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
        headers: Mapping[str, str],
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
                return Response.json({"ok": True, "version": __version__})

            if path == "/api/status" and method == "GET":
                return Response.json(
                    {
                        "name": "PiStick Server",
                        "version": __version__,
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

            match = re.fullmatch(r"/api/profiles/([A-Za-z0-9_-]+)", path)
            if match and method == "PATCH":
                payload = self._json_body(body)
                profile = self.state.rename_profile(match.group(1), str(payload.get("name") or ""))
                return Response.json({"profile": profile})
            if match and method == "DELETE":
                self.state.delete_profile(match.group(1))
                return Response.json({"ok": True})

            match = re.fullmatch(r"/api/profiles/([A-Za-z0-9_-]+)/activate", path)
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
                actions = {
                    ("/api/system/wifi/scan", "POST"): "wifi-scan",
                    ("/api/system/wifi/connect", "POST"): "wifi-connect",
                    ("/api/system/bluetooth/scan", "POST"): "bluetooth-scan",
                    ("/api/system/bluetooth/pair", "POST"): "bluetooth-pair",
                }
                action = actions.get((path, method))
                if action:
                    return Response.json(
                        self.system_controller.run(action, self._json_body(body))
                    )

            if path == "/api/home" and method == "GET":
                profile_id = self._profile_id(self._query_value(query, "profile_id"))
                return Response.json(self._decorate_home(profile_id, self.tmdb.home()))

            match = re.fullmatch(r"/api/discover/(movie|tv)", path)
            if match and method == "GET":
                page = _integer(self._query_value(query, "page", "1"), "Page", 1, 500)
                profile_id = self._profile_id(self._query_value(query, "profile_id"))
                payload = self.tmdb.discover(match.group(1), page)
                if self.low_memory:
                    payload["items"] = payload["items"][:12]
                payload["items"] = [
                    self.state.decorate(profile_id, item) for item in payload["items"]
                ]
                return Response.json(payload)

            if path == "/api/search" and method == "GET":
                profile_id = self._profile_id(self._query_value(query, "profile_id"))
                page = _integer(self._query_value(query, "page", "1"), "Page", 1, 500)
                payload = self.tmdb.search(self._query_value(query, "q"), page)
                if self.low_memory:
                    payload["items"] = payload["items"][:12]
                payload["items"] = [
                    self.state.decorate(profile_id, item) for item in payload["items"]
                ]
                return Response.json(payload)

            match = re.fullmatch(r"/api/media/(movie|tv)/(\d+)", path)
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

            match = re.fullmatch(r"/api/tv/(\d+)/season/(\d+)", path)
            if match and method == "GET":
                profile_id = self._profile_id(self._query_value(query, "profile_id"))
                show_id = _integer(match.group(1), "TMDB ID")
                season_number = _integer(match.group(2), "Season", 0, 10_000)
                show = self.tmdb.details("tv", show_id)
                season = self.tmdb.season(show_id, season_number)
                for episode in season.get("episodes", []):
                    watch = self.state.episode_entry(
                        profile_id,
                        show,
                        int(episode["season_number"]),
                        int(episode["episode_number"]),
                    )
                    if watch:
                        episode["watch"] = {
                            key: watch.get(key)
                            for key in (
                                "status",
                                "progress",
                                "position_seconds",
                                "duration_seconds",
                            )
                            if watch.get(key) is not None
                        }
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
            return self._static(path)
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

    def _static(self, path: str) -> Response:
        relative = path.lstrip("/")
        if not relative or path.endswith("/"):
            relative = "index.html"
        candidate = (self.static_dir / relative).resolve()
        try:
            candidate.relative_to(self.static_dir)
        except ValueError:
            return Response.text("Not found", 404)
        if not candidate.is_file():
            if "." not in Path(relative).name:
                candidate = self.static_dir / "index.html"
            else:
                return Response.text("Not found", 404)
        try:
            content = candidate.read_bytes()
        except OSError:
            return Response.text("Not found", 404)
        if candidate.name == "index.html":
            content = content.replace(
                b"__PISTICK_ASSET_VERSION__", self.asset_version.encode("ascii")
            )
        content_type, _ = mimetypes.guess_type(candidate.name)
        return Response(
            status=200,
            body=content,
            content_type=(content_type or "application/octet-stream") + (
                "; charset=utf-8"
                if (content_type or "").startswith(("text/", "application/javascript"))
                else ""
            ),
            # PiStick's persistent kiosk profile previously cached CSS for an
            # hour. Content-versioned URLs bypass that old cache entry, while
            # these headers prevent another stale UI after future updates.
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
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
        try:
            client = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        if client.is_global or client.is_unspecified or client.is_multicast:
            return False
        if host in LAN_NAMES:
            return True
        try:
            requested = ipaddress.ip_address(host)
        except ValueError:
            return False
        return not (
            requested.is_global or requested.is_unspecified or requested.is_multicast
        )

    def _local_request(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

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
                    {"error": "LAN access is turned off or this device is not on the local network."},
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

    def log_message(self, message: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.client_address[0], message % args)


def _configure_logging(data_dir: Path, verbose: bool = False) -> None:
    LOGGER.setLevel(logging.DEBUG if verbose else logging.INFO)
    if LOGGER.handlers:
        return
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    LOGGER.addHandler(stream)
    log_dir = data_dir.parent / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "server.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        LOGGER.addHandler(file_handler)
    except OSError:
        LOGGER.warning("Could not create the server log file.")


def _server_host(value: str) -> str:
    cleaned = str(value or "").strip().lower()
    if cleaned == "localhost":
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(cleaned)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("PiStick Server needs a valid bind address.") from exc
    if address.is_loopback:
        return str(address)
    allow_lan = os.getenv("PISTICK_ALLOW_LAN_BIND", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not allow_lan or not address.is_unspecified:
        raise argparse.ArgumentTypeError(
            "PiStick Server may bind to the LAN only in the Pi appliance service."
        )
    return str(address)


def run_server(
    data_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: Optional[int] = None,
    open_browser: bool = False,
    verbose: bool = False,
) -> None:
    data_dir = Path(data_dir)
    _configure_logging(data_dir, verbose)
    application = PiStickApplication(data_dir)
    selected_port = int(port or application.config.port)
    application.advertised_port = selected_port
    server = LoopbackHTTPServer((host, selected_port), PiStickRequestHandler)
    server.application = application  # type: ignore[attr-defined]
    application.shutdown_callback = server.shutdown

    runtime_path = data_dir / "runtime.json"
    runtime_payload = {
        "pid": os.getpid(),
        "host": host,
        "port": selected_port,
        "started_at": time.time(),
    }
    runtime_path.write_text(json.dumps(runtime_payload, indent=2) + "\n", encoding="utf-8")

    def stop(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    for signum in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if signum is not None:
            try:
                signal.signal(signum, stop)
            except (OSError, ValueError):
                pass

    url = f"http://127.0.0.1:{selected_port}/"
    LOGGER.info("PiStick Server %s listening at %s", __version__, url)
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        try:
            current = json.loads(runtime_path.read_text(encoding="utf-8"))
            if int(current.get("pid", -1)) == os.getpid():
                runtime_path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            pass
        LOGGER.info("PiStick Server stopped.")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run PiStick's local web server.")
    parser.add_argument("--host", type=_server_host, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--data-dir", type=Path, default=_default_data_dir())
    parser.add_argument("--open", action="store_true", dest="open_browser")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.port is not None:
        allow_http_port = (
            args.port == 80
            and os.getenv("PISTICK_ALLOW_HTTP_PORT", "").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        if not (1024 <= args.port <= 65535 or allow_http_port):
            parser.error("--port must be between 1024 and 65535 (or the Pi appliance's port 80).")
    try:
        run_server(
            args.data_dir,
            host=args.host,
            port=args.port,
            open_browser=args.open_browser,
            verbose=args.verbose,
        )
    except OSError as exc:
        LOGGER.error("Could not start PiStick Server: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
