import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pistick_server.app import (
    LoopbackHTTPServer,
    PiStickApplication,
    PiStickRequestHandler,
)


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


class ApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name) / "data"
        self.fake_tmdb = FakeTMDB()
        self.app = PiStickApplication(self.data_dir, tmdb=self.fake_tmdb)
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

    def test_settings_validate_before_saving(self) -> None:
        token = "a-valid-looking-read-token-value"
        response, payload = self.dispatch_json(
            "POST", "/api/settings/tmdb", {"token": token}
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(self.fake_tmdb.validated, token)
        self.assertEqual(self.app.config.tmdb_read_token, token)

    def test_static_ui_is_served(self) -> None:
        response = self.app.dispatch("GET", "/", {})
        self.assertEqual(response.status, 200)
        self.assertIn(b"PiStick", response.body)
        script = self.app.dispatch("GET", "/app.js", {})
        self.assertEqual(script.status, 200)
        self.assertIn(b"/api/watch/progress", script.body)


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
