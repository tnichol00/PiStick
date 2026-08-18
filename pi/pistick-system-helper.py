#!/usr/bin/env python3
"""Root-owned helper for PiStick's HDMI-only system settings.

The web server may invoke only the fixed actions below through sudo. Inputs arrive
as validated JSON on stdin and commands are executed directly without a shell.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Any


SAFE_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
MAC_PATTERN = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")


class HelperError(RuntimeError):
    pass


def command(arguments: list[str], *, timeout: int = 15, input_text: str | None = None) -> str:
    try:
        completed = subprocess.run(
            arguments,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            env={"PATH": SAFE_PATH, "LANG": "C.UTF-8", "LC_ALL": "C"},
        )
    except FileNotFoundError as exc:
        raise HelperError(f"{arguments[0]} is not installed.") from exc
    except subprocess.TimeoutExpired as exc:
        raise HelperError("The system command timed out.") from exc
    output = (completed.stdout or "").strip()
    if completed.returncode != 0:
        raise HelperError(f"{arguments[0]} could not complete the request.")
    return output


def split_nmcli(line: str) -> list[str]:
    values: list[str] = []
    current: list[str] = []
    escaped = False
    for character in line:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            values.append("".join(current))
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    values.append("".join(current))
    return values


def wifi_networks(rescan: bool) -> list[dict[str, Any]]:
    output = command(
        [
            "nmcli",
            "--terse",
            "--escape",
            "yes",
            "--fields",
            "IN-USE,SSID,SIGNAL,SECURITY",
            "device",
            "wifi",
            "list",
            "--rescan",
            "yes" if rescan else "no",
        ],
        timeout=22 if rescan else 8,
    )
    strongest: dict[str, dict[str, Any]] = {}
    for line in output.splitlines():
        fields = split_nmcli(line)
        if len(fields) < 4:
            continue
        active, ssid, signal, security = fields[:4]
        if not ssid:
            continue
        try:
            strength = max(0, min(100, int(signal or 0)))
        except ValueError:
            strength = 0
        network = {
            "ssid": ssid,
            "signal": strength,
            "security": security if security and security != "--" else "Open",
            "connected": active.strip() == "*",
        }
        previous = strongest.get(ssid)
        if previous is None or strength > int(previous["signal"]):
            strongest[ssid] = network
    return sorted(
        strongest.values(),
        key=lambda item: (not bool(item["connected"]), -int(item["signal"]), item["ssid"].lower()),
    )[:24]


def wlan_ipv4() -> str:
    try:
        output = command(["ip", "-4", "-o", "addr", "show", "dev", "wlan0", "scope", "global"])
    except HelperError:
        return ""
    match = re.search(r"\binet\s+([0-9.]+)/", output)
    return match.group(1) if match else ""


def bluetooth_powered() -> bool:
    try:
        output = command(["bluetoothctl", "show"])
    except HelperError:
        return False
    return bool(re.search(r"^\s*Powered:\s+yes\s*$", output, re.MULTILINE | re.IGNORECASE))


def bluetooth_device_lines(paired_only: bool) -> list[tuple[str, str]]:
    commands = (
        ["bluetoothctl", "devices", "Paired"],
        ["bluetoothctl", "paired-devices"],
    ) if paired_only else (["bluetoothctl", "devices"],)
    output = ""
    for arguments in commands:
        try:
            output = command(list(arguments))
            if output or not paired_only:
                break
        except HelperError:
            continue
    devices: list[tuple[str, str]] = []
    for line in output.splitlines():
        match = re.match(r"^Device\s+([0-9A-Fa-f:]{17})\s+(.+)$", line.strip())
        if match:
            devices.append((match.group(1).upper(), match.group(2).strip()))
    return devices[:12]


def bluetooth_info(address: str, fallback_name: str) -> dict[str, Any]:
    paired = False
    connected = False
    name = fallback_name
    try:
        output = command(["bluetoothctl", "info", address])
        name_match = re.search(r"^\s*(?:Name|Alias):\s+(.+)$", output, re.MULTILINE)
        if name_match:
            name = name_match.group(1).strip()
        paired = bool(re.search(r"^\s*Paired:\s+yes\s*$", output, re.MULTILINE | re.IGNORECASE))
        connected = bool(re.search(r"^\s*Connected:\s+yes\s*$", output, re.MULTILINE | re.IGNORECASE))
    except HelperError:
        pass
    return {"address": address, "name": name, "paired": paired, "connected": connected}


def bluetooth_devices(paired_only: bool) -> list[dict[str, Any]]:
    listed = bluetooth_device_lines(paired_only)
    if not paired_only:
        return [
            {"address": address, "name": name, "paired": False, "connected": False}
            for address, name in listed
        ]
    return [bluetooth_info(address, name) for address, name in listed[:8]]


def wired_controllers() -> list[dict[str, str]]:
    try:
        source = open("/proc/bus/input/devices", encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    devices: list[dict[str, str]] = []
    controller_words = ("gamepad", "joystick", "controller", "xbox", "dualsense", "dualshock")
    for block in source.split("\n\n"):
        name_match = re.search(r'^N:\s+Name="([^"]+)"', block, re.MULTILINE)
        handlers_match = re.search(r"^H:\s+Handlers=(.+)$", block, re.MULTILINE)
        if not name_match or not handlers_match:
            continue
        name = name_match.group(1).strip()
        handlers = handlers_match.group(1).strip()
        lowered = name.lower()
        has_joystick = re.search(r"(?:^|\s)js\d+(?:\s|$)", handlers) is not None
        if not has_joystick and not any(word in lowered for word in controller_words):
            continue
        devices.append({"name": name, "handlers": handlers})
    return devices[:12]


def status() -> dict[str, Any]:
    try:
        networks = wifi_networks(False)
        wifi_available = True
    except HelperError:
        networks = []
        wifi_available = False
    active = next((network for network in networks if network["connected"]), None)
    return {
        "wifi": {
            "available": wifi_available,
            "connected": active is not None,
            "ssid": active["ssid"] if active else "",
            "signal": active["signal"] if active else 0,
            "ipv4": wlan_ipv4(),
        },
        "bluetooth": {
            "powered": bluetooth_powered(),
            "devices": bluetooth_devices(True),
        },
        "wired_controllers": wired_controllers(),
    }


def wifi_connect(payload: dict[str, Any]) -> dict[str, Any]:
    ssid = str(payload.get("ssid") or "").strip()
    password = str(payload.get("password") or "")
    if not ssid or len(ssid.encode("utf-8")) > 32:
        raise HelperError("Choose a valid Wi-Fi network name.")
    if len(password) > 128:
        raise HelperError("The Wi-Fi password is too long.")
    if "\n" in password or "\r" in password or "\x00" in password:
        raise HelperError("The Wi-Fi password contains an unsupported character.")
    arguments = ["nmcli", "--wait", "40", "device", "wifi", "connect", ssid]
    if password:
        arguments.extend(["password", password])
    arguments.extend(["ifname", "wlan0"])
    command(arguments, timeout=45)
    return {"wifi": status()["wifi"]}


def bluetooth_scan() -> dict[str, Any]:
    command(["bluetoothctl", "power", "on"])
    try:
        command(["bluetoothctl", "--timeout", "10", "scan", "on"], timeout=14)
    except HelperError:
        # BlueZ may return a timeout status after completing the requested scan.
        pass
    return {"devices": bluetooth_devices(False)}


def bluetooth_pair(payload: dict[str, Any]) -> dict[str, Any]:
    address = str(payload.get("address") or "").strip().upper()
    if not MAC_PATTERN.fullmatch(address):
        raise HelperError("Choose a valid Bluetooth device.")
    command(["bluetoothctl", "power", "on"])
    existing = bluetooth_info(address, address)
    if not existing["paired"]:
        pair_output = command(
            ["bluetoothctl", "--agent=NoInputNoOutput", "pair", address], timeout=32
        )
        if "failed" in pair_output.lower():
            raise HelperError("Bluetooth pairing failed. Put the controller in pairing mode and try again.")
    command(["bluetoothctl", "trust", address])
    command(["bluetoothctl", "connect", address], timeout=18)
    return {"device": bluetooth_info(address, address)}


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read(16_385)
    if len(raw) > 16_384:
        raise HelperError("The request is too large.")
    try:
        payload = json.loads(raw or "{}")
    except (TypeError, ValueError) as exc:
        raise HelperError("The request data is invalid.") from exc
    if not isinstance(payload, dict):
        raise HelperError("The request data is invalid.")
    return payload


def main() -> int:
    if len(sys.argv) != 2:
        raise HelperError("A system action is required.")
    action = sys.argv[1]
    payload = read_payload()
    if action == "status":
        result = status()
    elif action == "wifi-scan":
        result = {"networks": wifi_networks(True)}
    elif action == "wifi-connect":
        result = wifi_connect(payload)
    elif action == "bluetooth-scan":
        result = bluetooth_scan()
    elif action == "bluetooth-pair":
        result = bluetooth_pair(payload)
    else:
        raise HelperError("That system action is not allowed.")
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HelperError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, separators=(",", ":")))
        raise SystemExit(1)
