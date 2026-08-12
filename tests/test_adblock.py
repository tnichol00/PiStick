import json
from pathlib import Path
import shutil
import socket
import socketserver
import subprocess
import tempfile
import threading
import unittest

from adblock import (
    AdBlockSettings,
    DEFAULT_AD_HOST_SUFFIXES,
    DEFAULT_PLAYBACK_ALLOWED_HOST_SUFFIXES,
    DEFAULT_REMOTE_BLOCKLIST_URL,
    add_webview_proxy_argument,
    build_playback_adblock_script,
    is_blocked_ad_url,
    load_adblock_settings,
    parse_hosts_blocklist,
    refresh_adblock_cache,
    same_origin,
    start_playback_adblock_proxy,
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

    def test_maintained_list_uses_oisd_wildcard_domains(self) -> None:
        self.assertEqual(DEFAULT_REMOTE_BLOCKLIST_URL, "https://big.oisd.nl/domainswild")
        parsed = parse_hosts_blocklist(
            "# oisd wildcard syntax\n*.first-ad.test\n*.rotating-ad.test\n"
        )
        self.assertIn("first-ad.test", parsed)
        self.assertIn("rotating-ad.test", parsed)

    def test_videasy_player_and_core_api_hosts_are_always_allowed(self) -> None:
        settings = load_adblock_settings(
            self.write_config(
                {
                    "adblock_domains": [
                        "player.videasy.to",
                        "api.speedracelight.com",
                    ]
                }
            )
        )
        self.assertIn("player.videasy.to", DEFAULT_PLAYBACK_ALLOWED_HOST_SUFFIXES)
        self.assertIn("api.speedracelight.com", DEFAULT_PLAYBACK_ALLOWED_HOST_SUFFIXES)
        self.assertFalse(
            is_blocked_ad_url("https://player.videasy.to/movie/550", settings)
        )
        self.assertFalse(
            is_blocked_ad_url("https://api.speedracelight.com/vsrc/source", settings)
        )

    def test_video_subtitle_and_api_requests_are_not_broadly_blocked(self) -> None:
        settings = load_adblock_settings(self.write_config({}))
        for url in (
            "https://video.example/ads-in-title/master.m3u8",
            "https://subtitle.example/v2/movie/550",
            "https://player.videasy.to/movie/550",
        ):
            with self.subTest(url=url):
                self.assertFalse(is_blocked_ad_url(url, settings))

    def test_provider_and_private_allowlists_override_strong_rules(self) -> None:
        settings = load_adblock_settings(
            self.write_config(
                {
                    "adblock_domains": ["player.videasy.to", "media.example"],
                    "adblock_allow_domains": ["media.example"],
                }
            )
        )
        self.assertFalse(
            is_blocked_ad_url("https://player.videasy.to/movie/550", settings)
        )
        self.assertFalse(
            is_blocked_ad_url("https://cdn.media.example/video/master.m3u8", settings)
        )

    def test_cached_online_list_is_loaded_and_can_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "hosts.txt"
            cache.write_text(
                "0.0.0.0 suspicious-ad.test\ntracker.example\n",
                encoding="utf-8",
            )
            enabled = load_adblock_settings(self.write_config({}), cache)
            self.assertTrue(
                is_blocked_ad_url("https://cdn.suspicious-ad.test/popup", enabled)
            )
            disabled = load_adblock_settings(
                self.write_config({"adblock_online_lists": False}),
                cache,
            )
            self.assertFalse(
                is_blocked_ad_url("https://cdn.suspicious-ad.test/popup", disabled)
            )

    def test_hosts_parser_and_atomic_refresh_activate_new_rules(self) -> None:
        parsed = parse_hosts_blocklist(
            "# list\n0.0.0.0 first.test\n127.0.0.1 second.test alias.test\n"
        )
        self.assertEqual(parsed, frozenset({"first.test", "second.test", "alias.test"}))

        class Response:
            def read(self, _limit: int) -> bytes:
                return b"0.0.0.0 fresh-one.test\n0.0.0.0 fresh-two.test\n"

            def close(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "strong-hosts.txt"
            settings = load_adblock_settings(self.write_config({}), cache)
            refreshed = refresh_adblock_cache(
                settings,
                cache,
                source_url="https://lists.example/hosts",
                opener=lambda _request, timeout: Response(),
                minimum_hosts=2,
            )
            self.assertTrue(refreshed)
            self.assertTrue(cache.is_file())
            self.assertTrue(
                is_blocked_ad_url("https://img.fresh-two.test/banner.jpg", settings)
            )

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
            "frame.setAttribute('sandbox'",
            "frame.removeAttribute('allowfullscreen')",
            "'pointerdown'",
            "Node.prototype.appendChild",
            "blockInsertedNode(node)",
        ):
            self.assertIn(expected, source)
        self.assertNotIn("querySelectorAll('video')", source)

    def test_online_hosts_are_not_embedded_into_every_javascript_frame(self) -> None:
        settings = AdBlockSettings(
            enabled=True,
            blocked_hosts=frozenset({"built-in.test", "large-online-list.test"}),
            script_hosts=frozenset({"built-in.test"}),
            configured_hosts=frozenset({"built-in.test"}),
        )
        source = build_playback_adblock_script(settings)
        self.assertIn("built-in.test", source)
        self.assertNotIn("large-online-list.test", source)

    def test_webview_proxy_argument_preserves_user_proxy(self) -> None:
        arguments, installed = add_webview_proxy_argument("--disable-gpu", 12345)
        self.assertTrue(installed)
        self.assertEqual(
            arguments,
            "--disable-gpu --proxy-server=http://127.0.0.1:12345",
        )
        existing, installed = add_webview_proxy_argument(
            "--proxy-server=http://corporate.example:8080",
            12345,
        )
        self.assertFalse(installed)
        self.assertEqual(existing, "--proxy-server=http://corporate.example:8080")
        existing, installed = add_webview_proxy_argument("--no-proxy-server", 12345)
        self.assertFalse(installed)
        self.assertEqual(existing, "--no-proxy-server")

    def test_loopback_proxy_blocks_https_connect_before_dns(self) -> None:
        settings = AdBlockSettings(
            enabled=True,
            blocked_hosts=frozenset({"unsafe-popup.test"}),
        )
        proxy = start_playback_adblock_proxy(settings)
        self.assertIsNotNone(proxy)
        try:
            with socket.create_connection(("127.0.0.1", proxy.port), timeout=2) as client:
                client.sendall(
                    b"CONNECT unsafe-popup.test:443 HTTP/1.1\r\n"
                    b"Host: unsafe-popup.test:443\r\n\r\n"
                )
                response = client.recv(4096)
            self.assertIn(b"403 Forbidden", response)
        finally:
            proxy.stop()

    def test_loopback_proxy_relays_allowed_http_without_inspection(self) -> None:
        class UpstreamHandler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                self.request.recv(4096)
                self.request.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
                    b"Connection: close\r\n\r\nOK"
                )

        upstream = socketserver.TCPServer(("127.0.0.1", 0), UpstreamHandler)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        proxy = start_playback_adblock_proxy(
            AdBlockSettings(enabled=True, blocked_hosts=frozenset())
        )
        self.assertIsNotNone(proxy)
        try:
            upstream_port = int(upstream.server_address[1])
            with socket.create_connection(("127.0.0.1", proxy.port), timeout=2) as client:
                client.sendall(
                    f"GET http://127.0.0.1:{upstream_port}/ok HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{upstream_port}\r\n"
                    "Connection: close\r\n\r\n".encode("ascii")
                )
                response = bytearray()
                while True:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    response.extend(chunk)
            self.assertIn(b"200 OK", response)
            self.assertTrue(response.endswith(b"OK"))
        finally:
            proxy.stop()
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join(timeout=2)


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
        self.assertIn("start_playback_adblock_proxy(adblock_settings)", self.main_source)
        self.assertIn('os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"]', self.main_source)

    def test_browser_security_is_not_disabled(self) -> None:
        self.assertNotIn("--disable-web-security", self.main_source)
        self.assertNotIn("WebSecurityEnabled", self.main_source)


if __name__ == "__main__":
    unittest.main()
