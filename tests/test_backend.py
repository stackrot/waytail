from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

from waytail import backend


def status_data() -> dict[str, object]:
    return {
        "BackendState": "Running",
        "Self": {
            "HostName": "desktop",
            "DNSName": "desktop.example.ts.net.",
            "TailscaleIPs": ["100.64.0.1", "fd7a:115c:a1e0::1"],
            "OS": "linux",
            "Online": True,
        },
        "Peer": {
            "device": {
                "ID": "device",
                "HostName": "laptop",
                "DNSName": "laptop.example.ts.net.",
                "TailscaleIPs": ["100.64.0.2"],
                "OS": "linux",
                "Online": True,
                "CurAddr": "192.0.2.2:41641",
                "Relay": "lhr",
                "RxBytes": 2048,
                "TxBytes": 1024,
                "LastHandshake": "2026-08-02T18:00:00Z",
            },
            "tailnet-exit": {
                "ID": "tailnet-exit",
                "HostName": "server",
                "DNSName": "server.example.ts.net.",
                "TailscaleIPs": ["100.64.0.3"],
                "OS": "linux",
                "Online": True,
                "ExitNodeOption": True,
            },
            "located-device": {
                "ID": "located-device",
                "HostName": "located",
                "TailscaleIPs": "invalid",
                "OS": "linux",
                "Online": False,
                "Location": {"Country": "United Kingdom"},
            },
            "london-a": {
                "ID": "london-a",
                "HostName": "london-a",
                "TailscaleIPs": ["100.64.0.4"],
                "Online": True,
                "ExitNodeOption": True,
                "ExitNode": True,
                "Location": {
                    "Country": "United Kingdom",
                    "CountryCode": "GB",
                    "City": "London, GB",
                    "Priority": 0,
                },
            },
            "london-b": {
                "ID": "london-b",
                "HostName": "london-b",
                "TailscaleIPs": ["100.64.0.5"],
                "Online": True,
                "ExitNodeOption": True,
                "Location": {
                    "Country": "United Kingdom",
                    "CountryCode": "GB",
                    "City": "London, GB",
                    "Priority": 1,
                },
            },
            "edinburgh": {
                "ID": "edinburgh",
                "HostName": "edinburgh",
                "TailscaleIPs": ["100.64.0.6"],
                "Online": True,
                "ExitNodeOption": True,
                "Location": {
                    "Country": "United Kingdom",
                    "CountryCode": "GB",
                    "City": "Edinburgh, GB",
                    "Priority": 0,
                },
            },
        },
    }


class StatusTests(unittest.TestCase):
    @patch("waytail.backend.run_tailscale")
    def test_status_groups_provider_exits_and_keeps_tailnet_exits_as_devices(self, run) -> None:
        run.return_value = json.dumps(status_data())

        status = backend.load_status()

        self.assertEqual(
            [device.hostname for device in status.devices],
            ["laptop", "server", "located"],
        )
        self.assertEqual(status.devices[0].cur_addr, "192.0.2.2:41641")
        self.assertEqual(status.devices[2].ips, ())
        self.assertEqual(len(status.exit_nodes), 3)
        london = next(node for node in status.exit_nodes if node.city == "London")
        edinburgh = next(node for node in status.exit_nodes if node.city == "Edinburgh")
        tailnet = next(node for node in status.exit_nodes if node.country == "Tailnet")
        self.assertEqual((london.count, london.priority, london.ip), (2, 1, "100.64.0.5"))
        self.assertEqual(edinburgh.priority, 0)
        self.assertEqual(tailnet.label, "server")
        self.assertEqual(status.active_exit_node, london)

    @patch("waytail.backend.run_tailscale", return_value="[]")
    def test_status_rejects_non_object_json(self, _run) -> None:
        with self.assertRaisesRegex(backend.WaytailError, "expected a JSON object"):
            backend.load_status()

    @patch("waytail.backend.load_status")
    def test_waybar_payload_escapes_errors(self, load) -> None:
        load.side_effect = backend.WaytailError("failed <now>")

        payload = backend.waybar_payload()

        self.assertEqual(payload["text"], "")
        self.assertIn("failed &lt;now&gt;", payload["tooltip"])
        self.assertEqual(payload["class"], ["error", "disconnected"])

    @patch("waytail.backend.run_tailscale")
    def test_exit_selection_does_not_enable_lan_access(self, run) -> None:
        backend.set_exit_node("100.64.0.3")

        run.assert_called_once_with("set", "--exit-node=100.64.0.3", timeout=60)

    def test_relative_time_accepts_naive_timestamps(self) -> None:
        self.assertNotEqual(backend.relative_time("2026-08-02T18:00:00"), "—")

    def test_flag_rejects_non_ascii_country_codes(self) -> None:
        self.assertEqual(backend.flag("éé"), "")


class CommandTests(unittest.TestCase):
    @patch("waytail.backend.subprocess.run")
    def test_tailscale_uses_expected_executable(self, run) -> None:
        run.return_value = SimpleNamespace(returncode=0, stdout="ok", stderr="")

        self.assertEqual(backend.run_tailscale("status"), "ok")

        self.assertEqual(run.call_args.args[0], ["/usr/bin/tailscale", "status"])

    @patch("waytail.backend.subprocess.run")
    def test_tailscale_surfaces_command_errors(self, run) -> None:
        run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="not logged in\n")

        with self.assertRaisesRegex(backend.WaytailError, "not logged in"):
            backend.run_tailscale("status")

    @patch("waytail.backend.subprocess.run", side_effect=subprocess.TimeoutExpired("tailscale", 1))
    def test_tailscale_surfaces_timeouts(self, _run) -> None:
        with self.assertRaises(backend.WaytailError):
            backend.run_tailscale("status")


class WaybarSignalTests(unittest.TestCase):
    def test_signal_uses_configured_realtime_offset(self) -> None:
        with patch.dict(os.environ, {"WAYTAIL_WAYBAR_SIGNAL": "5"}):
            self.assertEqual(backend._waybar_signal(), int(signal.SIGRTMIN) + 5)

    def test_signal_rejects_invalid_offset(self) -> None:
        with (
            patch.dict(os.environ, {"WAYTAIL_WAYBAR_SIGNAL": "invalid"}),
            self.assertRaises(backend.WaytailError),
        ):
            backend._waybar_signal()

    def test_process_discovery_only_returns_current_users_waybar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / "101").mkdir()
            (proc / "101" / "comm").write_text("waybar\n", encoding="utf-8")
            (proc / "102").mkdir()
            (proc / "102" / "comm").write_text("python\n", encoding="utf-8")
            (proc / "self").mkdir()

            self.assertEqual(backend._waybar_pids(proc), (101,))

    @patch("waytail.backend.os.kill")
    @patch("waytail.backend._waybar_pids", return_value=(101, 102))
    def test_refresh_signals_every_waybar_process(self, _pids, kill) -> None:
        with patch.dict(os.environ, {"WAYTAIL_WAYBAR_SIGNAL": "8"}):
            count = backend.refresh_waybar()

        expected_signal = int(signal.SIGRTMIN) + 8
        self.assertEqual(
            kill.call_args_list,
            [call(101, expected_signal), call(102, expected_signal)],
        )
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
