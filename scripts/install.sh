#!/usr/bin/env bash

set -euo pipefail

project_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
data_dir=${XDG_DATA_HOME:-"$HOME/.local/share"}
bin_dir=${XDG_BIN_HOME:-"$HOME/.local/bin"}
install_dir="$data_dir/waytail"
venv_dir="$install_dir/venv"
executable="$bin_dir/waytail"
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
"$venv_dir/bin/python" -c "from waytail.panel import WaytailApplication"
install -Dm0644 "$project_dir/waytail/resources/waytail-symbolic.svg" \
    "$data_dir/icons/hicolor/scalable/apps/waytail-symbolic.svg"
mkdir -p "$bin_dir"
ln -sfn "$venv_dir/bin/waytail" "$executable"

echo "Installed Waytail to $executable"
