from pathlib import Path
import tempfile
import unittest

from pistick_server.tmdb import TMDBClient


class LowMemoryHomeTests(unittest.TestCase):
    def test_home_uses_one_trending_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            client = TMDBClient(lambda: "test-token-value-long-enough", Path(temporary))
            client.low_memory = True
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


if __name__ == "__main__":
    unittest.main()
