#!/usr/bin/env bash

set -euo pipefail

project_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
data_dir=${XDG_DATA_HOME:-"$HOME/.local/share"}
config_dir=${XDG_CONFIG_HOME:-"$HOME/.config"}
bin_dir=${XDG_BIN_HOME:-"$HOME/.local/bin"}
install_dir="$data_dir/waytail"
venv_dir="$install_dir/venv"
executable="$bin_dir/waytail"
icon_dir="$config_dir/waybar/waytail"
python_bin=${PYTHON:-python3}

for path in /usr/bin/hyprctl /usr/bin/tailscale; do
    if [[ ! -x "$path" ]]; then
        echo "Required executable not found: $path" >&2
        exit 1
    fi
done

if ! command -v "$python_bin" >/dev/null 2>&1; then
    echo "Python not found: $python_bin" >&2
    exit 1
fi

if [[ -e "$executable" && ! -L "$executable" ]]; then
    echo "Refusing to replace non-symlink: $executable" >&2
    exit 1
fi

"$python_bin" -m venv --system-site-packages "$venv_dir"
"$venv_dir/bin/python" -m pip install --disable-pip-version-check --no-deps --upgrade "$project_dir"
"$venv_dir/bin/python" -I -c "from waytail.panel import WaytailApplication"
for state in connected disconnected error exit; do
    install -Dm0644 "$project_dir/waytail/resources/tailscale-$state.svg" \
        "$icon_dir/tailscale-$state.svg"
done
mkdir -p "$bin_dir"
ln -sfn "$venv_dir/bin/waytail" "$executable"

echo "Installed Waytail to $executable"
echo "Installed Waybar icons to $icon_dir"
