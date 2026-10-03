#!/bin/bash
# Real GNOME/Mutter without the Docking bridge: a negative compatibility lane.
adapter_prepare() {
    COMPOSITOR_LOG="$LAB_DIR/gnome.log"
    export XDG_CURRENT_DESKTOP=GNOME XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland
    export LIBGL_ALWAYS_SOFTWARE=1
    export DBUS_SYSTEM_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS"
    export MUTTER_DEBUG_DUMMY_MODE_SPECS="${LAB_WIDTH:-1280}x${LAB_HEIGHT:-720}"
    export MUTTER_DEBUG_NUM_DUMMY_MONITORS=1
    unset DISPLAY
}
adapter_start() {
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
    session_probe_json | jq '{compositor:"gnome", expected_backend:"reduced",
        placement:false, native_geometry:false, pointer:false,
        unsupported_reasons:{placement:"GNOME requires the Docking Shell bridge, which this negative compatibility lane deliberately omits"},
        gtk_display_is_wayland, screenshot_method:"private-xvfb"}'
}
adapter_screenshot() {
    GDK_BACKEND=x11 XAUTHORITY="$(cat "$LAB_DIR/outer-authority")" \
        /usr/bin/python3 "$LAB_SCRIPTS/probes/x11_capture.py" \
        "$(cat "$LAB_DIR/outer-display")" "$1"
}
adapter_geometry() { /usr/bin/python3 "$LAB_SCRIPTS/probes/session_probe.py" geometry; }
adapter_pointer() { return 1; }
adapter_stop() { adapter_cleanup; }
