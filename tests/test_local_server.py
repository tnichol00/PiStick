import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pistick_server.app import (
    LoopbackHTTPServer,
    PiStickApplication,
    PiStickRequestHandler,
    _server_host,
    main,
)
from pistick_server.config import ConfigStore


class FakeTMDB:
    def __init__(self) -> None:
        self.validated = None

    @staticmethod
    def movie():
        return {
            "id": 550,
            "media_type": "movie",
            "title": "Fight Club",
            "year": "1999",
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "overview": "A test movie.",
            "videos": {"results": []},
        }

    @staticmethod
    def show():
        return {
            "id": 1399,
            "media_type": "tv",
            "name": "Game of Thrones",
            "year": "2011",
            "seasons": [{"season_number": 1, "episode_count": 2, "name": "Season 1"}],
            "videos": {"results": []},
        }

    def validate_token(self, token):
        self.validated = token

    def home(self):
        movie = self.movie()
        return {"hero": movie, "rows": [{"title": "Popular Movies", "items": [movie]}]}

    def discover(self, media_type, page=1):
        item = self.movie() if media_type == "movie" else self.show()
        return {"page": page, "total_pages": 1, "items": [item]}

    def search(self, query, page=1):
        return {"page": page, "total_pages": 1, "items": [self.movie()] if query else []}

    def details(self, media_type, media_id):
        return self.movie() if media_type == "movie" else self.show()

    def season(self, show_id, season_number):
        return {
            "season_number": season_number,
            "episodes": [
                {
                    "id": 1,
                    "season_number": season_number,
                    "episode_number": 1,
                    "name": "Winter Is Coming",
                }
            ],
        }


class FakeSystemController:
    available = True

    def __init__(self) -> None:
        self.calls = []

    def run(self, action, payload=None):
        self.calls.append((action, payload or {}))
        if action == "status":
            return {
                "wifi": {
                    "available": True,
                    "connected": True,
                    "ssid": "Home WiFi",
                    "signal": 88,
                    "ipv4": "192.168.1.20",
                },
                "bluetooth": {"powered": True, "devices": []},
                "wired_controllers": [],
            }
        if action == "wifi-scan":
            return {"networks": [{"ssid": "Home WiFi", "signal": 88}]}
        return {"ok": True}


class ApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name) / "data"
        self.fake_tmdb = FakeTMDB()
        self.system = FakeSystemController()
        self.app = PiStickApplication(
            self.data_dir, tmdb=self.fake_tmdb, system_controller=self.system
        )
        self.profile_id = self.app.state.profiles()[0]["id"]
        self.app.state.activate_profile(self.profile_id)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def dispatch_json(self, method, path, payload=None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        response = self.app.dispatch(method, path, {"X-PiStick-Request": "1"}, body)
        return response, json.loads(response.body.decode("utf-8"))

    def test_status_does_not_expose_private_tokens(self) -> None:
        self.app.config.data["tmdb_read_token"] = "this-is-a-private-token-that-is-long-enough"
        self.app.config.save = lambda: None
        response, payload = self.dispatch_json("GET", "/api/status")
        self.assertEqual(response.status, 200)
        self.assertNotIn("token", json.dumps(payload).lower().replace("tmdb_configured", ""))
        self.assertNotIn("this-is-a-private-token", response.body.decode("utf-8"))

    def test_private_config_file_is_owner_only(self) -> None:
        if os.name == "nt":
            self.skipTest("Windows does not expose POSIX file modes")
        mode = (self.data_dir / "config.json").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_string_false_does_not_enable_lan_access(self) -> None:
        path = Path(self.temporary.name) / "legacy-config.json"
        path.write_text('{"lan_enabled":"false"}', encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True):
            store = ConfigStore(path)
        self.assertFalse(store.lan_enabled)

    def test_home_includes_server_side_continue_watching(self) -> None:
        movie = self.fake_tmdb.movie()
        self.app.state.set_position(self.profile_id, movie, 60, 600)
        response, payload = self.dispatch_json(
            "GET", "/api/home?profile_id=" + self.profile_id
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["rows"][0]["title"], "Continue Watching")
        self.assertEqual(payload["rows"][0]["items"][0]["watch"]["position_seconds"], 60.0)

    def test_play_and_progress_endpoints_use_documented_videasy_urls(self) -> None:
        movie = self.fake_tmdb.movie()
        response, payload = self.dispatch_json(
            "POST",
            "/api/play",
            {"profile_id": self.profile_id, "media": movie},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["embed_url"], "https://player.videasy.to/movie/550")

        response, payload = self.dispatch_json(
            "POST",
            "/api/watch/progress",
            {
                "profile_id": self.profile_id,
                "media": movie,
                "position_seconds": 125,
                "duration_seconds": 500,
            },
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(self.app.state.entry(self.profile_id, movie)["position_seconds"], 125.0)

        response, payload = self.dispatch_json(
            "POST",
            "/api/play",
            {
                "profile_id": self.profile_id,
                "media": self.fake_tmdb.show(),
                "episode": {"season_number": 1, "episode_number": 1, "name": "Pilot"},
            },
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["embed_url"], "https://player.videasy.to/tv/1399/1/1")

    def test_tmdb_token_cannot_be_changed_through_http(self) -> None:
        token = "a-valid-looking-read-token-value"
        response, payload = self.dispatch_json(
            "POST", "/api/settings/tmdb", {"token": token}
        )
        self.assertEqual(response.status, 403)
        self.assertIn("SSH", payload["error"])
        self.assertIsNone(self.fake_tmdb.validated)
        self.assertFalse(self.app.config.tmdb_read_token)

    def test_system_controls_are_local_only_and_lan_toggle_persists(self) -> None:
        response = self.app.dispatch(
            "GET", "/api/system/status", {}, local_request=False
        )
        self.assertEqual(response.status, 403)

        response, payload = self.dispatch_json("GET", "/api/system/status")
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["wifi"]["ssid"], "Home WiFi")
        self.assertTrue(payload["lan_url"].startswith("http://pistick.local:"))

        response, payload = self.dispatch_json(
            "POST", "/api/system/lan", {"enabled": True}
        )
        self.assertEqual(response.status, 200)
        self.assertTrue(payload["lan_enabled"])
        self.assertTrue(self.app.config.lan_enabled)

    def test_status_marks_only_local_hdmi_requests_as_system_control(self) -> None:
        local = self.app.dispatch("GET", "/api/status", {}, local_request=True)
        remote = self.app.dispatch("GET", "/api/status", {}, local_request=False)
        self.assertTrue(json.loads(local.body)["local_control"])
        self.assertFalse(json.loads(remote.body)["local_control"])

    def test_pi_http_port_is_advertised_without_a_port_suffix(self) -> None:
        self.app.advertised_port = 80
        response = self.app.dispatch("GET", "/api/status", {})
        payload = json.loads(response.body)
        self.assertEqual(payload["port"], 80)
        self.assertEqual(payload["lan_url"], "http://pistick.local")

    def test_lan_gate_accepts_only_private_clients_and_expected_hosts(self) -> None:
        handler = object.__new__(PiStickRequestHandler)
        handler.server = SimpleNamespace(application=self.app)
        handler.headers = {"Host": "pistick.local"}
        handler.client_address = ("192.168.1.55", 50000)
        self.assertFalse(handler._trusted_request())

        self.app.config.set_lan_enabled(True)
        self.assertTrue(handler._trusted_request())

        handler.headers = {"Host": "unexpected.example"}
        self.assertFalse(handler._trusted_request())
        handler.headers = {"Host": "pistick.local"}
        handler.client_address = ("8.8.8.8", 50000)
        self.assertFalse(handler._trusted_request())

    def test_lan_bind_requires_explicit_pi_service_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_server_host("127.0.0.1"), "127.0.0.1")
            with self.assertRaises(argparse.ArgumentTypeError):
                _server_host("0.0.0.0")
        with patch.dict(os.environ, {"PISTICK_ALLOW_LAN_BIND": "1"}):
            self.assertEqual(_server_host("0.0.0.0"), "0.0.0.0")

    def test_port_80_requires_explicit_pi_service_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "pistick_server.app.run_server"
        ) as runner:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main(["--port", "80"])
            runner.assert_not_called()
        with patch.dict(os.environ, {"PISTICK_ALLOW_HTTP_PORT": "1"}), patch(
            "pistick_server.app.run_server"
        ) as runner:
            self.assertEqual(main(["--port", "80"]), 0)
            runner.assert_called_once()

    def test_static_ui_is_served(self) -> None:
        response = self.app.dispatch("GET", "/", {})
        self.assertEqual(response.status, 200)
        self.assertIn(b"PiStick", response.body)
        script = self.app.dispatch("GET", "/app.js", {})
        self.assertEqual(script.status, 200)
        self.assertIn(b"/api/watch/progress", script.body)
        self.assertIn(b"openSearchKeyboard", script.body)
        self.assertIn(b"/api/system/wifi/connect", script.body)
        self.assertNotIn(b"/api/settings/tmdb", script.body)

    def test_pi_low_memory_mode_limits_home_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"PISTICK_LOW_MEMORY": "1"}
        ):
            app = PiStickApplication(Path(directory) / "data", tmdb=FakeTMDB())
            profile_id = app.state.profiles()[0]["id"]
            app.state.activate_profile(profile_id)
            items = []
            for media_id in range(1, 21):
                item = FakeTMDB.movie()
                item["id"] = media_id
                items.append(item)
            result = app._decorate_home(
                profile_id,
                {"hero": items[0], "rows": [{"title": "Many", "items": items}]},
            )
            self.assertTrue(app.low_memory)
            self.assertEqual(len(result["rows"][0]["items"]), 12)
            response = app.dispatch("GET", "/api/status", {})
            payload = json.loads(response.body.decode("utf-8"))
            self.assertTrue(payload["low_memory"])


class LoopbackHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        app = PiStickApplication(Path(self.temporary.name) / "data", tmdb=FakeTMDB())
        self.server = LoopbackHTTPServer(("127.0.0.1", 0), PiStickRequestHandler)
        self.server.application = app
        app.shutdown_callback = self.server.shutdown
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_address[1])

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def test_real_loopback_health_and_security_headers(self) -> None:
        with urlopen(self.base + "/health", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
            self.assertTrue(payload["ok"])
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_write_without_pistick_header_is_blocked(self) -> None:
        request = Request(
            self.base + "/api/profiles",
            data=json.dumps({"name": "Blocked"}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as captured:
            urlopen(request, timeout=3)
        self.assertEqual(captured.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
