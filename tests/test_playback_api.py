import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from playback_api import PlaybackAPIError, getmovie, getshow


class PlaybackAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temporary_directory.name) / "config.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "tmdb_read_token": "test-token",
                    "playback_base_url": "https://playback.example/base/",
                }
            ),
            encoding="utf-8",
        )
        self.config_environment = patch.dict(
            os.environ,
            {"PISTICK_CONFIG_PATH": str(self.config_path)},
        )
        self.config_environment.start()

    def tearDown(self) -> None:
        self.config_environment.stop()
        self.temporary_directory.cleanup()

    def test_movie_embed_url(self) -> None:
        self.assertEqual(
            getmovie(550),
            "https://playback.example/base/embed/movie/550",
        )

    def test_episode_embed_url(self) -> None:
        self.assertEqual(
            getshow(1399, 1, 3),
            "https://playback.example/base/embed/tv/1399/1/3",
        )

    def test_specials_season_is_supported(self) -> None:
        self.assertTrue(getshow(1399, 0, 1).endswith("/1399/0/1"))

    def test_invalid_identifiers_are_rejected(self) -> None:
        for call in (
            lambda: getmovie(0),
            lambda: getmovie(True),
            lambda: getmovie(5.5),
            lambda: getshow(1399, -1, 1),
            lambda: getshow(1399, 1, 0),
        ):
            with self.subTest(call=call):
                with self.assertRaises(PlaybackAPIError):
                    call()

    def test_missing_private_config_is_reported(self) -> None:
        missing_path = Path(self.temporary_directory.name) / "missing.json"
        with patch.dict(os.environ, {"PISTICK_CONFIG_PATH": str(missing_path)}):
            with self.assertRaisesRegex(PlaybackAPIError, "playback_base_url"):
                getmovie(550)

    def test_insecure_or_credentialed_url_is_rejected(self) -> None:
        for playback_base_url in (
            "http://playback.example",
            "https://user:password@playback.example",
            "https://playback.example?source=private",
        ):
            with self.subTest(playback_base_url=playback_base_url):
                self.config_path.write_text(
                    json.dumps({"playback_base_url": playback_base_url}),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(PlaybackAPIError, "HTTPS base URL"):
                    getmovie(550)


if __name__ == "__main__":
    unittest.main()
