from __future__ import annotations

import json
import os
import subprocess
import unittest
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from tests.test_backend import status_data
from waytail import backend

try:
    from waytail import panel
except (ImportError, ValueError):
    if os.environ.get("WAYTAIL_REQUIRE_GTK_TESTS") == "1":
        raise
    panel = None


@unittest.skipIf(panel is None, "GTK4 and gtk4-layer-shell are required")
class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not panel.Gtk.init_check():
            if os.environ.get("WAYTAIL_REQUIRE_GTK_TESTS") == "1":
                raise RuntimeError("The GTK test job requires a display")
            raise unittest.SkipTest("A GTK display is required")
        cls.application = panel.WaytailApplication()
        cls.application.set_application_id("com.stackrot.waytail.tests")
        cls.application.set_flags(panel.Gio.ApplicationFlags.NON_UNIQUE)
        cls.addClassCleanup(cls.application.executor.shutdown, wait=False, cancel_futures=True)
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

    def test_panel_uses_user_palette_colours(self) -> None:
        provider = panel.Gtk.CssProvider()
        display = panel.Gdk.Display.get_default()
        panel.Gtk.StyleContext.add_provider_for_display(
            display, provider, panel.Gtk.STYLE_PROVIDER_PRIORITY_USER + 1
        )
        self.addCleanup(panel.Gtk.StyleContext.remove_provider_for_display, display, provider)
        with TemporaryDirectory() as directory:
            stylesheet = Path(directory) / "theme.css"
            for colour in ("#243040", "#e0e8f0"):
                with self.subTest(colour=colour):
                    stylesheet.write_text(f"@define-color theme_fg_color {colour};\n")
                    provider.load_from_path(str(stylesheet))
                    expected = panel.Gdk.RGBA()
                    expected.parse(colour)
                    window = panel.WaytailWindow(self.application, self.executor)
                    self.addCleanup(window.destroy)

                    self.assertTrue(window.content.get_color().equal(expected))
                    self.assertTrue(window.hostname.get_color().equal(expected))

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

    def test_refresh_updates_connection_state_and_clears_previous_error(self) -> None:
        self.window._show_error("Previous failure")
        self.window.refresh()
        status = replace(self.window.status, backend_state="Stopped", active_exit_node=None)
        self.task.set_result(status)

        self.window._finish_refresh(self.task)

        self.assertEqual(self.window.connection_button.get_label(), "Connect")
        self.assertFalse(self.window.clear_exit_button.get_sensitive())
        self.assertFalse(self.window.error.get_visible())
        self.assertFalse(self.window.spinner.get_spinning())

    def test_exit_buttons_select_candidates_and_clear_active_groups(self) -> None:
        for index, node in enumerate(self.window.status.exit_nodes):
            with self.subTest(node=node.key), patch.object(self.window, "_run_action") as action:
                self.window.exits_list.get_row_at_index(index).get_child().emit("clicked")

                if node.active:
                    action.assert_called_once_with(panel.clear_exit_node)
                else:
                    action.assert_called_once_with(panel.set_exit_node, node.ip)

    def test_connection_button_uses_latest_connection_state(self) -> None:
        with patch.object(self.window, "_run_action") as action:
            self.window.connection_button.emit("clicked")
            action.assert_called_once_with(panel.disconnect)
            action.reset_mock()
            self.window.status = replace(self.window.status, backend_state="Stopped")

            self.window.connection_button.emit("clicked")

            action.assert_called_once_with(panel.connect)

    def test_filter_is_case_insensitive_and_updates_country_headers(self) -> None:
        search = Mock()
        search.get_text.return_value = "  LONDON  "

        self.window._search_changed(search)

        matching = []
        row = self.window.exits_list.get_row_at_index(0)
        while row is not None:
            if self.window._filter_exit(row):
                matching.append(row)
            row = row.get_next_sibling()
            while row is not None and not isinstance(row, panel.Gtk.ListBoxRow):
                row = row.get_next_sibling()
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].get_header().get_text(), "United Kingdom")

    def test_list_sizes_follow_content_and_respect_monitor_limit(self) -> None:
        self.window.status = replace(self.window.status, active_exit_node=None)
        self.window.height_limit = 405
        self.window._render()
        vertical = panel.Gtk.Orientation.VERTICAL
        width = self.window.measure(panel.Gtk.Orientation.HORIZONTAL, -1)[1]
        small = self.window.measure(vertical, width)[1]
        devices = self.window.status.devices
        many = tuple(replace(devices[0], id=f"device-{i}") for i in range(100))
        self.window.status = replace(self.window.status, devices=many)
        self.window._render()

        large = self.window.measure(vertical, width)[1]

        self.assertGreater(large, small)
        self.assertLessEqual(large, self.window.height_limit)
        self.window.status = replace(self.window.status, devices=devices)
        self.window._render()
        self.assertEqual(self.window.measure(vertical, width)[1], small)

    def test_exit_list_does_not_set_device_page_height(self) -> None:
        self.window.status = replace(self.window.status, active_exit_node=None)
        self.window._render()
        vertical = panel.Gtk.Orientation.VERTICAL
        width = self.window.measure(panel.Gtk.Orientation.HORIZONTAL, -1)[1]
        devices_height = self.window.measure(vertical, width)[1]
        node = self.window.status.exit_nodes[0]
        exits = tuple(replace(node, key=f"exit-{i}") for i in range(100))
        self.window.status = replace(self.window.status, exit_nodes=exits)
        self.window._render()
        self.assertEqual(self.window.measure(vertical, width)[1], devices_height)

        self.window.stack.set_visible_child_name("exits")

        self.assertLessEqual(self.window.measure(vertical, width)[1], self.window.height_limit)
        self.window.stack.set_visible_child_name("devices")
        self.assertEqual(self.window.measure(vertical, width)[1], devices_height)

    @patch("waytail.panel.subprocess.run")
    def test_monitor_selection_uses_focused_output_and_logical_height(self, run) -> None:
        run.return_value = Mock(stdout=json.dumps([
            {"name": "other", "focused": False},
            {"name": "focused", "focused": True},
        ]))
        monitor = Mock()
        monitor.get_connector.return_value = "focused"
        monitor.get_geometry.return_value = Mock(height=540)
        monitors = Mock()
        monitors.get_n_items.return_value = 1
        monitors.get_item.return_value = monitor
        display = Mock()
        display.get_monitors.return_value = monitors
        with patch.object(panel.Gdk.Display, "get_default", return_value=display):
            self.window._move_to_active_monitor()

        panel.Gtk4LayerShell.set_monitor.assert_called_once_with(self.window, monitor)
        self.assertEqual(self.window.height_limit, 405)

    @patch("waytail.panel.subprocess.run")
    def test_monitor_discovery_failure_keeps_panel_usable(self, run) -> None:
        run.side_effect = subprocess.TimeoutExpired("hyprctl", 3)

        self.window._move_to_active_monitor()

        self.assert_controls_enabled()
        panel.Gtk4LayerShell.set_monitor.assert_not_called()
