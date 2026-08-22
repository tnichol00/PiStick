import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pistick_server.tmdb import (
    DISK_CACHE_LIMIT,
    TMDB_API_TIMEOUT,
    TMDBClient,
    TMDBError,
    _connect_ipv4,
)


class LowMemoryHomeTests(unittest.TestCase):
    def test_home_uses_one_trending_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            client = TMDBClient(lambda: "test-token-value-long-enough", Path(temporary))
            calls = []

            def fake_request(endpoint, params=None, **_kwargs):
                calls.append((endpoint, params))
                return {
                    "results": [
                        {
                            "id": 1,
                            "media_type": "movie",
                            "title": "Movie",
                            "backdrop_path": "/movie.jpg",
                        },
                        {
                            "id": 2,
                            "media_type": "tv",
                            "name": "Show",
                            "backdrop_path": "/show.jpg",
                        },
                    ]
                }

            client._request = fake_request
            payload = client.home()

        self.assertEqual(calls, [("/trending/all/week", {"page": 1})])
        self.assertEqual([row["title"] for row in payload["rows"]], [
            "Trending Now",
            "Trending Movies",
            "Trending TV Shows",
        ])
        self.assertEqual([item["id"] for item in payload["rows"][1]["items"]], [1])
        self.assertEqual([item["id"] for item in payload["rows"][2]["items"]], [2])
        self.assertEqual(payload["hero"]["id"], 1)

    def test_list_payload_discards_fields_the_ui_never_uses(self) -> None:
        item = TMDBClient.normalize_item(
            {
                "id": 550,
                "media_type": "movie",
                "title": "Fight Club",
                "release_date": "1999-10-15",
                "poster_path": "/poster.jpg",
                "genre_ids": [18],
                "popularity": 123.4,
                "original_language": "en",
            }
        )
        self.assertEqual(item["year"], "1999")
        self.assertNotIn("genre_ids", item)
        self.assertNotIn("popularity", item)
        self.assertNotIn("original_language", item)

    def test_cached_payload_is_compacted_before_it_reaches_disk(self) -> None:
        compact = TMDBClient._compact_payload(
            "/trending/all/week",
            {
                "page": 1,
                "total_pages": 100,
                "total_results": 2000,
                "results": [
                    {
                        "id": 550,
                        "media_type": "movie",
                        "title": "Fight Club",
                        "poster_path": "/poster.jpg",
                        "genre_ids": [18],
                        "popularity": 123.4,
                    }
                ],
            },
        )
        self.assertEqual(compact["results"][0]["title"], "Fight Club")
        self.assertNotIn("total_results", compact)
        self.assertNotIn("genre_ids", compact["results"][0])
        self.assertNotIn("popularity", compact["results"][0])

    def test_details_and_seasons_are_compacted_without_changing_ui_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            client = TMDBClient(lambda: "test-token-value-long-enough", Path(temporary))
            calls = []

            def fake_request(endpoint, params=None, **_kwargs):
                calls.append((endpoint, params))
                if "/season/" in endpoint:
                    return {
                        "season_number": 1,
                        "name": "Season 1",
                        "overview": "unused season overview",
                        "episodes": [
                            {
                                "id": 10,
                                "name": "Pilot",
                                "overview": "Episode overview",
                                "season_number": 1,
                                "episode_number": 1,
                                "runtime": 55,
                                "vote_average": 9.0,
                                "crew": [{"id": 1}],
                            }
                        ],
                    }
                if endpoint.endswith("/videos"):
                    return {
                        "results": [
                            {
                                "key": "small-trailer",
                                "site": "YouTube",
                                "type": "Trailer",
                                "official": True,
                                "size": 1080,
                            }
                        ]
                    }
                return {
                    "id": 1399,
                    "name": "Game of Thrones",
                    "first_air_date": "2011-04-17",
                    "poster_path": "/poster.jpg",
                    "number_of_seasons": 1,
                    "credits": {"cast": [{"id": 1}]},
                    "popularity": 999,
                    "seasons": [
                        {
                            "id": 1,
                            "name": "Season 1",
                            "season_number": 1,
                            "episode_count": 10,
                            "poster_path": "/season.jpg",
                            "overview": "unused",
                        }
                    ],
                    "videos": {
                        "results": [
                            {
                                "key": "abc",
                                "site": "YouTube",
                                "type": "Trailer",
                                "official": True,
                                "published_at": "unused",
                            }
                        ]
                    },
                }

            client._request = fake_request
            details = client.details("tv", 1399)
            season = client.season(1399, 1)
            videos = client.videos("movie", 550)

        self.assertEqual(calls[0], ("/tv/1399", {"append_to_response": "videos"}))
        self.assertNotIn("credits", details)
        self.assertNotIn("popularity", details)
        self.assertNotIn("overview", details["seasons"][0])
        self.assertNotIn("published_at", details["videos"]["results"][0])
        self.assertNotIn("crew", season["episodes"][0])
        self.assertNotIn("vote_average", season["episodes"][0])
        self.assertEqual(calls[2], ("/movie/550/videos", None))
        self.assertEqual(videos["results"][0]["key"], "small-trailer")
        self.assertNotIn("size", videos["results"][0])

    def test_gzip_response_is_requested_and_decoded(self) -> None:
        class FakeResponse:
            status = 200

            @staticmethod
            def getheader(name):
                return "gzip" if name == "Content-Encoding" else None

            @staticmethod
            def read():
                return gzip.compress(json.dumps({"ok": True}).encode("utf-8"))

        class FakeConnection:
            def __init__(self):
                self.request_args = None
                self.closed = False

            def request(self, method, target, headers):
                self.request_args = (method, target, headers)

            @staticmethod
            def getresponse():
                return FakeResponse()

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temporary:
            client = TMDBClient(lambda: "token", Path(temporary))
            connection = FakeConnection()
            with patch(
                "pistick_server.tmdb.HTTPSConnection", return_value=connection
            ) as opener:
                payload = client._request("/configuration", cache=False)

        opener.assert_called_once_with("api.themoviedb.org", timeout=TMDB_API_TIMEOUT)
        method, target, headers = connection.request_args
        self.assertEqual(method, "GET")
        self.assertTrue(target.startswith("/3/configuration?"))
        headers = {key.lower(): value for key, value in headers.items()}
        self.assertEqual(headers["accept-encoding"], "gzip")
        self.assertEqual(headers["connection"], "keep-alive")
        self.assertIs(connection._create_connection, _connect_ipv4)
        self.assertFalse(connection.closed)
        self.assertEqual(payload, {"ok": True})

    def test_successive_requests_reuse_one_tls_connection(self) -> None:
        class FakeResponse:
            status = 200

            @staticmethod
            def getheader(_name):
                return None

            @staticmethod
            def read():
                return b'{"ok":true}'

        class FakeConnection:
            def __init__(self):
                self.requests = []
                self.closed = False

            def request(self, method, target, headers):
                self.requests.append((method, target, headers))

            @staticmethod
            def getresponse():
                return FakeResponse()

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temporary:
            client = TMDBClient(lambda: "token", Path(temporary))
            connection = FakeConnection()
            with patch(
                "pistick_server.tmdb.HTTPSConnection", return_value=connection
            ) as opener:
                client._request("/configuration", cache=False)
                client._request("/configuration", cache=False)

        opener.assert_called_once_with(
            "api.themoviedb.org", timeout=TMDB_API_TIMEOUT
        )
        self.assertEqual(len(connection.requests), 2)
        self.assertFalse(connection.closed)

    def test_dead_keep_alive_socket_reconnects_once(self) -> None:
        class FakeResponse:
            status = 200

            @staticmethod
            def getheader(_name):
                return None

            @staticmethod
            def read():
                return b'{"ok":true}'

        class FirstConnection:
            def __init__(self):
                self.requests = 0
                self.closed = False

            def request(self, *_args, **_kwargs):
                self.requests += 1
                if self.requests > 1:
                    raise OSError("server closed idle socket")

            @staticmethod
            def getresponse():
                return FakeResponse()

            def close(self):
                self.closed = True

        class ReplacementConnection:
            def __init__(self):
                self.requests = 0

            def request(self, *_args, **_kwargs):
                self.requests += 1

            @staticmethod
            def getresponse():
                return FakeResponse()

            @staticmethod
            def close():
                return None

        with tempfile.TemporaryDirectory() as temporary:
            client = TMDBClient(lambda: "token", Path(temporary))
            first = FirstConnection()
            replacement = ReplacementConnection()
            with patch(
                "pistick_server.tmdb.HTTPSConnection",
                side_effect=[first, replacement],
            ) as opener:
                client._request("/configuration", cache=False)
                client._request("/configuration", cache=False)

        self.assertEqual(opener.call_count, 2)
        self.assertTrue(first.closed)
        self.assertEqual(replacement.requests, 1)

    def test_keep_alive_timeout_is_not_retried(self) -> None:
        class FakeResponse:
            status = 200

            @staticmethod
            def getheader(_name):
                return None

            @staticmethod
            def read():
                return b'{"ok":true}'

        class TimeoutConnection:
            def __init__(self):
                self.requests = 0
                self.closed = False

            def request(self, *_args, **_kwargs):
                self.requests += 1
                if self.requests > 1:
                    raise TimeoutError("offline")

            @staticmethod
            def getresponse():
                return FakeResponse()

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temporary:
            client = TMDBClient(lambda: "token", Path(temporary))
            connection = TimeoutConnection()
            with patch(
                "pistick_server.tmdb.HTTPSConnection", return_value=connection
            ) as opener:
                client._request("/configuration", cache=False)
                with self.assertRaises(TMDBError):
                    client._request("/configuration", cache=False)

        opener.assert_called_once()
        self.assertTrue(connection.closed)

    def test_video_endpoint_keeps_only_ui_fields_and_uses_long_cache(self) -> None:
        compact = TMDBClient._compact_payload(
            "/movie/550/videos",
            {
                "id": 550,
                "results": [
                    {
                        "key": "trailer123",
                        "site": "YouTube",
                        "type": "Trailer",
                        "official": True,
                        "published_at": "unused",
                        "size": 1080,
                    }
                ],
            },
        )
        self.assertEqual(compact["results"][0]["key"], "trailer123")
        self.assertNotIn("published_at", compact["results"][0])
        self.assertNotIn("size", compact["results"][0])
        self.assertEqual(
            TMDBClient._cache_ttl("/movie/550/videos"),
            7 * 24 * 60 * 60,
        )

    def test_http_authentication_error_is_reported_and_connection_closes(self) -> None:
        class RejectedResponse:
            status = 401

            @staticmethod
            def read():
                return b'{}'

            @staticmethod
            def getheader(_name):
                return None

        class FakeConnection:
            closed = False

            @staticmethod
            def request(*_args, **_kwargs):
                return None

            @staticmethod
            def getresponse():
                return RejectedResponse()

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temporary:
            client = TMDBClient(lambda: "bad-token", Path(temporary))
            connection = FakeConnection()
            with patch(
                "pistick_server.tmdb.HTTPSConnection", return_value=connection
            ), self.assertRaises(TMDBError) as captured:
                client._request("/configuration", cache=False)

        self.assertEqual(captured.exception.status, 401)
        self.assertTrue(connection.closed)

    def test_network_failure_uses_compacted_stale_cache(self) -> None:
        class OfflineConnection:
            closed = False

            @staticmethod
            def request(*_args, **_kwargs):
                raise OSError("offline")

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temporary:
            cache_dir = Path(temporary)
            client = TMDBClient(lambda: "token", cache_dir)
            endpoint = "/trending/all/week"
            key = client._key(endpoint, {"language": "en-CA", "page": 1})
            cached = client._cache_path(key)
            cached.write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "id": 1,
                                "media_type": "movie",
                                "title": "Offline Movie",
                                "popularity": 999,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            os.utime(cached, (1, 1))
            connection = OfflineConnection()
            with patch(
                "pistick_server.tmdb.HTTPSConnection", return_value=connection
            ):
                payload = client._request(endpoint, {"page": 1})

        self.assertEqual(payload["results"][0]["title"], "Offline Movie")
        self.assertNotIn("popularity", payload["results"][0])
        self.assertTrue(connection.closed)

    def test_disk_cache_is_bounded_and_stale_temp_files_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_dir = Path(temporary)
            for index in range(DISK_CACHE_LIMIT + 17):
                path = cache_dir / f"{index:064x}.json"
                path.write_text("{}", encoding="utf-8")
                os.utime(path, (index + 1, index + 1))
            stale_temp = cache_dir / ".stale.json.123.tmp"
            stale_temp.write_text("partial", encoding="utf-8")

            client = TMDBClient(lambda: "token", cache_dir)
            self.assertIsNotNone(client)
            remaining = list(cache_dir.glob("*.json"))

        self.assertEqual(len(remaining), DISK_CACHE_LIMIT)
        self.assertFalse(stale_temp.exists())


if __name__ == "__main__":
    unittest.main()
