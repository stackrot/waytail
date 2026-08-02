from __future__ import annotations

import html
import json
import re
import signal
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


class BarScaleError(RuntimeError):
    pass


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


def _first_ip(node: dict[str, Any]) -> str:
    ips = node.get("TailscaleIPs") or []
    return _text(ips[0]) if ips else ""


def clean_name(hostname: Any, dns_name: Any) -> str:
    host = _text(hostname)
    if host and host.lower() != "localhost":
        return host
    dns = _text(dns_name).rstrip(".")
    return dns.split(".", 1)[0] if dns else host or "(unknown)"


def flag(country_code: str) -> str:
    code = country_code.upper()
    if len(code) != 2 or not code.isalpha():
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
        raise BarScaleError(str(error)) from error
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "tailscale command failed"
        raise BarScaleError(message)
    return result.stdout


def load_status() -> TailnetStatus:
    try:
        data = json.loads(run_tailscale("status", "--json"))
    except json.JSONDecodeError as error:
        raise BarScaleError(f"Invalid Tailscale status: {error}") from error

    raw_self = data.get("Self") or {}
    self_node = SelfNode(
        hostname=clean_name(raw_self.get("HostName"), raw_self.get("DNSName")),
        ip=_first_ip(raw_self),
        ips=tuple(_text(item) for item in raw_self.get("TailscaleIPs") or []),
        os=_text(raw_self.get("OS")),
        online=bool(raw_self.get("Online")),
        dns=_text(raw_self.get("DNSName")).rstrip("."),
    )

    devices: list[Device] = []
    grouped_exits: dict[str, dict[str, Any]] = {}

    for raw_peer in (data.get("Peer") or {}).values():
        if not raw_peer.get("ExitNodeOption"):
            devices.append(
                Device(
                    id=_text(raw_peer.get("ID")),
                    hostname=clean_name(raw_peer.get("HostName"), raw_peer.get("DNSName")),
                    dns=_text(raw_peer.get("DNSName")).rstrip("."),
                    ip=_first_ip(raw_peer),
                    ips=tuple(_text(item) for item in raw_peer.get("TailscaleIPs") or []),
                    os=_text(raw_peer.get("OS")),
                    online=bool(raw_peer.get("Online")),
                    rx=int(raw_peer.get("RxBytes") or 0),
                    tx=int(raw_peer.get("TxBytes") or 0),
                    last_handshake=_text(raw_peer.get("LastHandshake")),
                    last_seen=_text(raw_peer.get("LastSeen")),
                )
            )
            continue

        location = raw_peer.get("Location") or {}
        country = _text(location.get("Country")) or "Other"
        country_code = _text(location.get("CountryCode"))
        city = re.sub(r", [A-Z]{2}$", "", _text(location.get("City")))
        hostname = clean_name(raw_peer.get("HostName"), raw_peer.get("DNSName"))
        peer_id = _text(raw_peer.get("ID"))
        key = f"{country_code}|{country}|{city}" if city else f"peer|{peer_id}"
        online = bool(raw_peer.get("Online"))
        priority = int(location.get("Priority") or -1)
        active = bool(raw_peer.get("ExitNode"))
        current = grouped_exits.get(key)

        if current is None:
            grouped_exits[key] = {
                "key": key,
                "country": country,
                "country_code": country_code,
                "city": city or hostname,
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
    except BarScaleError as error:
        return {
            "text": " ",
            "tooltip": f"<b>barScale</b>\n{html.escape(str(error))}",
            "class": ["error", "disconnected"],
            "alt": "error",
        }

    online = sum(device.online for device in status.devices)
    classes = ["connected" if status.running else "disconnected"]
    lines = ["<b>barScale</b>"]
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
        "text": " ",
        "tooltip": "\n".join(lines),
        "class": classes,
        "alt": classes[-1],
    }


def connect() -> None:
    run_tailscale("up", timeout=120)


def disconnect() -> None:
    run_tailscale("down", timeout=60)


def set_exit_node(ip: str) -> None:
    run_tailscale("set", f"--exit-node={ip}", "--exit-node-allow-lan-access", timeout=60)


def clear_exit_node() -> None:
    run_tailscale("set", "--exit-node=", timeout=60)


def refresh_waybar() -> None:
    try:
        subprocess.run(
            [
                "/usr/bin/systemctl",
                "--user",
                "kill",
                "--kill-whom=main",
                f"--signal={signal.SIGRTMIN + 8}",
                "waybar.service",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
