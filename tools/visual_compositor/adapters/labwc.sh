#!/bin/bash
# labwc adapter: wlroots, layer-shell, no IPC.
#
# Structurally the same headless route as sway, but labwc publishes no output
# query, so the output geometry is measured from the captured framebuffer rather
# than asked for. For a single headless output at the origin that is the same
# answer, and it is measured rather than assumed.

LABWC_CONFIG="${XDG_RUNTIME_DIR:-/tmp}/labwc"

adapter_capabilities() {
    local probe panel=false
    probe="$(session_probe_json)"
    [ "${LAB_PANEL_HEIGHT:-0}" -gt 0 ] && panel=true
    jq -n --argjson probe "$probe" --argjson panel "$panel" '{
        compositor: "labwc",
        expected_backend: "wayland-layer-shell",
        native_geometry: false,
        pointer: false,
        placement: $probe.layer_shell_supported,
        panel: $panel,
        screenshot_method: "grim",
        gtk_display_is_wayland: $probe.gtk_display_is_wayland,
        layer_shell_supported: $probe.layer_shell_supported
    }'
}

adapter_prepare() {
    COMPOSITOR_LOG="${LAB_DIR}/labwc.log"
    export XDG_CURRENT_DESKTOP=labwc
    export XDG_SESSION_TYPE=wayland
    export GDK_BACKEND=wayland

    export WLR_BACKENDS=headless
    export WLR_RENDERER=pixman
    export WLR_LIBINPUT_NO_DEVICES=1
    export WLR_HEADLESS_OUTPUTS="${LAB_OUTPUTS:-1}"

    # An empty private config directory: labwc reads rc.xml and menu.xml from
    # XDG_CONFIG_HOME/labwc if present, and defaults are what we want.
    mkdir -p "$XDG_CONFIG_HOME/labwc"
}

adapter_start() {
    labwc >"${LAB_DIR}/labwc.log" 2>&1 &
    ADAPTER_COMPOSITOR_PID=$!
    wait_for_wayland_socket 80
}

adapter_wait_ready() {
    # No IPC to poll, so the readiness proof is that a capture succeeds: a grim
    # round-trip needs a working compositor and an output to copy from.
    local attempts="${1:-60}"
    local probe="$XDG_RUNTIME_DIR/.ready.png"
    for _ in $(seq "$attempts"); do
        kill -0 "$ADAPTER_COMPOSITOR_PID"
        if grim "$probe" 2>/dev/null; then
            rm -f "$probe"
            return 0
        fi
        sleep 0.25
    done
    log_adapter "labwc never produced a capturable frame"
    return 1
}

adapter_screenshot() {
    grim "$1"
}

adapter_geometry() {
    local probe="$XDG_RUNTIME_DIR/.geometry.png"
    grim "$probe"
    local dims width height
    dims="$(png_dimensions "$probe")"
    rm -f "$probe"
    width="${dims%x*}"
    height="${dims#*x}"

    jq -n --argjson w "$width" --argjson h "$height" \
        '{outputs: [{name: "headless-1", x: 0, y: 0, width: $w, height: $h, scale: 1}],
          dock_rect: null}'
}

adapter_pointer() { [ "$LAB_INPUT_SUPPORTED" = true ] && lab_pointer "$@"; }

adapter_stop() {
    if [ -n "$ADAPTER_COMPOSITOR_PID" ]; then
        terminate_pid "$ADAPTER_COMPOSITOR_PID"
        ADAPTER_COMPOSITOR_PID=""
    fi
}
