#!/usr/bin/env bash
# Isolated native-service and actual dock-pixel regressions. No host session bus.
set -euo pipefail
if [ "${1:-}" != --session ]; then
    compositor="${1:?compositor required}"
    mode="${2:-visual}"
    repo_dir="$(cd "$(dirname "$0")/../.." && pwd)"
    evidence="$(mktemp -d "$repo_dir/tools/visual_compositor/evidence/native-$compositor.XXXXXX")"
    image="${DOCKING_LAB_IMAGE:-docking-lab:$compositor}"
    lab_uid="$(id -u)"
    lab_gid="$(id -g)"
    device_args=()
    if [ -n "${DOCKING_LAB_RENDER_NODE:-}" ]; then
        [ -c "$DOCKING_LAB_RENDER_NODE" ] || { echo "Render node must be a character device" >&2; exit 1; }
        device_args=(--device "$DOCKING_LAB_RENDER_NODE" --group-add "$(stat -c %g "$DOCKING_LAB_RENDER_NODE")"
            -e LAB_PARENT_RENDERER=gles2 -e LAB_KWIN_COMPOSE=O2
            -e "WLR_RENDER_DRM_DEVICE=$DOCKING_LAB_RENDER_NODE")
    fi
    docker run --rm --entrypoint cat "$image" /etc/passwd > "$evidence/.passwd"
    docker run --rm --entrypoint cat "$image" /etc/group > "$evidence/.group"
    printf 'lab:x:%s:%s:lab:/tmp:/bin/sh\n' "$lab_uid" "$lab_gid" >> "$evidence/.passwd"
    printf 'lab:x:%s:\n' "$lab_gid" >> "$evidence/.group"
    printf 'Evidence: %s\n' "$evidence"
    docker run --rm --network none --user "$lab_uid:$lab_gid" -e HOME=/tmp -e "LAB_OUTPUTS=${LAB_OUTPUTS:-1}" "${device_args[@]}" \
        -v "$evidence/.passwd:/etc/passwd:ro" -v "$evidence/.group:/etc/group:ro" \
        -v "$repo_dir:/src:ro" -v "$evidence:/evidence" \
        "$image" dbus-run-session -- bash /src/tools/visual_compositor/native_services.sh --session "$compositor" "$mode"
    exit
fi
compositor="${2:?compositor required}"
export LAB_SERVICE_MODE="${3:-visual}"
export LAB_DIR=/evidence LAB_SCRIPTS=/src/tools/visual_compositor DOCKING_SOURCE=/src
export XDG_RUNTIME_DIR="$(mktemp -d /tmp/docking-native.XXXXXX)"
export XDG_CONFIG_HOME=/tmp/native-config XDG_CACHE_HOME=/tmp/native-cache
export XDG_DATA_HOME=/tmp/native-data XDG_STATE_HOME=/tmp/native-state
export GSETTINGS_BACKEND=memory DOCKING_LOG_LEVEL=DEBUG
unset DISPLAY WAYLAND_DISPLAY DOCKING_BACKEND SWAYSOCK NIRI_SOCKET HYPRLAND_INSTANCE_SIGNATURE
mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME"
source "$LAB_SCRIPTS/adapters/common.sh"
source "$LAB_SCRIPTS/adapters/$compositor.sh"
trap adapter_cleanup EXIT
export LAB_EXPECTED_BACKEND="$compositor" LAB_CAPTURE_MODE=wayland
if [ "$compositor" = gnome ]; then
    export GSETTINGS_BACKEND=keyfile LAB_EXPECTED_BACKEND=gnome-shell-bridge LAB_CAPTURE_MODE=x11
    extension="$XDG_DATA_HOME/gnome-shell/extensions/docking-bridge@docking.org"
    mkdir -p "$extension"
    cp /src/docking/platform/backends/gnome/extension/* "$extension/"
    gsettings set org.gnome.shell enabled-extensions "['docking-bridge@docking.org']"
    gsettings set org.gnome.desktop.notifications show-banners false
elif [ "$compositor" = kwin ] || [ "$compositor" = cosmic ] || [ "$compositor" = hyprland ]; then
    export LAB_CAPTURE_MODE=parent
fi
adapter_prepare
if [ "$compositor" = kwin ]; then
    printf '[Desktops]\nNumber=2\n' > "$XDG_CONFIG_HOME/kwinrc"
fi
if [ "$compositor" = sway ]; then
    # Blue server decorations are not part of the fixture's red client image.
    printf '\ndefault_border none\ndefault_floating_border none\n' >> "$SWAY_CONFIG"
fi
if [ "$compositor" = niri ]; then
    # Niri currently exposes absolute positions for floating windows only.
    # Tiled windows must retain unknown geometry, not be guessed at the origin.
    printf '\nwindow-rule {\n match app-id="lab-native";\n open-floating true;\n default-floating-position x=0 y=0 relative-to="top-left";\n}\n' >> "$XDG_CONFIG_HOME/niri/config.kdl"
fi
adapter_start
adapter_wait_ready
export PYTHONPATH="$(docking_source_pythonpath)"
positions="bottom top left right"
probe=native_services_probe.py
limit=40
[ "$LAB_SERVICE_MODE" = idle ] && positions=idle
if [ "$LAB_SERVICE_MODE" = outline ]; then
    positions=bottom
    probe=window_outline_probe.py
    limit=60
fi
for position in $positions; do
    timeout -k 5 "$limit" /usr/bin/python3 "$LAB_SCRIPTS/probes/$probe" "$position" \
        > "$LAB_DIR/$position.log" 2>&1 || {
            tail -60 "$LAB_DIR/$position.log"
            if [ "$compositor" = niri ]; then
                niri msg -j outputs
                niri msg -j workspaces
                niri msg -j windows
            fi
            exit 1
        }
    tail -1 "$LAB_DIR/$position.log"
done
