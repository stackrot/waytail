from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from . import _layer_shell as _layer_shell

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")

from gi.repository import Gdk, Gio, GLib, Gtk, Gtk4LayerShell

from .backend import (
    Device,
    ExitNode,
    TailnetStatus,
    WaytailError,
    clear_exit_node,
    connect,
    disconnect,
    flag,
    format_bytes,
    load_status,
    refresh_waybar,
    relative_time,
    set_exit_node,
)


RESOURCE_ROOT = Path(__file__).with_name("resources")


def _label(text: str = "", css: str | None = None, wrap: bool = False) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_wrap(wrap)
    widget.set_selectable(False)
    if css:
        widget.add_css_class(css)
    return widget


def _clear(box: Gtk.ListBox) -> None:
    row = box.get_row_at_index(0)
    while row is not None:
        box.remove(row)
        row = box.get_row_at_index(0)


def _os_icon(os_name: str) -> str:
    return {
        "android": "phone-symbolic",
        "ios": "phone-symbolic",
        "windows": "computer-symbolic",
        "macos": "computer-symbolic",
        "linux": "computer-symbolic",
    }.get(os_name.lower(), "network-server-symbolic")


class WaytailWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application, executor: ThreadPoolExecutor) -> None:
        super().__init__(application=application)
        self.executor = executor
        self.status: TailnetStatus | None = None
        self.refreshing = False
        self.pending = False
        self.search_text = ""
        self.height_limit = 680
        self.set_title("Waytail")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_size_request(540, -1)

        Gtk4LayerShell.init_for_window(self)
        Gtk4LayerShell.set_namespace(self, "waytail")
        Gtk4LayerShell.set_layer(self, Gtk4LayerShell.Layer.OVERLAY)
        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.TOP, True)
        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.RIGHT, True)
        Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.TOP, 6)
        Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.RIGHT, 8)
        Gtk4LayerShell.set_keyboard_mode(self, Gtk4LayerShell.KeyboardMode.ON_DEMAND)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.content.add_css_class("panel")
        self.set_child(self.content)

        self._build_header()
        self._build_exit_strip()
        self._build_pages()

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        GLib.timeout_add_seconds(30, self._scheduled_refresh)

    def _build_header(self) -> None:
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.add_css_class("header")

        picture = Gtk.Picture.new_for_filename(str(RESOURCE_ROOT / "tailscale.svg"))
        picture.set_size_request(28, 28)
        picture.set_can_shrink(True)
        header.append(picture)

        identity = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        identity.set_hexpand(True)
        self.hostname = _label("Waytail", "heading")
        self.connection = _label("Loading…", "subtle")
        identity.append(self.hostname)
        identity.append(self.connection)
        header.append(identity)

        self.spinner = Gtk.Spinner()
        header.append(self.spinner)

        self.refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.refresh_button.set_tooltip_text("Refresh")
        self.refresh_button.connect("clicked", lambda _button: self.refresh())
        header.append(self.refresh_button)

        self.connection_button = Gtk.Button(label="Connect")
        self.connection_button.add_css_class("connection-button")
        self.connection_button.connect("clicked", self._toggle_connection)
        header.append(self.connection_button)

        close_button = Gtk.Button.new_from_icon_name("window-close-symbolic")
        close_button.set_tooltip_text("Close")
        close_button.connect("clicked", lambda _button: self.close())
        header.append(close_button)

        self.content.append(header)
        self.error = _label("", "error", True)
        self.error.set_visible(False)
        self.content.append(self.error)

    def _build_exit_strip(self) -> None:
        self.exit_revealer = Gtk.Revealer()
        self.exit_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.exit_revealer.connect(
            "notify::child-revealed", lambda *_args: self._resize_lists()
        )
        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        strip.add_css_class("active-exit")
        self.active_exit_label = _label()
        self.active_exit_label.set_hexpand(True)
        strip.append(self.active_exit_label)
        self.clear_exit_button = Gtk.Button(label="Disable")
        self.clear_exit_button.connect(
            "clicked", lambda _button: self._run_action(clear_exit_node)
        )
        strip.append(self.clear_exit_button)
        self.exit_revealer.set_child(strip)
        self.content.append(self.exit_revealer)

    def _build_pages(self) -> None:
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vhomogeneous(False)
        self.stack.set_interpolate_size(True)
        self.stack.set_vexpand(True)

        self.devices_list = Gtk.ListBox()
        self.devices_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.devices_list.add_css_class("content-list")
        devices_scroll = Gtk.ScrolledWindow()
        devices_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        devices_scroll.set_propagate_natural_height(True)
        devices_scroll.set_max_content_height(480)
        devices_scroll.set_child(self.devices_list)
        self.devices_page = devices_scroll
        self.stack.add_titled(devices_scroll, "devices", "Devices")

        exits_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.exit_search = Gtk.SearchEntry(placeholder_text="Filter by country or city…")
        self.exit_search.connect("search-changed", self._search_changed)
        exits_box.append(self.exit_search)

        self.direct_button = Gtk.Button(label="Direct connection (no exit node)")
        self.direct_button.add_css_class("direct")
        self.direct_button.connect(
            "clicked", lambda _button: self._run_action(clear_exit_node)
        )
        exits_box.append(self.direct_button)

        self.exits_list = Gtk.ListBox()
        self.exits_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.exits_list.add_css_class("content-list")
        self.exits_list.set_filter_func(self._filter_exit)
        self.exits_list.set_header_func(self._header_exit)
        exits_scroll = Gtk.ScrolledWindow()
        exits_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        exits_scroll.set_propagate_natural_height(True)
        exits_scroll.set_max_content_height(480)
        exits_scroll.set_vexpand(True)
        exits_scroll.set_child(self.exits_list)
        self.exits_scroll = exits_scroll
        exits_box.append(exits_scroll)
        self.exits_page = exits_box
        self.stack.add_titled(exits_box, "exits", "Exit nodes")

        switcher = Gtk.StackSwitcher(stack=self.stack)
        switcher.set_halign(Gtk.Align.CENTER)
        self.content.append(switcher)
        self.content.append(self.stack)

    def present_panel(self) -> None:
        self._move_to_active_monitor()
        self.present()
        self.refresh()

    def _move_to_active_monitor(self) -> None:
        try:
            result = subprocess.run(
                ["/usr/bin/hyprctl", "-j", "monitors"],
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            )
            raw_monitors = json.loads(result.stdout)
            if not isinstance(raw_monitors, list):
                return
            monitors = [
                monitor
                for monitor in raw_monitors
                if isinstance(monitor, dict) and isinstance(monitor.get("name"), str)
            ]
            if not monitors:
                return
            connector = next(
                (monitor["name"] for monitor in monitors if monitor.get("focused")),
                monitors[0]["name"],
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            return
        display = Gdk.Display.get_default()
        if display is None:
            return
        display_monitors = display.get_monitors()
        for index in range(display_monitors.get_n_items()):
            monitor = display_monitors.get_item(index)
            if monitor.get_connector() == connector:
                Gtk4LayerShell.set_monitor(self, monitor)
                self.height_limit = min(680, monitor.get_geometry().height * 3 // 4)
                self._resize_lists()
                return

    def _resize_lists(self) -> None:
        width = max(self.get_width(), self.content.measure(Gtk.Orientation.HORIZONTAL, -1)[0])
        vertical = Gtk.Orientation.VERTICAL
        chrome = (
            self.content.measure(vertical, width)[1]
            - self.stack.measure(vertical, width)[1]
        )
        for page, scroll in (
            (self.devices_page, self.devices_page),
            (self.exits_page, self.exits_scroll),
        ):
            controls = page.measure(vertical, width)[1] - scroll.measure(vertical, width)[1]
            height = max(1, min(480, self.height_limit - chrome - controls))
            scroll.set_max_content_height(height)

    def refresh(self) -> None:
        if self.refreshing or self.pending:
            return
        self.refreshing = True
        self._update_controls()
        future = self.executor.submit(load_status)
        future.add_done_callback(lambda task: GLib.idle_add(self._finish_refresh, task))

    def _finish_refresh(self, future: Future[TailnetStatus]) -> bool:
        self.refreshing = False
        self._update_controls()
        try:
            self.status = future.result()
        except Exception as error:
            self._show_error(str(error))
            return GLib.SOURCE_REMOVE
        self._show_error("")
        self._render()
        return GLib.SOURCE_REMOVE

    def _render(self) -> None:
        if self.status is None:
            return
        status = self.status
        self.hostname.set_text(status.self_node.hostname or "Waytail")
        if status.running:
            connection = "Connected"
            if status.self_node.ip:
                connection += f" · {status.self_node.ip}"
        else:
            connection = status.backend_state
        self.connection.set_text(connection)
        self.connection_button.set_label("Disconnect" if status.running else "Connect")

        active = status.active_exit_node
        self.exit_revealer.set_reveal_child(active is not None)
        if active:
            prefix = flag(active.country_code)
            self.active_exit_label.set_text(
                f"{prefix}  Exit node · {active.label}" if prefix else f"Exit node · {active.label}"
            )
        if active is None:
            self.direct_button.add_css_class("active")
        else:
            self.direct_button.remove_css_class("active")

        self._render_devices(status.devices)
        self._render_exits(status.exit_nodes)
        page = self.stack.get_page(self.devices_page)
        page.set_title(f"Devices ({len(status.devices)})")
        self._update_controls()
        self._resize_lists()

    def _render_devices(self, devices: tuple[Device, ...]) -> None:
        _clear(self.devices_list)
        if not devices:
            row = Gtk.ListBoxRow()
            row.set_child(_label("No devices", "empty"))
            self.devices_list.append(row)
            return
        for device in devices:
            self.devices_list.append(self._device_row(device))

    def _device_row(self, device: Device) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.add_css_class("device-row")
        expander = Gtk.Expander()

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        dot = Gtk.Box()
        dot.add_css_class("status-dot")
        dot.add_css_class("online" if device.online else "offline")
        header.append(dot)
        header.append(Gtk.Image.new_from_icon_name(_os_icon(device.os)))
        name = _label(device.hostname, "device-name")
        name.set_hexpand(True)
        header.append(name)
        ip = _label(device.ip, "subtle")
        header.append(ip)
        expander.set_label_widget(header)

        details = Gtk.Grid(column_spacing=12, row_spacing=5)
        details.set_margin_start(28)
        details.set_margin_top(8)
        details.set_margin_bottom(8)
        if not device.online:
            connection = relative_time(device.last_seen)
        elif device.cur_addr:
            connection = f"Direct · {device.cur_addr}"
        elif device.peer_relay:
            connection = f"Peer relay · {device.peer_relay}"
        elif device.relay:
            connection = f"DERP relay · {device.relay}"
        else:
            handshake = relative_time(device.last_handshake)
            connection = "Idle" if handshake in ("—", "never") else f"Idle · {handshake}"
        values = (
            ("OS", device.os or "—"),
            ("DNS", device.dns or "—"),
            ("Addresses", "\n".join(device.ips) or "—"),
            ("Connection" if device.online else "Last seen", connection),
            ("Traffic", f"↓ {format_bytes(device.rx)}   ↑ {format_bytes(device.tx)}"),
        )
        for index, (name_text, value_text) in enumerate(values):
            details.attach(_label(name_text, "detail-key"), 0, index, 1, 1)
            value = _label(value_text, "detail-value", True)
            value.set_selectable(True)
            details.attach(value, 1, index, 1, 1)

        copy_button = Gtk.Button(label="Copy IP")
        copy_button.set_halign(Gtk.Align.START)
        copy_button.set_sensitive(bool(device.ip))
        copy_button.connect("clicked", lambda _button: self._copy(device.ip))
        details.attach(copy_button, 1, len(values), 1, 1)
        expander.set_child(details)
        row.set_child(expander)
        return row

    def _render_exits(self, exits: tuple[ExitNode, ...]) -> None:
        _clear(self.exits_list)
        for exit_node in exits:
            row = Gtk.ListBoxRow()
            row._waytail_country = exit_node.country
            row._waytail_search = (
                f"{exit_node.country} {exit_node.city} {exit_node.hostname}"
            ).casefold()
            button = Gtk.Button()
            button.add_css_class("exit-row")
            button.set_sensitive(exit_node.online or exit_node.active)
            if exit_node.active:
                button.add_css_class("active")
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            country_flag = flag(exit_node.country_code)
            if country_flag:
                line.append(_label(country_flag, "flag"))
            city = _label(exit_node.city or exit_node.hostname, "exit-name")
            city.set_hexpand(True)
            line.append(city)
            if exit_node.count > 1:
                line.append(_label(f"{exit_node.count} servers", "subtle"))
            dot = Gtk.Box()
            dot.add_css_class("status-dot")
            dot.add_css_class("online" if exit_node.online else "offline")
            line.append(dot)
            if exit_node.active:
                line.append(Gtk.Image.new_from_icon_name("object-select-symbolic"))
            button.set_child(line)
            button.connect("clicked", self._select_exit, exit_node)
            row.set_child(button)
            self.exits_list.append(row)
        self.exits_list.invalidate_filter()
        self.exits_list.invalidate_headers()

    def _select_exit(self, _button: Gtk.Button, exit_node: ExitNode) -> None:
        if exit_node.active:
            self._run_action(clear_exit_node)
        else:
            self._run_action(set_exit_node, exit_node.ip)

    def _run_action(self, operation: Callable[..., None], *args: str) -> None:
        if self.pending or self.refreshing:
            return
        self.pending = True
        self._update_controls()
        future = self.executor.submit(operation, *args)
        future.add_done_callback(lambda task: GLib.idle_add(self._finish_action, task))

    def _finish_action(self, future: Future[None]) -> bool:
        self.pending = False
        self._update_controls()
        try:
            future.result()
        except Exception as error:
            self._show_error(str(error))
            return GLib.SOURCE_REMOVE
        try:
            refresh_waybar()
        except WaytailError as error:
            self._show_error(str(error))
        self.refresh()
        return GLib.SOURCE_REMOVE

    def _update_controls(self) -> None:
        busy = self.pending or self.refreshing
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()
        self.connection_button.set_sensitive(not busy)
        self.refresh_button.set_sensitive(not busy)
        self.exits_list.set_sensitive(not busy)
        active = self.status is not None and self.status.active_exit_node is not None
        self.clear_exit_button.set_sensitive(active and not busy)
        self.direct_button.set_sensitive(active and not busy)

    def _toggle_connection(self, _button: Gtk.Button) -> None:
        if self.status and self.status.running:
            self._run_action(disconnect)
        else:
            self._run_action(connect)

    def _copy(self, value: str) -> None:
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(value)

    def _search_changed(self, search: Gtk.SearchEntry) -> None:
        self.search_text = search.get_text().strip().casefold()
        self.exits_list.invalidate_filter()
        self.exits_list.invalidate_headers()

    def _filter_exit(self, row: Gtk.ListBoxRow) -> bool:
        return not self.search_text or self.search_text in row._waytail_search

    def _header_exit(self, row: Gtk.ListBoxRow, before: Gtk.ListBoxRow | None) -> None:
        previous_country = before._waytail_country if before is not None else None
        if row._waytail_country == previous_country:
            row.set_header(None)
            return
        header = _label(row._waytail_country, "country")
        row.set_header(header)

    def _show_error(self, message: str) -> None:
        self.error.set_text(message)
        self.error.set_visible(bool(message))
        self._resize_lists()

    def _scheduled_refresh(self) -> bool:
        if self.get_visible():
            self.refresh()
        return GLib.SOURCE_CONTINUE

    def _on_key(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False


class WaytailApplication(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id="com.stackrot.waytail",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.window: WaytailWindow | None = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="waytail")

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_path(str(RESOURCE_ROOT / "style.css"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def do_activate(self) -> None:
        if self.window is None:
            self.window = WaytailWindow(self, self.executor)
            self.window.present_panel()
        elif self.window.get_visible():
            self.window.close()
        else:
            self.window.present_panel()

    def do_shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
        Gtk.Application.do_shutdown(self)


def run_panel() -> int:
    return WaytailApplication().run([])
