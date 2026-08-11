import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from adblock import (
    DEFAULT_AD_HOST_SUFFIXES,
    build_playback_adblock_script,
    is_blocked_ad_url,
    load_adblock_settings,
    same_origin,
)


ROOT = Path(__file__).resolve().parents[1]


class AdBlockTests(unittest.TestCase):
    def write_config(self, payload: object) -> Path:
        temporary = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            delete=False,
        )
        with temporary:
            json.dump(payload, temporary)
        self.addCleanup(lambda: Path(temporary.name).unlink(missing_ok=True))
        return Path(temporary.name)

    def test_blocking_is_enabled_by_default(self) -> None:
        missing = Path(tempfile.gettempdir()) / "pistick-missing-adblock-config.json"
        missing.unlink(missing_ok=True)
        settings = load_adblock_settings(missing)
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.blocked_hosts, DEFAULT_AD_HOST_SUFFIXES)

    def test_known_ad_hosts_and_subdomains_are_blocked(self) -> None:
        settings = load_adblock_settings(self.write_config({}))
        self.assertTrue(is_blocked_ad_url("https://doubleclick.net/ad.js", settings))
        self.assertTrue(
            is_blocked_ad_url("https://pagead2.googlesyndication.com/pagead/js", settings)
        )
        self.assertFalse(
            is_blocked_ad_url("https://notdoubleclick.net/video/master.m3u8", settings)
        )

    def test_video_subtitle_and_api_requests_are_not_broadly_blocked(self) -> None:
        settings = load_adblock_settings(self.write_config({}))
        for url in (
            "https://video.example/ads-in-title/master.m3u8",
            "https://subtitle.example/v2/movie/550",
            "https://playback.example/embed/movie/550",
        ):
            with self.subTest(url=url):
                self.assertFalse(is_blocked_ad_url(url, settings))

    def test_private_config_can_disable_or_extend_the_blocker(self) -> None:
        disabled = load_adblock_settings(
            self.write_config(
                {
                    "adblock_enabled": False,
                    "adblock_domains": ["*.ads.private-example.test"],
                }
            )
        )
        self.assertFalse(
            is_blocked_ad_url("https://ads.private-example.test/banner", disabled)
        )

        enabled = load_adblock_settings(
            self.write_config(
                {"adblock_domains": ["*.ads.private-example.test", "https://invalid.test"]}
            )
        )
        self.assertIn("ads.private-example.test", enabled.blocked_hosts)
        self.assertNotIn("https://invalid.test", enabled.blocked_hosts)
        self.assertTrue(
            is_blocked_ad_url("https://cdn.ads.private-example.test/banner", enabled)
        )

    def test_origin_lock_allows_paths_and_default_ports_only(self) -> None:
        self.assertTrue(
            same_origin(
                "https://playback.example/embed/movie/550",
                "https://playback.example:443/redirect",
            )
        )
        self.assertFalse(
            same_origin(
                "https://playback.example/embed/movie/550",
                "https://ads.example/landing",
            )
        )
        self.assertFalse(
            same_origin(
                "https://playback.example/embed/movie/550",
                "http://playback.example/landing",
            )
        )

    def test_generated_script_has_valid_javascript(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is unavailable")
        settings = load_adblock_settings(self.write_config({}))
        result = subprocess.run(
            [node, "--check", "-"],
            input=build_playback_adblock_script(settings),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_script_blocks_popups_and_late_ad_requests(self) -> None:
        source = build_playback_adblock_script(
            load_adblock_settings(self.write_config({}))
        )
        for expected in (
            "Object.defineProperty(window, 'open'",
            "window.fetch =",
            "XMLHttpRequest.prototype.open",
            "navigator.sendBeacon",
            "MutationObserver",
            "event.stopImmediatePropagation()",
        ):
            self.assertIn(expected, source)
        self.assertNotIn("querySelectorAll('video')", source)


class AdBlockIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_source = (ROOT / "main.py").read_text(encoding="utf-8")

    def test_pi_webengine_blocks_before_the_network_stack(self) -> None:
        self.assertIn("QWebEngineUrlRequestInterceptor", self.main_source)
        self.assertIn("profile.setUrlRequestInterceptor(interceptor)", self.main_source)
        self.assertIn("info.block(True)", self.main_source)
        self.assertIn('setName("pistick-playback-adblock")', self.main_source)
        self.assertIn('getattr(injection_points, "DocumentCreation")', self.main_source)
        self.assertIn("self._adblock_script.setRunsOnSubFrames(True)", self.main_source)

    def test_trailers_remain_outside_playback_filtering(self) -> None:
        self.assertIn("block_ads=bool(self.embed_url)", self.main_source)
        self.assertIn("_TRAILER_WEB_PROFILE", self.main_source)
        self.assertIn("_PLAYBACK_WEB_PROFILE", self.main_source)

    def test_windows_injects_filtering_and_locks_external_redirects(self) -> None:
        self.assertIn("self._native_view.loadProgressChanged.connect", self.main_source)
        self.assertIn("self._native_view.urlChanged.connect(self._url_changed)", self.main_source)
        self.assertIn("self._run_javascript(self._adblock_source)", self.main_source)
        self.assertIn("same_origin(candidate, self._trusted_url)", self.main_source)

    def test_browser_security_is_not_disabled(self) -> None:
        self.assertNotIn("--disable-web-security", self.main_source)
        self.assertNotIn("WebSecurityEnabled", self.main_source)


if __name__ == "__main__":
    unittest.main()
