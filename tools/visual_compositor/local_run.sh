#!/usr/bin/env bash
# Run installed compositors with a private home, runtime directory and session bus.
# Usage: local_run.sh cinnamon-services | native COMPOSITOR [MODE] |
#        matrix COMPOSITOR [--case NAME | --behavior NAME] [--require-supported]
set -euo pipefail
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
scripts="$repo_dir/tools/visual_compositor"
python="${DOCKING_LAB_PYTHON:-$repo_dir/.venv/bin/python}"
mode="${1:?mode required: cinnamon-services, native, matrix}"
shift
compositor=cinnamon
if [ "$mode" != cinnamon-services ]; then
    compositor="${1:?compositor required}"
    shift
fi
case "$mode" in
    cinnamon-services|native|matrix) ;;
    *) echo "Unknown local mode: $mode" >&2; exit 2 ;;
esac
[[ "$compositor" =~ ^[a-z]+$ ]] && [ -f "$scripts/adapters/$compositor.sh" ] \
    || { echo "No compositor adapter: $compositor" >&2; exit 2; }
for command in bwrap dbus-run-session /usr/bin/python3; do
    command -v "$command" >/dev/null
done
test -x "$python"
test "$("$python" -c 'import sys; print(sys.version_info[:2])')" = \
    "$(/usr/bin/python3 -c 'import sys; print(sys.version_info[:2])')" \
    || { echo "Lab Python must match the system Python version" >&2; exit 2; }
extra_pythonpath="$("$python" -c 'import site; print(":".join(site.getsitepackages()))')"
mkdir -p "$scripts/evidence"
evidence="$(mktemp -d "$scripts/evidence/local-$compositor-$mode.XXXXXX")"
profile="$(mktemp -d /tmp/docking-local-profile.XXXXXX)"
trap 'rm -rf "$profile"' EXIT
mkdir -p "$profile/home" "$profile/runtime"
chmod 700 "$profile/runtime"
cat > "$profile/bus.conf" <<'XML'
<busconfig>
  <type>session</type><listen>unix:tmpdir=/tmp</listen><auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*"/><allow receive_sender="*"/><allow own="*"/>
  </policy>
</busconfig>
XML
require_supported=()
case "$mode" in
    cinnamon-services) session=(bash "$scripts/cinnamon_services.sh" --session) ;;
    native)
        if [ "$compositor" = wayfire ] && [ "${1:-visual}" = contract ]; then
            session=(bash "$scripts/wayfire_services.sh" --session)
        else
            session=(bash "$scripts/native_services.sh" --session "$compositor" "${1:-visual}")
        fi
        ;;
    matrix)
        emit_args=()
        while [ $# -gt 0 ]; do
            case "$1" in
                --case|--behavior) emit_args+=("$1" "${2:?value required}"); shift 2 ;;
                --require-supported) require_supported=(--require-supported); shift ;;
                *) echo "Unknown matrix option: $1" >&2; exit 2 ;;
            esac
        done
        (cd "$repo_dir" && "$python" -m tools.visual_compositor.scenarios \
            --emit "$evidence/cases.json" "${emit_args[@]}")
        "$python" - "$evidence/run-meta.json" "$compositor" <<'PY'
import json, os, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "runtime": "local", "compositor": sys.argv[2],
    "panel": {"height": int(os.environ.get("LAB_PANEL_HEIGHT", "0")),
              "position": os.environ.get("LAB_PANEL_POSITION", "bottom"),
              "layer": os.environ.get("LAB_PANEL_LAYER", "top")},
    "outputs": [{"count": int(os.environ.get("LAB_OUTPUTS", "1")),
                 "width": int(os.environ.get("LAB_WIDTH", "1280")),
                 "height": int(os.environ.get("LAB_HEIGHT", "720")), "scale": 1}],
}, indent=2) + "\n")
PY
        session=(bash "$scripts/run_session.sh" "$compositor" source)
        ;;
esac
printf 'Local evidence: %s\n' "$evidence"
# Bind a private directory over the same home path without changing HOME.
# Rebind the repository beneath it so the interpreter/source/evidence stay visible.
env -u DISPLAY -u WAYLAND_DISPLAY -u DBUS_SESSION_BUS_ADDRESS \
    -u XDG_SESSION_DESKTOP -u DESKTOP_SESSION -u DOCKING_BACKEND \
    -u SWAYSOCK -u NIRI_SOCKET -u WAYFIRE_SOCKET -u HYPRLAND_INSTANCE_SIGNATURE \
    -u GAMESCOPE_WAYLAND_DISPLAY -u GDK_BACKEND \
    LAB_DIR="$evidence" LAB_SCRIPTS="$scripts" DOCKING_SOURCE="$repo_dir" \
    DOCKING_LAB_EXTRA_PYTHONPATH="$extra_pythonpath" \
    PYTHONPATH="$repo_dir:$extra_pythonpath" NO_AT_BRIDGE=1 \
    XDG_RUNTIME_DIR="$profile/runtime" \
    XDG_CONFIG_HOME="$evidence/config" XDG_CACHE_HOME="$evidence/cache" \
    XDG_DATA_HOME="$evidence/data" XDG_STATE_HOME="$evidence/state" \
    bwrap --ro-bind / / --dev-bind /dev /dev --proc /proc --bind /tmp /tmp \
    --bind /sys/kernel/security /sys/kernel/security \
    --bind "$profile/home" "$HOME" --bind "$repo_dir" "$repo_dir" \
    --tmpfs /run/dbus \
    --unshare-pid --unshare-net --die-with-parent --new-session \
    dbus-run-session --config-file="$profile/bus.conf" -- \
    bash -c 'export DBUS_SYSTEM_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS"; exec "$@"' \
    docking-private-session "${session[@]}"
if [ "$mode" = matrix ]; then
    (cd "$repo_dir" && "$python" -m tools.visual_compositor.compare \
        --compositor "$compositor" --evidence "$evidence" --out "$evidence/diffs" \
        --geometry-only "${emit_args[@]}" "${require_supported[@]}")
fi
