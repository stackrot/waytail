# barScale

barScale is a Waybar-first Tailscale controller with a GTK4 layer-shell panel.

It provides:

- a JSON Waybar status module;
- connection and active exit-node state;
- expandable device details and traffic totals;
- searchable, geographically grouped exit nodes;
- connect, disconnect, select and clear exit-node actions;
- a Catppuccin Mocha GTK panel anchored to the active monitor.

## Commands

```text
barscale waybar
barscale panel
barscale status
barscale connect
barscale disconnect
barscale set-exit ADDRESS
barscale clear-exit
```

## Runtime dependencies

- Python 3
- PyGObject
- GTK4
- gtk4-layer-shell
- Tailscale
- Waybar
