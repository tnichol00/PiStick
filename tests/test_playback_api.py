import inspect
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from playback_api import (
    PlaybackAPIError,
    VIDEASY_PLAYER_BASE_URL,
    getmovie,
    getshow,
)


class PlaybackAPITests(unittest.TestCase):
    def test_videasy_uses_the_final_https_player_origin(self) -> None:
        self.assertEqual(VIDEASY_PLAYER_BASE_URL, "https://player.videasy.to")

    def test_movie_url(self) -> None:
        self.assertEqual(
            getmovie(550),
            "https://player.videasy.to/movie/550",
        )

    def test_episode_url(self) -> None:
        self.assertEqual(
            getshow(1399, 1, 3),
            "https://player.videasy.to/tv/1399/1/3",
        )

    def test_documented_progress_parameter_restores_movies_and_episodes(self) -> None:
        self.assertEqual(
            getmovie(550, progress_seconds=123.9),
            "https://player.videasy.to/movie/550?progress=123",
        )
        self.assertEqual(
            getshow(1399, 2, 4, progress_seconds=456),
            "https://player.videasy.to/tv/1399/2/4?progress=456",
        )

    def test_public_helpers_match_the_videasy_signatures(self) -> None:
        self.assertEqual(
            list(inspect.signature(getmovie).parameters),
            ["tmdb_number", "progress_seconds"],
        )
        self.assertEqual(
            list(inspect.signature(getshow).parameters),
            ["tmdb_number", "season_number", "episode_number", "progress_seconds"],
        )

    def test_specials_season_is_supported(self) -> None:
        self.assertEqual(
            getshow(1399, 0, 1),
            "https://player.videasy.to/tv/1399/0/1",
        )

    def test_invalid_identifiers_and_resume_values_are_rejected(self) -> None:
        for call in (
            lambda: getmovie(0),
            lambda: getmovie(True),
            lambda: getmovie(5.5),
            lambda: getshow(1399, -1, 1),
            lambda: getshow(1399, 1, 0),
            lambda: getmovie(550, progress_seconds=-1),
            lambda: getmovie(550, progress_seconds=float("inf")),
            lambda: getmovie(550, progress_seconds=True),
        ):
            with self.subTest(call=call):
                with self.assertRaises(PlaybackAPIError):
                    call()

    def test_playback_no_longer_depends_on_private_provider_config(self) -> None:
        missing = Path(tempfile.gettempdir()) / "pistick-missing-provider-config.json"
        missing.unlink(missing_ok=True)
        with patch.dict(os.environ, {"PISTICK_CONFIG_PATH": str(missing)}):
            self.assertEqual(
                getmovie(550),
                "https://player.videasy.to/movie/550",
            )


if __name__ == "__main__":
    unittest.main()
