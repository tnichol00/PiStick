"""Build Videasy player URLs from TMDB identifiers.

Videasy documents ``player.videasy.net`` as its embed host. That address
currently redirects to ``player.videasy.to``; PiStick uses the final HTTPS
origin directly so its anti-popup navigation lock does not reject the redirect.
"""

import math


VIDEASY_PLAYER_BASE_URL = "https://player.videasy.to"


class PlaybackAPIError(ValueError):
    """Raised when an invalid movie, episode, or resume value is supplied."""


def _integer(value: object, name: str, minimum: int) -> int:
    if isinstance(value, bool):
        raise PlaybackAPIError(f"{name} must be an integer greater than or equal to {minimum}.")
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as exc:
        raise PlaybackAPIError(
            f"{name} must be an integer greater than or equal to {minimum}."
        ) from exc
    if isinstance(value, float) and not value.is_integer():
        raise PlaybackAPIError(f"{name} must be a whole number.")
    if number < minimum:
        raise PlaybackAPIError(f"{name} must be greater than or equal to {minimum}.")
    return number


def _resume_seconds(value: object) -> int:
    if isinstance(value, bool):
        raise PlaybackAPIError("Resume time must be a non-negative number of seconds.")
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as exc:
        raise PlaybackAPIError(
            "Resume time must be a non-negative number of seconds."
        ) from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise PlaybackAPIError("Resume time must be a non-negative number of seconds.")
    return int(seconds)


def _with_progress(url: str, progress_seconds: object) -> str:
    progress = _resume_seconds(progress_seconds)
    return f"{url}?progress={progress}" if progress > 0 else url


def getmovie(tmdb_number: int, progress_seconds: float = 0.0) -> str:
    """Return Videasy's documented movie URL for a TMDB identifier."""
    movie_id = _integer(tmdb_number, "TMDB movie number", 1)
    return _with_progress(
        f"{VIDEASY_PLAYER_BASE_URL}/movie/{movie_id}",
        progress_seconds,
    )


def getshow(
    tmdb_number: int,
    season_number: int,
    episode_number: int,
    progress_seconds: float = 0.0,
) -> str:
    """Return Videasy's documented TV episode URL."""
    show_id = _integer(tmdb_number, "TMDB show number", 1)
    season = _integer(season_number, "Season number", 0)
    episode = _integer(episode_number, "Episode number", 1)
    return _with_progress(
        f"{VIDEASY_PLAYER_BASE_URL}/tv/{show_id}/{season}/{episode}",
        progress_seconds,
    )


__all__ = [
    "PlaybackAPIError",
    "VIDEASY_PLAYER_BASE_URL",
    "getmovie",
    "getshow",
]
