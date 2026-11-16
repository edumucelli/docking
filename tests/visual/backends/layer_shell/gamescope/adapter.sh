#!/usr/bin/env bash
source "$LAB_SCRIPTS/adapters/nested.sh"
adapter_prepare() {
    COMPOSITOR_LOG="$LAB_DIR/gamescope.log"
    export XDG_CURRENT_DESKTOP=gamescope XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland
    export LIBGL_ALWAYS_SOFTWARE=1
    prepare_sway_parent
}
adapter_start() {
    start_sway_parent
    WAYLAND_DISPLAY="$LAB_PARENT_DISPLAY" gamescope --backend wayland --expose-wayland \
        -W "${LAB_WIDTH:-1280}" -H "${LAB_HEIGHT:-720}" -- /bin/sleep infinity \
        >"$COMPOSITOR_LOG" 2>&1 &
    ADAPTER_COMPOSITOR_PID=$!
    for _ in $(seq 160); do
        kill -0 "$ADAPTER_COMPOSITOR_PID" 2>/dev/null || { report_compositor_failure; return 1; }
        for socket in "$XDG_RUNTIME_DIR"/wayland-*; do
            [ -S "$socket" ] || continue
            [ "${socket##*/}" != "$LAB_PARENT_DISPLAY" ] || continue
            export WAYLAND_DISPLAY="${socket##*/}"
            export GAMESCOPE_WAYLAND_DISPLAY="$WAYLAND_DISPLAY"
            return 0
        done
        sleep 0.25
    done
    return 1
}
adapter_capabilities() {
    session_probe_json | jq '{compositor:"gamescope", expected_backend:"wayland-layer-shell",
        placement:.layer_shell_supported, native_geometry:false, pointer:false,
        gtk_display_is_wayland, layer_shell_supported, screenshot_method:"parent-grim"}'
}
adapter_screenshot() { capture_parent "$1"; }
adapter_geometry() { /usr/bin/python3 "$LAB_SCRIPTS/probes/session_probe.py" geometry; }
adapter_pointer() { lab_pointer "$@"; }
adapter_stop() { adapter_cleanup; }
