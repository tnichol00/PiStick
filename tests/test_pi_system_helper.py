import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pistick_system_helper", ROOT / "pi" / "pistick-system-helper.py"
)
assert SPEC and SPEC.loader
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class PiSystemHelperTests(unittest.TestCase):
    def test_nmcli_parser_preserves_escaped_colons_and_backslashes(self) -> None:
        self.assertEqual(
            helper.split_nmcli(r"*:Living\:Room:91:WPA2"),
            ["*", "Living:Room", "91", "WPA2"],
        )
        self.assertEqual(
            helper.split_nmcli(r":Printer\\Net:40:--"),
            ["", r"Printer\Net", "40", "--"],
        )

    def test_wifi_scan_deduplicates_and_sorts_networks(self) -> None:
        output = "\n".join(
            [
                r":Guest:35:--",
                r"*:Home\:5G:82:WPA2",
                r":Guest:60:--",
            ]
        )
        with patch.object(helper, "command", return_value=output):
            networks = helper.wifi_networks(True)
        self.assertEqual([item["ssid"] for item in networks], ["Home:5G", "Guest"])
        self.assertTrue(networks[0]["connected"])
        self.assertEqual(networks[1]["signal"], 60)
        self.assertEqual(networks[1]["security"], "Open")

    def test_wifi_connect_uses_nmcli_without_a_shell(self) -> None:
        calls = []

        def fake_command(arguments, **kwargs):
            calls.append((arguments, kwargs))
            return ""

        with patch.object(helper, "command", side_effect=fake_command), patch.object(
            helper, "status", return_value={"wifi": {"connected": True}}
        ):
            helper.wifi_connect({"ssid": "Home", "password": "private-secret"})
        arguments, options = calls[0]
        self.assertEqual(
            arguments,
            [
                "nmcli",
                "--wait",
                "40",
                "device",
                "wifi",
                "connect",
                "Home",
                "password",
                "private-secret",
                "ifname",
                "wlan0",
            ],
        )
        self.assertNotIn("input_text", options)

    def test_wired_controller_parser_ignores_keyboards(self) -> None:
        devices = """I: Bus=0003 Vendor=0001 Product=0001 Version=0100
N: Name=\"USB Keyboard\"
H: Handlers=sysrq kbd event0

I: Bus=0003 Vendor=045e Product=02ea Version=0301
N: Name=\"Xbox Wireless Controller\"
H: Handlers=event1 js0
"""
        with patch("builtins.open", return_value=io.StringIO(devices)):
            controllers = helper.wired_controllers()
        self.assertEqual(len(controllers), 1)
        self.assertEqual(controllers[0]["name"], "Xbox Wireless Controller")


if __name__ == "__main__":
    unittest.main()
