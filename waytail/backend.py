from __future__ import annotations

import html
import json
import os
import re
import signal
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class WaytailError(RuntimeError):
    pass


_WAYBAR_ANCHOR = "\u200b"


@dataclass(frozen=True, slots=True)
class SelfNode:
    hostname: str
    ip: str
    ips: tuple[str, ...]
    os: str
    online: bool
    dns: str


@dataclass(frozen=True, slots=True)
class Device:
    id: str
    hostname: str
    dns: str
    ip: str
    ips: tuple[str, ...]
    os: str
    online: bool
    cur_addr: str
    relay: str
    peer_relay: str
    rx: int
    tx: int
    last_handshake: str
    last_seen: str


@dataclass(frozen=True, slots=True)
class ExitNode:
    key: str
    country: str
    country_code: str
    city: str
    hostname: str
    ip: str
    online: bool
    count: int
    priority: int
    active: bool

    @property
    def label(self) -> str:
        if self.city and self.country:
            return f"{self.city}, {self.country}"
        return self.hostname or self.ip


@dataclass(frozen=True, slots=True)
class TailnetStatus:
    backend_state: str
    self_node: SelfNode
    devices: tuple[Device, ...]
    exit_nodes: tuple[ExitNode, ...]
    active_exit_node: ExitNode | None

    @property
    def running(self) -> bool:
        return self.backend_state == "Running"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ips(node: dict[str, Any]) -> tuple[str, ...]:
    values = node.get("TailscaleIPs") or []
    if not isinstance(values, list):
        return ()
    return tuple(ip for item in values if (ip := _text(item)))


def _first_ip(node: dict[str, Any]) -> str:
    ips = _ips(node)
    return ips[0] if ips else ""


def _integer(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clean_name(hostname: Any, dns_name: Any) -> str:
    host = _text(hostname)
    if host and host.lower() != "localhost":
        return host
    dns = _text(dns_name).rstrip(".")
    return dns.split(".", 1)[0] if dns else host or "(unknown)"


def flag(country_code: str) -> str:
    code = country_code.upper()
    if len(code) != 2 or not code.isascii() or not code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(char) - ord("A")) for char in code)


def format_bytes(value: int) -> str:
    amount = float(value or 0)
    if amount < 1024:
        return f"{int(amount)} B"
    for unit in ("KiB", "MiB", "GiB"):
        amount /= 1024
        if amount < 1024:
            return f"{amount:.1f} {unit}"
    return f"{amount / 1024:.1f} TiB"


