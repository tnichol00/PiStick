import json
from pathlib import Path
import tempfile
import unittest

from pistick_server.state import StateError, WatchStateStore


MOVIE = {
    "id": 550,
    "media_type": "movie",
    "title": "Fight Club",
    "poster_path": "/poster.jpg",
}

SHOW = {
    "id": 1399,
    "media_type": "tv",
    "name": "Game of Thrones",
    "seasons": [
        {"season_number": 1, "episode_count": 2, "name": "Season 1"},
        {"season_number": 2, "episode_count": 2, "name": "Season 2"},
    ],
}


class ServerStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "state.json"
        self.store = WatchStateStore(self.path)
        self.profile_id = self.store.profiles()[0]["id"]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_profiles_are_server_side_and_persist(self) -> None:
        self.store.activate_profile(self.profile_id)
        second = self.store.add_profile("Coby")
        renamed = self.store.rename_profile(second["id"], "Layered Kingdom")
        self.assertEqual(renamed["name"], "Layered Kingdom")

        reloaded = WatchStateStore(self.path)
        self.assertEqual(reloaded.profiles_payload()["active_profile"], self.profile_id)
        self.assertIn("Layered Kingdom", [profile["name"] for profile in reloaded.profiles()])
        self.assertNotIn("localStorage", self.path.read_text(encoding="utf-8"))

    def test_last_profile_cannot_be_deleted(self) -> None:
        with self.assertRaises(StateError):
            self.store.delete_profile(self.profile_id)

    def test_movie_position_is_saved_and_returned_in_continue_watching(self) -> None:
        self.store.activate_profile(self.profile_id)
        self.store.mark_started(self.profile_id, MOVIE)
        self.store.set_position(self.profile_id, MOVIE, 412.5, 6000)
        entry = self.store.entry(self.profile_id, MOVIE)
        self.assertEqual(entry["position_seconds"], 412.5)
        self.assertEqual(entry["duration_seconds"], 6000.0)
        self.assertEqual(entry["status"], "in_progress")
        self.assertEqual(self.store.continue_watching(self.profile_id)[0]["id"], 550)

        self.store.set_position(self.profile_id, MOVIE, 5990, 6000)
        self.assertEqual(self.store.entry(self.profile_id, MOVIE)["status"], "finished")
        self.assertEqual(self.store.continue_watching(self.profile_id), [])

    def test_episode_resume_moves_forward_across_seasons(self) -> None:
        self.store.activate_profile(self.profile_id)
        episode = {"season_number": 1, "episode_number": 2, "name": "Episode 2"}
        self.store.set_episode_position(self.profile_id, SHOW, episode, 100, 100)
        self.assertEqual(self.store.resume_episode(self.profile_id, SHOW), (2, 1))

        next_episode = {"season_number": 2, "episode_number": 1, "name": "Episode 1"}
        self.store.set_episode_position(self.profile_id, SHOW, next_episode, 25, 100)
        self.assertEqual(self.store.resume_episode(self.profile_id, SHOW), (2, 1))
        show_entry = self.store.entry(self.profile_id, SHOW)
        self.assertEqual(show_entry["last_episode"], {"season_number": 2, "episode_number": 1})
        self.assertEqual(show_entry["status"], "in_progress")

    def test_desktop_state_schema_imports_without_conversion(self) -> None:
        legacy = {
            "active_profile": "profile-old",
            "profiles": [{"id": "profile-old", "name": "Old", "avatar": "blue"}],
            "watch_state": {
                "profile-old": {
                    "movie:550": {
                        "status": "in_progress",
                        "progress": 0.5,
                        "position_seconds": 300,
                        "duration_seconds": 600,
                        "media": MOVIE,
                    }
                }
            },
        }
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        imported = WatchStateStore(self.path)
        self.assertEqual(imported.profiles_payload()["active_profile"], "profile-old")
        self.assertEqual(imported.entry("profile-old", MOVIE)["position_seconds"], 300)


if __name__ == "__main__":
    unittest.main()
