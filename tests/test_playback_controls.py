import ast
import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PlaybackControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        cls.module = ast.parse(cls.main_source)

    def class_method(self, class_name: str, method_name: str) -> str:
        class_node = next(
            node
            for node in self.module.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        method = next(
            node
            for node in class_node.body
            if isinstance(node, ast.FunctionDef) and node.name == method_name
        )
        return ast.get_source_segment(self.main_source, method) or ""

    def test_a_toggles_trailers_and_api_playback(self) -> None:
        activate = self.class_method("MainWindow", "_controller_activate")
        self.assertIn("detail.is_player_screen_open()", activate)
        self.assertIn("detail.toggle_player_playback()", activate)
        self.assertIn("window.pistickToggleTrailer", self.main_source)
        self.assertGreaterEqual(self.main_source.count("def toggle_media(self)"), 2)
        self.assertIn("action: 'toggle'", self.main_source)

    def test_players_autoplay_after_watch_is_selected(self) -> None:
        self.assertIn("let playRequested = true", self.main_source)
        self.assertIn("autoplay: 1", self.main_source)
        self.assertIn("let autoplayPending = true", self.main_source)
        self.assertIn("triggerVideasyStartOverlay", self.main_source)
        self.assertIn("if (autoplayPending) requestPlaybackStart()", self.main_source)
        self.assertIn("PlaybackRequiresUserGesture", self.main_source)
        self.assertIn("--autoplay-policy=no-user-gesture-required", self.main_source)

    def test_x_toggles_english_subtitles_for_trailers_and_content(self) -> None:
        poll = self.class_method("ControllerManager", "_poll")
        handler = self.class_method("MainWindow", "_controller_subtitles")
        detail_toggle = self.class_method("DetailDialog", "toggle_player_subtitles")
        self.assertIn("subtitles = Signal()", self.main_source)
        self.assertIn("self._button_edge(2)", poll)
        self.assertIn("self.subtitles.emit()", poll)
        self.assertIn(
            "self.controller.subtitles.connect(self._controller_subtitles)",
            self.main_source,
        )
        self.assertIn("detail.is_player_screen_open()", handler)
        self.assertIn("detail.toggle_player_subtitles()", handler)
        self.assertIn("dialog.toggle_subtitles()", detail_toggle)
        self.assertIn("window.pistickToggleTrailerSubtitles", self.main_source)
        self.assertIn("action: 'subtitles-english-toggle'", self.main_source)
        self.assertGreaterEqual(self.main_source.count("def toggle_subtitles(self)"), 3)

    def test_subtitles_start_off_and_trailers_prefer_english(self) -> None:
        self.assertIn("cc_load_policy: 0", self.main_source)
        self.assertIn("cc_lang_pref: 'en'", self.main_source)
        self.assertIn("let englishSubtitlesEnabled = false", self.main_source)
        self.assertIn("track.mode = englishSubtitlesEnabled", self.main_source)
        self.assertIn("hls.subtitleTrack = englishSubtitlesEnabled", self.main_source)

    def test_left_and_right_seek_actual_playback_by_ten_seconds(self) -> None:
        navigate = self.class_method("MainWindow", "_controller_navigate")
        self.assertIn('direction in {"left", "right"}', navigate)
        self.assertIn("PLAYBACK_SEEK_SECONDS", navigate)
        self.assertIn("detail.seek_playback(offset)", navigate)
        self.assertIn("PLAYBACK_SEEK_SECONDS = 10", self.main_source)
        self.assertIn("action: 'seek-relative'", self.main_source)
        self.assertIn("requestRelativeSeek(data.offsetSeconds)", self.main_source)

    def test_b_closes_actual_playback_directly_to_details(self) -> None:
        controller_back = self.class_method("PlaybackDialog", "controller_back")
        self.assertIn("self.reject()", controller_back)
        self.assertNotIn("exit_fullscreen", controller_back)

    def test_escape_uses_the_same_back_path_as_controller_b(self) -> None:
        trailer_escape = self.class_method("TrailerDialog", "_escape_requested")
        detail_escape = self.class_method("DetailDialog", "_escape_requested")
        self.assertIn("self.controller_back()", trailer_escape)
        self.assertIn("self.controller_back()", detail_escape)

    def test_continue_watching_shows_episode_and_show_completion_actions(self) -> None:
        render = self.class_method("DetailDialog", "_render")
        self.assertIn("✓  Mark Episode as Finished", render)
        self.assertIn("self._mark_current_episode_finished(media)", render)
        self.assertIn("✓  Mark Show as Finished", render)
        self.assertIn("self._mark_finished(media)", render)
        self.assertIn("Mark as Unwatched", render)

    def test_continue_watching_refreshes_when_playback_exits(self) -> None:
        playback_finished = self.class_method("DetailDialog", "_playback_finished")
        playback_exited = self.class_method("MainWindow", "_detail_playback_exited")
        refresh = self.class_method("MainWindow", "_refresh_home_from_state")
        self.assertIn("playbackExited = Signal()", self.main_source)
        self.assertIn("self.playbackExited.emit()", playback_finished)
        self.assertIn("dialog.playbackExited.connect(self._detail_playback_exited)", self.main_source)
        self.assertIn("self._refresh_home_from_state()", playback_exited)
        self.assertNotIn("self.stack.currentWidget() != self.home_page", refresh)

    def test_featured_focus_scrolls_home_to_the_exact_top(self) -> None:
        ensure_visible = self.class_method("MainWindow", "_ensure_focus_visible")
        scroll_to_top = self.class_method("SmoothScrollArea", "smooth_scroll_to_top")
        self.assertIn("HeroBanner", ensure_visible)
        self.assertIn('parent.objectName() == "mainScroll"', ensure_visible)
        self.assertIn("parent.smooth_scroll_to_top()", ensure_visible)
        self.assertIn("bar.minimum()", scroll_to_top)

    def test_players_request_or_lock_1080p_without_api_query_parameters(self) -> None:
        self.assertIn("vq: 'hd1080'", self.main_source)
        self.assertIn("const preferredHeight = 1080", self.main_source)
        self.assertIn("state.settings.quality = `${preferredHeight}p`", self.main_source)
        self.assertIn("hls.currentLevel = selected.index", self.main_source)
        self.assertNotIn("?quality=", self.main_source)

    def test_generated_youtube_toggle_script_is_valid_javascript(self) -> None:
        function = next(
            node
            for node in self.module.body
            if isinstance(node, ast.FunctionDef) and node.name == "build_youtube_embed_html"
        )
        namespace = {
            "json": json,
            "YOUTUBE_REFERER": "https://com.layeredkingdom.pistick/",
        }
        exec(
            compile(
                ast.Module(body=[function], type_ignores=[]),
                str(ROOT / "main.py"),
                "exec",
            ),
            namespace,
        )
        html = namespace["build_youtube_embed_html"]("test-video-key")
        self.assertIn("pistickToggleTrailer", html)
        self.assertIn("pistickToggleTrailerSubtitles", html)
        self.assertIn("cc_load_policy: 0", html)
        self.assertIn("cc_lang_pref: 'en'", html)
        self.assertIn("autoplay: 1", html)
        self.assertIn("hd1080", html)
        script = html.split("<script>", 1)[1].split("</script>", 1)[0]
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is unavailable")
        result = subprocess.run(
            [node, "--check", "-"],
            input=script,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
