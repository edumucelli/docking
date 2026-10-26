#!/bin/bash
# cage adapter: wlroots kiosk compositor. The negative-compatibility lane.
#
# Cage runs a single application full-screen. A dock is not a meaningful client
# there, and the project documents this: selection.py:262-267 carries a specific
# reason for Cage ("does not expose a layer-shell surface suitable for a dock").
#
# So the point of this lane is NOT placement. It is that Docking degrades
# HONESTLY: starts, selects a backend it can actually honour, and does not claim
# capabilities it cannot deliver. Whether placement runs at all is decided by
# what cage actually advertises, probed rather than assumed -- if it turns out to
# implement layer-shell, placement cases run and their result is information
# rather than a preset expectation.
#
# Cage needs a client to stay alive; `sleep` occupies the screen and keeps the
# compositor up while Docking is launched separately.

PROBE="${LAB_SCRIPTS}/probes/session_probe.py"

adapter_capabilities() {
    local probe layer_shell placement reasons
    probe="$(/usr/bin/python3 "$PROBE" capabilities)"
    layer_shell="$(echo "$probe" | jq -r '.layer_shell_supported')"
    if [ "$layer_shell" = "true" ]; then
        placement=true
        reasons='{}'
    else
        placement=false
        reasons='{"placement": "cage is a single-application kiosk compositor and does not expose a layer-shell surface suitable for a dock (selection.py:262-267), so Docking runs on the reduced tier"}'
    fi

    jq -n \
        --argjson layer_shell "$layer_shell" \
        --argjson placement "$placement" \
        --argjson reasons "$reasons" \
        --argjson probe "$probe" \
        '{
            compositor: "cage",
            expected_backend: (if $layer_shell then "wayland-layer-shell" else "reduced" end),
            native_geometry: false,
            pointer: false,
            placement: $placement,
            unsupported_reasons: $reasons,
            screenshot_method: "grim",
            gtk_display_is_wayland: $probe.gtk_display_is_wayland,
            layer_shell_supported: $probe.layer_shell_supported
        }'
}

adapter_prepare() {
    COMPOSITOR_LOG="${LAB_DIR}/cage.log"
    export XDG_CURRENT_DESKTOP=cage
    export XDG_SESSION_TYPE=wayland
    export GDK_BACKEND=wayland

    export WLR_BACKENDS=headless
    export WLR_RENDERER=pixman
    export WLR_LIBINPUT_NO_DEVICES=1
    export WLR_HEADLESS_OUTPUTS="${LAB_OUTPUTS:-1}"
}

adapter_start() {
    # cage exits when its client exits, so an infinite no-op holds the session
    # open for the dock to connect to.
    cage -- /bin/sleep infinity >"${LAB_DIR}/cage.log" 2>&1 &
    ADAPTER_COMPOSITOR_PID=$!
    wait_for_wayland_socket 80
}

adapter_wait_ready() {
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
    log_adapter "cage never produced a capturable frame"
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

adapter_pointer() {
    log_adapter "pointer injection is not implemented for cage"
    return 1
}

adapter_stop() {
    if [ -n "$ADAPTER_COMPOSITOR_PID" ]; then
        terminate_pid "$ADAPTER_COMPOSITOR_PID"
        ADAPTER_COMPOSITOR_PID=""
    fi
}
