#!/bin/bash
# KWin nested in an isolated headless Sway, captured through the parent.
source "$LAB_SCRIPTS/adapters/nested.sh"
adapter_prepare() {
    COMPOSITOR_LOG="$LAB_DIR/kwin.log"
    export XDG_CURRENT_DESKTOP=KDE XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland
    export KWIN_COMPOSE=Q LIBGL_ALWAYS_SOFTWARE=1
    unset DISPLAY
    prepare_sway_parent
}
adapter_start() {
    start_sway_parent
    kwin_wayland --wayland-display "$LAB_PARENT_DISPLAY" --socket lab-kwin \
        --width "${LAB_WIDTH:-1280}" --height "${LAB_HEIGHT:-720}" \
        --no-lockscreen --no-global-shortcuts >"$COMPOSITOR_LOG" 2>&1 &
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
            capture_parent "$LAB_DIR/ready.png"
            return 0
        fi
        sleep 0.25
    done
    return 1
}
adapter_capabilities() {
    session_probe_json | jq '{compositor:"kwin", expected_backend:"kwin",
        placement:.layer_shell_supported, native_geometry:false, pointer:false,
        gtk_display_is_wayland, layer_shell_supported, screenshot_method:"parent-grim"}'
}
adapter_screenshot() { capture_parent "$1"; }
adapter_geometry() {
    /usr/bin/python3 "$LAB_SCRIPTS/probes/session_probe.py" geometry
}
adapter_pointer() { lab_pointer "$@"; }
adapter_stop() { adapter_cleanup; }
