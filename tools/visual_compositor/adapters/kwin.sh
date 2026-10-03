#!/bin/bash
# Native Wayland KWin in private Xvfb; software QPainter output capture.
adapter_prepare() {
    COMPOSITOR_LOG="$LAB_DIR/kwin.log"
    export XDG_CURRENT_DESKTOP=KDE XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland
    export KWIN_COMPOSE=Q LIBGL_ALWAYS_SOFTWARE=1
    unset DISPLAY
}
adapter_start() {
    local parent_width=$(( ${LAB_WIDTH:-1280} * ${LAB_OUTPUTS:-1} ))
    xvfb-run -a -s "-screen 0 ${parent_width}x${LAB_HEIGHT:-720}x24 +extension GLX +render -noreset" \
        bash -c 'printf "%s\n" "$DISPLAY" >"$LAB_DIR/outer-display";
                 printf "%s\n" "$XAUTHORITY" >"$LAB_DIR/outer-authority";
                 exec kwin_wayland --x11-display "$DISPLAY" --socket lab-kwin --width "${LAB_WIDTH:-1280}" --height "${LAB_HEIGHT:-720}" --output-count "${LAB_OUTPUTS:-1}" --no-lockscreen --no-global-shortcuts' \
                      >"$COMPOSITOR_LOG" 2>&1 &
    ADAPTER_COMPOSITOR_PID=$!
    for _ in $(seq 120); do
        kill -0 "$ADAPTER_COMPOSITOR_PID" 2>/dev/null || { report_compositor_failure; return 1; }
        if [ -S "$XDG_RUNTIME_DIR/lab-kwin" ]; then
            export WAYLAND_DISPLAY=lab-kwin
            return 0
        fi
        sleep 0.25
    done
    return 1
}
adapter_wait_ready() {
    for _ in $(seq 80); do
        kill -0 "$ADAPTER_COMPOSITOR_PID" 2>/dev/null || return 1
        if session_probe_json >"$LAB_DIR/display-probe.json" 2>/dev/null; then
            /usr/bin/python3 "$LAB_SCRIPTS/probes/x11_layout.py"
            return 0
        fi
        sleep 0.25
    done
    return 1
}
adapter_start_frame_clock() {
    LAB_FRAME_MARKERS=1 /usr/bin/python3 "$LAB_SCRIPTS/probes/frame_probe.py" >"$LAB_DIR/frame.log" 2>&1 &
    ADAPTER_FRAME_PID=$!
}
adapter_capabilities() {
    session_probe_json | jq '{compositor:"kwin", expected_backend:"kwin",
        placement:.layer_shell_supported, native_geometry:false, pointer:false, scaling:true, logical_screenshot:true,
        gtk_display_is_wayland, layer_shell_supported, screenshot_method:"native-kde-private-xvfb"}'
}
adapter_screenshot() {
    GDK_BACKEND=x11 XAUTHORITY="$(cat "$LAB_DIR/outer-authority")" \
        PYTHONPATH="$(docking_source_pythonpath)" /usr/bin/python3 "$LAB_SCRIPTS/probes/kwin_capture.py" "$1"
}
adapter_scene() {
    PYTHONPATH="$(docking_source_pythonpath)" /usr/bin/python3 "$LAB_SCRIPTS/probes/kwin_outputs.py" scene "${1:-}" >"$LAB_DIR/configured-outputs.json"
    echo '[20,46,71]' >"$LAB_DIR/background.json"
    sleep 1
}
adapter_geometry() {
    PYTHONPATH="$(docking_source_pythonpath)" /usr/bin/python3 "$LAB_SCRIPTS/probes/kwin_outputs.py"
}
adapter_pointer() { lab_pointer "$@"; }
adapter_stop() { adapter_cleanup; }
