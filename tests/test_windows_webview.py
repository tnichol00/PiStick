import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WindowsWebViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        cls.module = ast.parse(cls.main_source)

    def function(self, name: str) -> ast.FunctionDef:
        return next(
            node
            for node in self.module.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        )

    def test_native_backend_is_initialized_before_qapplication(self) -> None:
        main = self.function("main")
        calls = [
            node.func.id
            for node in ast.walk(main)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        self.assertLess(
            calls.index("_initialize_windows_playback_webview"),
            calls.index("QApplication"),
        )

    def test_windows_player_uses_edge_webview2_qwindow(self) -> None:
        self.assertIn("from PySide6.QtWebView import QWebView, QtWebView", self.main_source)
        self.assertIn('os.environ["QT_WEBVIEW_PLUGIN"] = "webview2"', self.main_source)
        self.assertIn("QtWebView.initialize()", self.main_source)
        self.assertIn("QWidget.createWindowContainer", self.main_source)
        self.assertIn("self._native_view = QWebView()", self.main_source)
        self.assertIn('"WindowDoesNotAcceptFocus"', self.main_source)

    def test_only_api_playback_uses_the_windows_native_view(self) -> None:
        self.assertIn(
            "get_playback_web_view_class()\n            if self.embed_url",
            self.main_source,
        )
        self.assertIn("else get_trailer_web_view_class()", self.main_source)

    def test_windows_path_preserves_progress_messages(self) -> None:
        self.assertIn("_PLAYBACK_FRAME_BRIDGE_SOURCE.replace", self.main_source)
        self.assertIn("pistick-playback-progress", self.main_source)
        self.assertIn("JSON.stringify(state)", self.main_source)

    def test_windows_path_reports_hls_codec_capability(self) -> None:
        self.assertIn("MediaSource.isTypeSupported", self.main_source)
        self.assertIn("H.264/AAC MSE", self.main_source)

    def test_fix_does_not_disable_browser_security(self) -> None:
        self.assertNotIn("--disable-web-security", self.main_source)
        self.assertNotIn("WebSecurityEnabled", self.main_source)

    def test_windows_requirements_select_qt_611_or_newer(self) -> None:
        requirements = (ROOT / "requirements-windows.txt").read_text(encoding="utf-8")
        self.assertIn("PySide6>=6.11,<7", requirements.splitlines())


if __name__ == "__main__":
    unittest.main()
