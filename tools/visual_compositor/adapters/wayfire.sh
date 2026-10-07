#!/bin/bash
# wayfire adapter: wlroots, layer-shell, own IPC.
#
# Wayfire publishes an IPC socket, but it reports *views* (regular clients), not
# layer surfaces, so it cannot answer where the dock is -- same limitation as
# sway. Placement is therefore verified from pixels, with the output geometry
# measured from the captured framebuffer.

WAYFIRE_CONFIG="$XDG_CONFIG_HOME/wayfire.ini"

adapter_capabilities() {
    local probe
    probe="$(session_probe_json)"
    jq -n --argjson probe "$probe" '{
        compositor: "wayfire",
        expected_backend: "wayfire",
        native_geometry: false,
        pointer: false,
        placement: $probe.layer_shell_supported,
        screenshot_method: "grim",
        gtk_display_is_wayland: $probe.gtk_display_is_wayland,
        layer_shell_supported: $probe.layer_shell_supported
    }'
}

adapter_prepare() {
    COMPOSITOR_LOG="${LAB_DIR}/wayfire.log"
    export XDG_CURRENT_DESKTOP=wayfire
    export XDG_SESSION_TYPE=wayland
    export GDK_BACKEND=wayland

    export WLR_BACKENDS=headless
    if [ -n "${WLR_RENDER_DRM_DEVICE:-}" ]; then
        export WLR_RENDERER=gles2
    else
        # Newer Wayfire/wlroots supports software rendering; the compositor's
        # startup gate reports if this image still requires a render node.
        unset WLR_RENDER_DRM_DEVICE
        export WLR_RENDERER=pixman
    fi
    export WLR_LIBINPUT_NO_DEVICES=1
    export WLR_HEADLESS_OUTPUTS="${LAB_OUTPUTS:-1}"

    # Arch's Wayfire target includes IPC plugins. Docking needs this socket to
    # select its native window-tracking/visibility backend; screenshots alone
    # would only exercise generic layer-shell placement.
    mkdir -p "$(dirname "$WAYFIRE_CONFIG")"
    cat >"$WAYFIRE_CONFIG" <<'INI'
[core]
plugins = ipc ipc-rules place grid move resize
close_top_view = false
INI
}

adapter_start() {
    wayfire -c "$WAYFIRE_CONFIG" >"${LAB_DIR}/wayfire.log" 2>&1 &
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
            for socket in "$XDG_RUNTIME_DIR"/wayfire* /tmp/wayfire*.socket; do
                if [ -S "$socket" ]; then export WAYFIRE_SOCKET="$socket"; return 0; fi
            done
        fi
        sleep 0.25
    done
    log_adapter "wayfire never produced a capturable frame"
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
