#!/bin/bash
# Real GNOME/Mutter, with an optional bridge for the positive integration lane.
adapter_prepare() {
    COMPOSITOR_LOG="$LAB_DIR/gnome.log"
    export XDG_CURRENT_DESKTOP=GNOME XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland
    export LIBGL_ALWAYS_SOFTWARE=1
    export DBUS_SYSTEM_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS"
    export MUTTER_DEBUG_DUMMY_MODE_SPECS="${LAB_WIDTH:-1280}x${LAB_HEIGHT:-720}"
    export MUTTER_DEBUG_NUM_DUMMY_MONITORS=1
    export GSETTINGS_BACKEND=keyfile
    # A uniform private wallpaper keeps desktop artwork out of the pixel oracle.
    gsettings set org.gnome.desktop.background picture-uri "''"
    gsettings set org.gnome.desktop.background picture-uri-dark "''"
    gsettings set org.gnome.desktop.background primary-color "'#000000'"
    gsettings set org.gnome.desktop.background color-shading-type "'solid'"
    if gnome-shell --help | grep -q -- '--nested'; then
        GNOME_LAB_MODE=nested
    else
        GNOME_LAB_MODE=headless
        export LAB_CAPTURE_MODE=gnome
    fi
    if [ "${LAB_GNOME_BRIDGE:-0}" = 1 ]; then
        extension="$XDG_DATA_HOME/gnome-shell/extensions/docking-bridge@docking.org"
        mkdir -p "$extension"
        cp "$DOCKING_SOURCE"/docking/platform/backends/gnome/extension/* "$extension/"
        gsettings set org.gnome.shell enabled-extensions "['docking-bridge@docking.org']"
    fi
    unset DISPLAY
}
adapter_start() {
    if [ "$GNOME_LAB_MODE" = headless ]; then
        gnome-shell --headless --wayland --no-x11 \
            --virtual-monitor="${LAB_WIDTH:-1280}x${LAB_HEIGHT:-720}" \
            >"$COMPOSITOR_LOG" 2>&1 &
        ADAPTER_COMPOSITOR_PID=$!
        wait_for_wayland_socket 240
        return
    fi
    xvfb-run -a -s "-screen 0 ${LAB_WIDTH:-1280}x${LAB_HEIGHT:-720}x24 +extension GLX +render -noreset" \
        bash -c 'printf "%s\n" "$DISPLAY" >"$LAB_DIR/outer-display";
                 printf "%s\n" "$XAUTHORITY" >"$LAB_DIR/outer-authority";
                 exec gnome-shell --nested --wayland --no-x11' >"$COMPOSITOR_LOG" 2>&1 &
    ADAPTER_COMPOSITOR_PID=$!
    wait_for_wayland_socket 240
}
adapter_wait_ready() {
    for _ in $(seq 120); do
        kill -0 "$ADAPTER_COMPOSITOR_PID" 2>/dev/null || { report_compositor_failure; return 1; }
        if gdbus call --session --dest org.freedesktop.DBus \
            --object-path /org/freedesktop/DBus --method org.freedesktop.DBus.GetNameOwner \
            org.gnome.Shell >/dev/null 2>&1; then
            sleep 2
            return 0
        fi
        sleep 0.5
    done
    return 1
}
adapter_capabilities() {
    local method=private-xvfb
    [ "${GNOME_LAB_MODE:-nested}" = headless ] && method=org.gnome.Shell.Screenshot
    session_probe_json | jq --arg method "$method" --argjson bridge "${LAB_GNOME_BRIDGE:-0}" '{compositor:"gnome", expected_backend:(if $bridge == 1 then "gnome-shell-bridge" else "reduced" end),
        placement:($bridge == 1), native_geometry:false, pointer:false,
        unsupported_reasons:(if $bridge == 1 then {} else {placement:"GNOME requires the Docking Shell bridge"} end),
        gtk_display_is_wayland, screenshot_method:$method}'
}
adapter_screenshot() {
    if [ "$GNOME_LAB_MODE" = headless ]; then
        /usr/bin/python3 "$LAB_SCRIPTS/../../backends/gnome/capture.py" "$1"
        return
    fi
    GDK_BACKEND=x11 XAUTHORITY="$(cat "$LAB_DIR/outer-authority")" \
        /usr/bin/python3 "$LAB_SCRIPTS/probes/x11_capture.py" \
        "$(cat "$LAB_DIR/outer-display")" "$1"
}
adapter_geometry() { /usr/bin/python3 "$LAB_SCRIPTS/probes/session_probe.py" geometry; }
adapter_pointer() { return 1; }
adapter_stop() { adapter_cleanup; }
