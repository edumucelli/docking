#!/usr/bin/env bash
source "$LAB_SCRIPTS/adapters/nested.sh"
adapter_prepare() {
    COMPOSITOR_LOG="$LAB_DIR/jay.log"
    export XDG_CURRENT_DESKTOP=jay XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland
    export LIBGL_ALWAYS_SOFTWARE="${LAB_SOFTWARE_RENDERING:-1}" JAY_NO_REALTIME=1
    prepare_sway_parent
    printf '\nxwayland force\n' >>"$XDG_CONFIG_HOME/sway/config"
    mkdir -p "$XDG_CONFIG_HOME/jay"
    # Grant the documented protocols only inside this private harness session.
    cat >"$XDG_CONFIG_HOME/jay/config.toml" <<'TOML'
[[clients]]
match = {}
capabilities = ["layer-shell", "foreign-toplevel-manager", "foreign-toplevel-list", "workspace-manager", "screencopy", "idle-notifier"]
TOML
}
adapter_start() {
    start_sway_parent
    local socket
    for _ in $(seq 80); do
        for socket in /tmp/.X11-unix/X*; do
            [ -S "$socket" ] || continue
            export DISPLAY=":${socket##*X}"
            break
        done
        [ -n "${DISPLAY:-}" ] && break
        sleep 0.25
    done
    [ -n "${DISPLAY:-}" ] || return 1
    jay run --backends x11 >"$COMPOSITOR_LOG" 2>&1 &
    ADAPTER_COMPOSITOR_PID=$!
    unset DISPLAY
    for _ in $(seq 120); do
        kill -0 "$ADAPTER_COMPOSITOR_PID" 2>/dev/null || { report_compositor_failure; return 1; }
        for socket in "$XDG_RUNTIME_DIR"/wayland-*; do
            [ -S "$socket" ] || continue
            [ "${socket##*/}" != "$LAB_PARENT_DISPLAY" ] || continue
            export WAYLAND_DISPLAY="${socket##*/}"
            return 0
        done
        sleep 0.25
    done
    return 1
}
retain_jay_log() {
    local kind log
    for kind in jay portal; do
        for log in "$XDG_DATA_HOME"/jay/logs/"$kind"/*.txt; do
            [ -f "$log" ] || continue
            mv "$log" "$LAB_DIR/$kind-details.log"
        done
    done
    [ ! -f "$LAB_DIR/jay-details.log" ] || COMPOSITOR_LOG="$LAB_DIR/jay-details.log"
}
report_compositor_failure() { retain_jay_log; tail -60 "$COMPOSITOR_LOG"; }
adapter_wait_ready() { retain_jay_log; session_probe_json >"$LAB_DIR/display-probe.json"; }
adapter_capabilities() {
    session_probe_json | jq '{compositor:"jay", expected_backend:"wayland-layer-shell",
        placement:.layer_shell_supported, native_geometry:false, pointer:false,
        gtk_display_is_wayland, layer_shell_supported, screenshot_method:"parent-grim"}'
}
adapter_screenshot() { capture_parent "$1"; }
adapter_geometry() { /usr/bin/python3 "$LAB_SCRIPTS/probes/session_probe.py" geometry; }
adapter_pointer() { lab_pointer "$@"; }
adapter_stop() { adapter_cleanup; }
