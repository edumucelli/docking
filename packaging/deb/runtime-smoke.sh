#!/bin/bash
# Exercise the installed launcher on X11 and a software-rendered Wayland compositor.
set -euo pipefail
checks="$(cd "$(dirname "$0")" && pwd)"
if [ "${1:-}" = --session ]; then
    backend="$2"
    export DOCKING_LOG_LEVEL=INFO
    export XDG_CONFIG_HOME="$HOME/config-$backend"
    export XDG_CACHE_HOME="$HOME/cache-$backend"
    export XDG_DATA_HOME="$HOME/data-$backend"
    app_pid=""
    compositor_pid=""
    cleanup() {
        status=$?
        if [ "$status" -ne 0 ]; then
            cat "$HOME/docking-$backend.log" 2>/dev/null || true
            cat "$HOME/sway.log" 2>/dev/null || true
            cat "$HOME/dbus-$backend.err" 2>/dev/null || true
        fi
        [ -z "$app_pid" ] || kill "$app_pid" 2>/dev/null || true
        [ -z "$compositor_pid" ] || kill "$compositor_pid" 2>/dev/null || true
    }
    trap cleanup EXIT
    if [ "$backend" = wayland ]; then
        unset DISPLAY
        export GDK_BACKEND=wayland XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=sway
        export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1
        printf 'output HEADLESS-1 resolution 1280x720
xwayland disable
' > "$HOME/sway.conf"
        sway --config "$HOME/sway.conf" > "$HOME/sway.log" 2>&1 &
        compositor_pid=$!
        for attempt in {1..60}; do
            kill -0 "$compositor_pid"
            for socket in "$XDG_RUNTIME_DIR"/wayland-*; do
                if [ -S "$socket" ]; then export WAYLAND_DISPLAY="${socket##*/}"; break; fi
            done
            [ -z "${WAYLAND_DISPLAY:-}" ] || break
            sleep 0.5
        done
        test -n "${WAYLAND_DISPLAY:-}"
        /usr/bin/python3 "$checks/smoke.py" --require-wayland
    else
        export GDK_BACKEND=x11 XDG_SESSION_TYPE=x11
        unset WAYLAND_DISPLAY
        /usr/bin/python3 "$checks/smoke.py"
    fi
    if [ "$backend" = wayland ]; then
        mkdir -p "$XDG_DATA_HOME/applications"
        cat > "$XDG_DATA_HOME/applications/org.docking.PackageSmoke.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Docking package Wayland probe
Exec=/usr/bin/true
Icon=org.docking.Docking
EOF
    fi
    /usr/bin/docking > "$HOME/docking-$backend.log" 2>&1 &
    app_pid=$!
    ready=false
    for attempt in {1..60}; do
        kill -0 "$app_pid"
        if gdbus call --session --dest org.docking.Docking --object-path /org/docking/Docking \
            --method org.docking.Docking.Items1.GetCount > "$HOME/dbus-$backend.log" 2> "$HOME/dbus-$backend.err"; then
            ready=true
            break
        fi
        sleep 0.5
    done
    test "$ready" = true
    sleep 2
    kill -0 "$app_pid"
    if [ "$backend" = wayland ]; then
        grep -F 'Selected session backend: wayland-layer-shell' "$HOME/docking-$backend.log"
        grep -F 'Wayland protocol runtime started: foreign_toplevel=True' "$HOME/docking-$backend.log"
        /usr/bin/python3 "$checks/wayland-smoke.py"
    else
        grep -F 'Selected session backend: x11' "$HOME/docking-$backend.log"
    fi
    echo "Checking D-Bus responsiveness before shutdown"
    gdbus call --session --timeout 3 --dest org.docking.Docking --object-path /org/docking/Docking \
        --method org.docking.Docking.Items1.GetCount
    echo "Sending SIGTERM to $app_pid"
    kill -TERM "$app_pid"
    for attempt in {1..40}; do
        kill -0 "$app_pid" 2>/dev/null || break
        sleep 0.25
    done
    if kill -0 "$app_pid" 2>/dev/null; then
        kill -USR1 "$app_pid"
        sleep 1
        echo "Docking did not shut down within 10 seconds" >&2
        kill -KILL "$app_pid"
        exit 1
    fi
    wait "$app_pid"
    app_pid=""
    if grep -E 'Traceback|Failed to start runtime stage|Forcing shutdown after the cleanup timeout' "$HOME/docking-$backend.log"; then exit 1; fi
    echo "Installed Docking passed $backend startup, D-Bus request, and graceful shutdown"
else
    # Sway refuses to run with root privileges. Use an isolated unprivileged user.
    id docking-smoke >/dev/null 2>&1 || useradd --create-home --shell /bin/bash docking-smoke
    install -d -m 0700 -o docking-smoke -g docking-smoke /tmp/docking-runtime
    timeout 120s runuser -u docking-smoke -- env XDG_RUNTIME_DIR=/tmp/docking-runtime \
        xvfb-run -a dbus-run-session -- bash "$0" --session x11
    timeout 120s runuser -u docking-smoke -- env XDG_RUNTIME_DIR=/tmp/docking-runtime \
        dbus-run-session -- bash "$0" --session wayland
fi
