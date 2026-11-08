#!/usr/bin/env bash
# Two-output IPC contract with and without wm-actions/vswitch. No host session bus.
set -euo pipefail
if [ "${1:-}" != --session ]; then
    repo_dir="$(cd "$(dirname "$0")/../../.." && pwd)"
    mkdir -p "$repo_dir/tools/linux/visual/evidence"
    evidence="$(mktemp -d "$repo_dir/tools/linux/visual/evidence/wayfire-contract.XXXXXX")"
    image="${DOCKING_LAB_IMAGE:-docking-lab:wayfire}"
    lab_uid="$(id -u)"
    lab_gid="$(id -g)"
    docker run --rm --entrypoint cat "$image" /etc/passwd > "$evidence/.passwd"
    docker run --rm --entrypoint cat "$image" /etc/group > "$evidence/.group"
    printf 'lab:x:%s:%s:lab:/tmp:/bin/sh\n' "$lab_uid" "$lab_gid" >> "$evidence/.passwd"
    printf 'lab:x:%s:\n' "$lab_gid" >> "$evidence/.group"
    printf 'Evidence: %s\n' "$evidence"
    docker run --rm --network none --user "$lab_uid:$lab_gid" -e HOME=/tmp \
        -v "$evidence/.passwd:/etc/passwd:ro" -v "$evidence/.group:/etc/group:ro" \
        -v "$repo_dir:/src:ro" -v "$evidence:/evidence" \
        "$image" dbus-run-session -- bash /src/tools/linux/visual/wayfire_services.sh --session
    exit
fi
export LAB_DIR="${LAB_DIR:-/evidence}"
export LAB_SCRIPTS="${LAB_SCRIPTS:-/src/tools/linux/visual}"
export DOCKING_SOURCE="${DOCKING_SOURCE:-/src}"
export XDG_RUNTIME_DIR="$(mktemp -d /tmp/docking-wayfire-contract.XXXXXX)"
export XDG_CONFIG_HOME="$LAB_DIR/config" XDG_CACHE_HOME="$LAB_DIR/cache"
export XDG_DATA_HOME="$LAB_DIR/data" XDG_STATE_HOME="$LAB_DIR/state"
export GSETTINGS_BACKEND=memory
unset DISPLAY WAYLAND_DISPLAY DOCKING_BACKEND SWAYSOCK NIRI_SOCKET WAYFIRE_SOCKET
unset XDG_SESSION_DESKTOP DESKTOP_SESSION HYPRLAND_INSTANCE_SIGNATURE
mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME"
source "$LAB_SCRIPTS/adapters/common.sh"
source "$LAB_SCRIPTS/adapters/wayfire.sh"
trap adapter_cleanup EXIT
export LAB_OUTPUTS=2
export PYTHONPATH="$(docking_source_pythonpath)"
for plugins in full reduced; do
    adapter_prepare
    if [ "$plugins" = reduced ]; then
        sed -i 's/ wm-actions vswitch//' "$WAYFIRE_CONFIG"
    fi
    cat >> "$WAYFIRE_CONFIG" <<'INI'
vwidth = 3
vheight = 2
[output:HEADLESS-1]
mode = 800x600
position = 0,0
[output:HEADLESS-2]
mode = 800x600
position = 800,0
INI
    adapter_start
    adapter_wait_ready
    timeout -k 5 30 /usr/bin/python3 "$LAB_SCRIPTS/probes/wayfire_contract_probe.py" "$plugins" \
        > "$LAB_DIR/$plugins.log" 2>&1 || { cat "$LAB_DIR/$plugins.log"; exit 1; }
    cat "$LAB_DIR/$plugins.log"
    adapter_stop
    unset WAYLAND_DISPLAY WAYFIRE_SOCKET
done