def relative_time(value: str) -> str:
    if not value:
        return "—"
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "—"
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    if stamp.year < 2000:
        return "never"
    seconds = max(0, int((datetime.now(timezone.utc) - stamp).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def run_tailscale(*args: str, timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            ["/usr/bin/tailscale", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WaytailError(str(error)) from error
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "tailscale command failed"
        raise WaytailError(message)
    return result.stdout


def _device(raw_peer: dict[str, Any]) -> Device:
    return Device(
        id=_text(raw_peer.get("ID")),
        hostname=clean_name(raw_peer.get("HostName"), raw_peer.get("DNSName")),
        dns=_text(raw_peer.get("DNSName")).rstrip("."),
        ip=_first_ip(raw_peer),
        ips=_ips(raw_peer),
        os=_text(raw_peer.get("OS")),
        online=bool(raw_peer.get("Online")),
        cur_addr=_text(raw_peer.get("CurAddr")),
        relay=_text(raw_peer.get("Relay")),
        peer_relay=_text(raw_peer.get("PeerRelay")),
        rx=_integer(raw_peer.get("RxBytes")),
        tx=_integer(raw_peer.get("TxBytes")),
        last_handshake=_text(raw_peer.get("LastHandshake")),
        last_seen=_text(raw_peer.get("LastSeen")),
    )


def load_status() -> TailnetStatus:
    try:
        data = json.loads(run_tailscale("status", "--json"))
    except json.JSONDecodeError as error:
        raise WaytailError(f"Invalid Tailscale status: {error}") from error
    if not isinstance(data, dict):
        raise WaytailError("Invalid Tailscale status: expected a JSON object")

    raw_self = data.get("Self") or {}
    if not isinstance(raw_self, dict):
        raw_self = {}
    self_node = SelfNode(
        hostname=clean_name(raw_self.get("HostName"), raw_self.get("DNSName")),
        ip=_first_ip(raw_self),
        ips=_ips(raw_self),
        os=_text(raw_self.get("OS")),
        online=bool(raw_self.get("Online")),
        dns=_text(raw_self.get("DNSName")).rstrip("."),
    )

    devices: list[Device] = []
    grouped_exits: dict[str, dict[str, Any]] = {}
    peers = data.get("Peer") or {}
    if not isinstance(peers, dict):
        peers = {}

    for raw_peer in peers.values():
        if not isinstance(raw_peer, dict):
            continue

        exit_option = bool(raw_peer.get("ExitNodeOption"))
        raw_location = raw_peer.get("Location")
        location = raw_location if isinstance(raw_location, dict) else {}
        if not exit_option or not location:
            devices.append(_device(raw_peer))
        if not exit_option:
            continue

        country = _text(location.get("Country")) or "Tailnet"
        country_code = _text(location.get("CountryCode"))
        city = re.sub(r", [A-Z]{2}$", "", _text(location.get("City")))
        hostname = clean_name(raw_peer.get("HostName"), raw_peer.get("DNSName"))
        peer_id = _text(raw_peer.get("ID"))
        key = (
            f"{country_code}|{country}|{city}"
            if location and city
            else f"peer|{peer_id or _first_ip(raw_peer)}"
        )
        online = bool(raw_peer.get("Online"))
        priority = _integer(location.get("Priority"), -1)
        active = bool(raw_peer.get("ExitNode"))
        current = grouped_exits.get(key)

        if current is None:
            grouped_exits[key] = {
                "key": key,
                "country": country,
                "country_code": country_code,
                "city": city,
                "hostname": hostname,
                "ip": _first_ip(raw_peer),
                "online": online,
                "count": 1,
                "priority": priority,
                "active": active,
            }
            continue

        current["count"] += 1
        better = (online and not current["online"]) or (
            online == current["online"] and priority > current["priority"]
        )
        if better:
            current["hostname"] = hostname
            current["ip"] = _first_ip(raw_peer)
            current["priority"] = priority
        current["online"] = current["online"] or online
        current["active"] = current["active"] or active

    devices.sort(key=lambda device: (not device.online, device.hostname.casefold()))
    exit_nodes = [ExitNode(**values) for values in grouped_exits.values()]
    exit_nodes.sort(key=lambda node: (node.country.casefold(), node.city.casefold()))
    active_exit = next((node for node in exit_nodes if node.active), None)

    return TailnetStatus(
        backend_state=_text(data.get("BackendState")) or "Unknown",
        self_node=self_node,
        devices=tuple(devices),
        exit_nodes=tuple(exit_nodes),
        active_exit_node=active_exit,
    )


def waybar_payload() -> dict[str, Any]:
    try:
        status = load_status()
    except WaytailError as error:
        return {
            "text": _WAYBAR_ANCHOR,
            "tooltip": f"<b>Waytail</b>\n{html.escape(str(error))}",
            "class": ["error", "disconnected"],
            "alt": "error",
        }

    online = sum(device.online for device in status.devices)
    classes = ["connected" if status.running else "disconnected"]
    lines = ["<b>Waytail</b>"]
    if status.running:
        connection = "Connected"
        if status.self_node.ip:
            connection += f" · {html.escape(status.self_node.ip)}"
        lines.append(connection)
        lines.append(f"{online}/{len(status.devices)} devices online")
        if status.active_exit_node:
            classes.append("exit-node")
            lines.append(f"Exit node · {html.escape(status.active_exit_node.label)}")
    else:
        lines.append(f"Disconnected · {html.escape(status.backend_state)}")

    return {
        "text": _WAYBAR_ANCHOR,
        "tooltip": "\n".join(lines),
        "class": classes,
        "alt": classes[-1],
    }


def connect() -> None:
    run_tailscale("up", timeout=120)


def disconnect() -> None:
    run_tailscale("down", timeout=60)


def set_exit_node(ip: str) -> None:
    run_tailscale("set", f"--exit-node={ip}", timeout=60)


def clear_exit_node() -> None:
    run_tailscale("set", "--exit-node=", timeout=60)


def _waybar_signal() -> int:
    raw_offset = os.environ.get("WAYTAIL_WAYBAR_SIGNAL", "8")
    try:
        offset = int(raw_offset)
    except ValueError as error:
        raise WaytailError("WAYTAIL_WAYBAR_SIGNAL must be an integer") from error
    maximum = int(signal.SIGRTMAX) - int(signal.SIGRTMIN)
    if not 0 <= offset <= maximum:
        raise WaytailError(f"WAYTAIL_WAYBAR_SIGNAL must be between 0 and {maximum}")
    return int(signal.SIGRTMIN) + offset


def _waybar_pids(proc_root: Path = Path("/proc")) -> tuple[int, ...]:
    try:
        entries = tuple(proc_root.iterdir())
    except OSError:
        return ()
    user_id = os.getuid()
    pids: list[int] = []
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        try:
            if entry.stat().st_uid != user_id:
                continue
            if (entry / "comm").read_text(encoding="utf-8").strip() != "waybar":
                continue
        except OSError:
            continue
        pids.append(int(entry.name))
    return tuple(pids)


def refresh_waybar() -> int:
    signal_number = _waybar_signal()
    signalled = 0
    for pid in _waybar_pids():
        try:
            os.kill(pid, signal_number)
        except OSError:
            continue
        signalled += 1
    return signalled
