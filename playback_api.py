"""Build embed URLs for the legal playback service configured on this Pi.

TMDB supplies identifiers and metadata only. The playback service base URL is
private runtime configuration and is deliberately never stored in this module.
"""

import json
import os
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit


class PlaybackAPIError(ValueError):
    """Raised when an invalid movie or episode identifier is supplied."""


def _config_path() -> Path:
    configured = os.getenv("PISTICK_CONFIG_PATH", "").strip()
    return Path(configured).expanduser() if configured else Path(__file__).with_name("config.json")


def _playback_base_url() -> str:
    path = _config_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        configured = str(payload.get("playback_base_url") or "").strip()
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise PlaybackAPIError(
            f"Playback is not configured. Add playback_base_url to {path}."
        ) from exc

    if not configured:
        raise PlaybackAPIError(
            f"Playback is not configured. Add playback_base_url to {path}."
        )

    try:
        parts = urlsplit(configured)
        port = parts.port
    except ValueError as exc:
        raise PlaybackAPIError("playback_base_url is not a valid HTTPS URL.") from exc

    if (
        parts.scheme.lower() != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise PlaybackAPIError(
            "playback_base_url must be an HTTPS base URL without credentials, a query, or a fragment."
        )

    hostname = parts.hostname.lower()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname if port is None else f"{hostname}:{port}"
    path_prefix = parts.path.rstrip("/")
    return urlunsplit(("https", netloc, path_prefix, "", ""))


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


def _with_playback_options(
    url: str,
    start_seconds: object,
    *,
    auto_next_episode: bool = False,
) -> str:
    start = _integer(start_seconds, "Resume timestamp", 0)
    parameters: list[tuple[str, object]] = [
        ("autoplay", 1),
        ("ds_lang", "en"),
    ]
    if auto_next_episode:
        parameters.append(("autonext", 1))
    if start:
        parameters.append(("startAt", start))
    return f"{url}?{urlencode(parameters)}"


def getmovie(tmdb_number: int, start_seconds: int = 0) -> str:
    """Return the configured embed URL for a movie's TMDB identifier."""
    movie_id = _integer(tmdb_number, "TMDB movie number", 1)
    return _with_playback_options(
        f"{_playback_base_url()}/embed/movie/{movie_id}",
        start_seconds,
    )


def getshow(
    tmdb_number: int,
    season_number: int,
    episode_number: int,
    start_seconds: int = 0,
) -> str:
    """Return the configured embed URL for one TV episode."""
    show_id = _integer(tmdb_number, "TMDB show number", 1)
    season = _integer(season_number, "Season number", 0)
    episode = _integer(episode_number, "Episode number", 1)
    return _with_playback_options(
        f"{_playback_base_url()}/embed/tv/{show_id}/{season}/{episode}",
        start_seconds,
        auto_next_episode=True,
    )


__all__ = ["PlaybackAPIError", "getmovie", "getshow"]
