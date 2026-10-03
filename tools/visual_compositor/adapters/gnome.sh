#!/bin/bash
# Real GNOME/Mutter without the Docking bridge: a negative compatibility lane.
adapter_prepare() {
    COMPOSITOR_LOG="$LAB_DIR/gnome.log"
    export XDG_CURRENT_DESKTOP=GNOME XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland
    export LIBGL_ALWAYS_SOFTWARE=1
    export LAB_NATIVE_OBSERVER=gnome
    export DBUS_SYSTEM_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS"
    export MUTTER_DEBUG_DUMMY_MODE_SPECS="${LAB_WIDTH:-1280}x${LAB_HEIGHT:-720}"
    export MUTTER_DEBUG_NUM_DUMMY_MONITORS="${LAB_OUTPUTS:-1}"
    unset DISPLAY
    if [ "${LAB_GNOME_BRIDGE:-0}" = 1 ]; then
        local extensions="$XDG_DATA_HOME/gnome-shell/extensions"
        mkdir -p "$extensions"
        cp -r "$DOCKING_SOURCE/docking/platform/backends/gnome/extension" \
            "$extensions/docking-bridge@docking.org"
        cp -r "$LAB_SCRIPTS/probes/gnome-observer" \
            "$extensions/docking-visual-observer@docking.org"
    fi
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
            if [ "${LAB_GNOME_BRIDGE:-0}" = 1 ]; then
                enable_lab_gnome_bridge
            fi
            return 0
        fi
        sleep 0.5
    done
    return 1
}
adapter_capabilities() {
    if [ "${LAB_GNOME_BRIDGE:-0}" = 1 ]; then
        /usr/bin/python3 "$LAB_SCRIPTS/probes/gnome_probe.py" geometry >/dev/null
        session_probe_json | jq '{compositor:"gnome-bridge", expected_backend:"gnome-shell-bridge",
            placement:true, native_geometry:true, window_tracking:true, window_actions:true,
            workspace_switch:true, bridge_recovery:true, popup_bounds:true, scaling:true,
            preview_popup:true, logical_screenshot:true, pointer:false,
            gtk_display_is_wayland, screenshot_method:"native-mutter-output"}'
        return
    fi
    session_probe_json | jq '{compositor:"gnome", expected_backend:"reduced",
        placement:false, native_geometry:false, pointer:false,
        unsupported_reasons:{placement:"GNOME requires the Docking Shell bridge, which this negative compatibility lane deliberately omits"},
        gtk_display_is_wayland, screenshot_method:"private-xvfb"}'
}
adapter_screenshot() {
    if [ "${LAB_GNOME_BRIDGE:-0}" = 1 ]; then
        /usr/bin/python3 "$LAB_SCRIPTS/probes/gnome_capture.py" "$1"
        return
    fi
    GDK_BACKEND=x11 XAUTHORITY="$(cat "$LAB_DIR/outer-authority")" \
        /usr/bin/python3 "$LAB_SCRIPTS/probes/x11_capture.py" \
        "$(cat "$LAB_DIR/outer-display")" "$1"
}
adapter_geometry() {
    if [ "${LAB_GNOME_BRIDGE:-0}" = 1 ]; then
        /usr/bin/python3 "$LAB_SCRIPTS/probes/gnome_probe.py" geometry \
            | jq -e 'if .overview_visible then error("GNOME overview contaminated the capture") else . end'
    else
        /usr/bin/python3 "$LAB_SCRIPTS/probes/session_probe.py" geometry
    fi
}
adapter_windows() { /usr/bin/python3 "$LAB_SCRIPTS/probes/gnome_probe.py" windows; }
adapter_background() {
    if [ "${LAB_GNOME_BRIDGE:-0}" = 1 ]; then
        gdbus call --session --dest org.docking.VisualLab.Gnome --object-path /org/docking/VisualLab/Gnome \
            --method org.docking.VisualLab.Gnome1.SetBackground "$1" >/dev/null
    fi
}
adapter_scene() {
    if [ -n "${1:-}" ] && [ "${LAB_GNOME_BRIDGE:-0}" = 1 ]; then
        /usr/bin/python3 "$LAB_SCRIPTS/probes/gnome_display.py" "$1"
        sleep 1
    fi
}
adapter_pointer() { return 1; }
adapter_stop() { adapter_cleanup; }

enable_lab_gnome_bridge() {
    local uuid result
    for uuid in docking-bridge@docking.org docking-visual-observer@docking.org; do
        result="$(gdbus call --session --dest org.gnome.Shell \
            --object-path /org/gnome/Shell --method org.gnome.Shell.Extensions.EnableExtension "$uuid")"
        [ "$result" = '(true,)' ] || { log_adapter "GNOME refused extension $uuid: $result"; return 1; }
    done
    for _ in $(seq 40); do
        if gdbus call --session --dest org.docking.Docking.GnomeShellBridge \
            --object-path /org/docking/Docking/GnomeShellBridge \
            --method org.docking.Docking.GnomeShellBridge1.ListWorkspaces >/dev/null 2>&1 \
            && /usr/bin/python3 "$LAB_SCRIPTS/probes/gnome_probe.py" geometry >/dev/null 2>&1; then
            sleep 2
            return 0
        fi
        sleep 0.25
    done
    log_adapter "GNOME bridge or independent frame observer did not become ready"
    return 1
}
