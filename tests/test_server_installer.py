from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ServerInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.installer = (ROOT / "install-server.ps1").read_text(encoding="utf-8")
        cls.open_script = (ROOT / "windows" / "open-server.ps1").read_text(encoding="utf-8")
        cls.stop_script = (ROOT / "windows" / "stop-server.ps1").read_text(encoding="utf-8")
        cls.launcher = (ROOT / "windows" / "background_launcher.pyw").read_text(encoding="utf-8")
        cls.hidden_launcher = (ROOT / "windows" / "start-hidden.vbs").read_text(encoding="utf-8")
        cls.server = (ROOT / "pistick_server" / "app.py").read_text(encoding="utf-8")

    def test_installs_per_user_and_keeps_data_outside_app(self) -> None:
        self.assertIn("Join-Path $env:LOCALAPPDATA 'PiStickServer'", self.installer)
        self.assertIn("$DataRoot", self.installer)
        self.assertIn("$AppRoot", self.installer)
        self.assertIn('self.data_dir / "config.json"', self.server)

    def test_background_server_starts_at_windows_sign_in(self) -> None:
        self.assertIn("[Environment]::GetFolderPath('Startup')", self.installer)
        self.assertIn("PiStick Server Background.lnk", self.installer)
        self.assertIn("start-hidden.vbs", self.installer)
        self.assertIn("background_launcher.pyw", self.hidden_launcher)
        self.assertIn("from pistick_server.app import main", self.launcher)

    def test_server_is_localhost_only(self) -> None:
        self.assertIn('"--host", "127.0.0.1"', self.launcher)
        self.assertIn("PiStick Server may only bind to localhost", self.server)
        self.assertNotIn('host="0.0.0.0"', self.server)

    def test_existing_desktop_profiles_are_imported(self) -> None:
        self.assertIn("PiStick\\data\\pistick_state.json", self.installer)
        self.assertIn("Imported profiles and watch history", self.installer)

    def test_python_is_not_pinned_to_one_minor_version(self) -> None:
        self.assertIn("version[1] -ge 10", self.installer)
        self.assertNotIn("-3.12", self.installer)
        self.assertIn("pythonw.exe", self.installer)

    def test_start_menu_contains_open_and_stop_controls(self) -> None:
        self.assertIn("Open PiStick Server.lnk", self.installer)
        self.assertIn("Stop PiStick Server.lnk", self.installer)
        self.assertIn("/health", self.open_script)
        self.assertIn("/api/admin/shutdown", self.stop_script)

    def test_state_changing_http_requests_require_custom_header(self) -> None:
        self.assertIn('X-PiStick-Request', self.server)
        self.assertIn('Cross-site request blocked.', self.server)


if __name__ == "__main__":
    unittest.main()
