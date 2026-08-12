import json
from pathlib import Path
import struct
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class WindowsInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.installer = (PROJECT_ROOT / "install.ps1").read_text(encoding="utf-8")
        cls.main_source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
        cls.manifest = json.loads(
            (PROJECT_ROOT / "pistick-release.json").read_text(encoding="utf-8")
        )

    def test_installs_per_user_under_local_app_data(self) -> None:
        self.assertIn("$env:LOCALAPPDATA", self.installer)
        self.assertIn("Join-Path $env:LOCALAPPDATA 'PiStick'", self.installer)
        self.assertIn("Join-Path $InstallRoot 'data'", self.installer)
        self.assertNotIn("Program Files", self.installer)

    def test_uses_only_published_release_archives_for_app_code(self) -> None:
        self.assertIn("/releases?per_page=100", self.installer)
        self.assertIn("-not $_.draft", self.installer)
        self.assertIn("$_.zipball_url", self.installer)
        self.assertNotRegex(self.installer, r"git\s+(clone|pull|fetch|checkout)")
        self.assertNotIn("/archive/refs/heads/", self.installer)

    def test_launcher_keeps_private_data_outside_release(self) -> None:
        self.assertIn('environment("PISTICK_CONFIG_PATH")', self.installer)
        self.assertIn('environment("PISTICK_STATE_PATH")', self.installer)
        self.assertIn('environment("PISTICK_CACHE_DIR")', self.installer)
        self.assertIn('"current-release.txt"', self.installer)
        self.assertIn('"pistick_state.json"', self.installer)

    def test_creates_start_menu_shortcut_with_pistick_icon(self) -> None:
        self.assertIn("Microsoft\\Windows\\Start Menu\\Programs", self.installer)
        self.assertIn("$shell.CreateShortcut($ShortcutPath)", self.installer)
        self.assertIn('$shortcut.IconLocation = "$IconPath,0"', self.installer)
        self.assertIn("'PiStick.lnk'", self.installer)

    def test_installer_does_not_schedule_updates(self) -> None:
        blocked = ("Register-ScheduledTask", "schtasks", "New-ScheduledTask", "RunOnce")
        for command in blocked:
            self.assertNotIn(command, self.installer)

    def test_release_manifest_contains_windows_runtime_files(self) -> None:
        required = set(self.manifest["required_files"])
        self.assertTrue(
            {
                "install.ps1",
                "requirements-windows.txt",
                "assets/pistick.ico",
            }.issubset(required)
        )
        self.assertEqual(self.manifest["windows_updater"], "install.ps1")
        self.assertEqual(
            self.manifest["windows_requirements"], "requirements-windows.txt"
        )
        self.assertEqual(self.manifest["icon"], "assets/pistick.ico")

    def test_icon_is_a_multi_resolution_windows_icon(self) -> None:
        icon = (PROJECT_ROOT / "assets" / "pistick.ico").read_bytes()
        reserved, image_type, count = struct.unpack_from("<HHH", icon, 0)
        self.assertEqual((reserved, image_type), (0, 1))
        self.assertGreaterEqual(count, 7)

        sizes = set()
        for index in range(count):
            offset = 6 + index * 16
            width, height, _, _, _, _, length, image_offset = struct.unpack_from(
                "<BBBBHHII", icon, offset
            )
            sizes.add((256 if width == 0 else width, 256 if height == 0 else height))
            self.assertGreater(length, 0)
            self.assertLessEqual(image_offset + length, len(icon))
        self.assertTrue({(256, 256), (64, 64), (32, 32), (16, 16)}.issubset(sizes))

    def test_application_uses_the_packaged_icon(self) -> None:
        self.assertIn('APP_ICON_PATH = Path(__file__).resolve().parent / "assets" / "pistick.ico"', self.main_source)
        self.assertIn("app.setWindowIcon(QIcon(str(APP_ICON_PATH)))", self.main_source)

    def test_script_has_no_bash_style_line_continuations(self) -> None:
        self.assertFalse(any(line.rstrip().endswith("\\") for line in self.installer.splitlines()))


if __name__ == "__main__":
    unittest.main()
