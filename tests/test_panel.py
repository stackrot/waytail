from __future__ import annotations

import json
import unittest
from concurrent.futures import Future
from dataclasses import replace
from unittest.mock import Mock, patch

from tests.test_backend import status_data
from waytail import backend

try:
    from waytail import panel
except (ImportError, ValueError):
    panel = None


@unittest.skipIf(panel is None, "GTK4 and gtk4-layer-shell are required")
class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not panel.Gtk.init_check():
            raise unittest.SkipTest("A GTK display is required")
        cls.application = panel.Gtk.Application(
            application_id="com.stackrot.waytail.tests",
            flags=panel.Gio.ApplicationFlags.NON_UNIQUE,
        )
        cls.application.register(None)

    def setUp(self) -> None:
        for patcher in (
            patch.object(panel, "Gtk4LayerShell"),
            patch.object(panel.GLib, "timeout_add_seconds"),
            patch.object(panel.GLib, "idle_add"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.executor = Mock()
        self.task = Future()
        self.executor.submit.return_value = self.task
        self.window = panel.WaytailWindow(self.application, self.executor)
        self.addCleanup(self.window.destroy)
        with patch("waytail.backend.run_tailscale", return_value=json.dumps(status_data())):
            self.window.status = backend.load_status()
        self.window._render()

    def assert_controls_enabled(self) -> None:
        for button in (
            self.window.connection_button,
            self.window.refresh_button,
            self.window.clear_exit_button,
            self.window.direct_button,
            self.window.exits_list,
        ):
            self.assertTrue(button.get_sensitive())
        self.assertFalse(self.window.spinner.get_spinning())

    def test_failed_action_restores_exit_controls_and_allows_retry(self) -> None:
        self.window._run_action(backend.clear_exit_node)
        self.assertFalse(self.window.clear_exit_button.get_sensitive())
        self.assertFalse(self.window.exits_list.get_sensitive())
        self.task.set_exception(backend.WaytailError("Access denied"))

        self.window._finish_action(self.task)

        self.assert_controls_enabled()
        self.assertEqual(self.window.error.get_text(), "Access denied")
        self.executor.submit.return_value = Future()
        self.window.clear_exit_button.emit("clicked")
        self.assertEqual(self.executor.submit.call_count, 2)

    @patch("waytail.panel.refresh_waybar")
    def test_failed_refresh_after_action_restores_controls(self, _refresh) -> None:
        self.window._run_action(backend.clear_exit_node)
        self.task.set_result(None)
        refresh = Future()
        self.executor.submit.return_value = refresh
        self.window._finish_action(self.task)
        refresh.set_exception(backend.WaytailError("Daemon unavailable"))

        self.window._finish_refresh(refresh)

        self.assert_controls_enabled()
        self.assertEqual(self.window.error.get_text(), "Daemon unavailable")

    def test_action_cannot_overlap_a_status_refresh(self) -> None:
        self.window.refresh()

        self.window._run_action(backend.disconnect)

        self.executor.submit.assert_called_once_with(panel.load_status)
        self.assertFalse(self.window.connection_button.get_sensitive())

    def test_refresh_preserves_expansion_when_devices_move_or_change(self) -> None:
        devices = self.window.status.devices
        self.window.devices_list.get_row_at_index(0).get_child().set_expanded(True)
        updated = replace(devices[0], hostname="renamed-laptop", rx=8192)
        self.window.status = replace(self.window.status, devices=(*devices[1:], updated))

        self.window._render()

        first = self.window.devices_list.get_row_at_index(0).get_child()
        last = self.window.devices_list.get_row_at_index(len(devices) - 1).get_child()
        self.assertFalse(first.get_expanded())
        self.assertTrue(last.get_expanded())
        self.assertIn("8.0 KiB", last.get_child().get_child_at(1, 4).get_text())

    def test_expansion_is_forgotten_when_device_disappears(self) -> None:
        status = self.window.status
        self.window.devices_list.get_row_at_index(0).get_child().set_expanded(True)
        self.window.status = replace(status, devices=())
        self.window._render()

        self.window.status = status
        self.window._render()

        self.assertFalse(self.window.devices_list.get_row_at_index(0).get_child().get_expanded())
