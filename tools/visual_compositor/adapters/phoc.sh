#!/bin/bash
# Phoc/Phosh's wlroots compositor, with real layer-shell and private thumbnails.
# No Phosh shell is needed for this compositor lane; synthetic panels can be
# enabled independently to exercise exclusive-zone placement.
adapter_prepare() {
    COMPOSITOR_LOG="$LAB_DIR/phoc.log"
    export XDG_CURRENT_DESKTOP=Phosh XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland
    export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1
    export WLR_HEADLESS_OUTPUTS="${LAB_OUTPUTS:-1}"
    unset DISPLAY
    PHOC_LAB_ARGS=()
    if phoc --help | grep -q -- '--no-xwayland'; then
        PHOC_LAB_ARGS+=(--no-xwayland)
    fi
    mkdir -p "$XDG_CONFIG_HOME"
    cat >"$XDG_CONFIG_HOME/phoc.ini" <<EOF
[core]
xwayland=false
[output:HEADLESS-1]
mode=${LAB_WIDTH:-1280}x${LAB_HEIGHT:-720}
scale=1
EOF
}

adapter_start() {
    phoc "${PHOC_LAB_ARGS[@]}" -C "$XDG_CONFIG_HOME/phoc.ini" >"$COMPOSITOR_LOG" 2>&1 &
    ADAPTER_COMPOSITOR_PID=$!
    wait_for_wayland_socket 80
}

adapter_wait_ready() {
    local target="$XDG_RUNTIME_DIR/.phoc-ready.png"
    for _ in $(seq "${1:-60}"); do
        kill -0 "$ADAPTER_COMPOSITOR_PID" 2>/dev/null || { report_compositor_failure; return 1; }
        if grim "$target" 2>/dev/null; then rm -f "$target"; return 0; fi
        sleep 0.25
    done
    log_adapter "Phoc never produced a capturable frame"
    return 1
}

adapter_capabilities() {
    session_probe_json | jq '{compositor:"phoc", expected_backend:"wayland-layer-shell",
        placement:.layer_shell_supported, native_geometry:false, pointer:false,
        panel:(env.LAB_PANEL_HEIGHT // "0" | tonumber > 0),
        screenshot_method:"grim", gtk_display_is_wayland, layer_shell_supported}'
}

adapter_screenshot() { grim -s 1 "$1"; }
adapter_geometry() { /usr/bin/python3 "$LAB_SCRIPTS/probes/session_probe.py" geometry; }
adapter_pointer() { [ "$LAB_INPUT_SUPPORTED" = true ] && lab_pointer "$@"; }
adapter_stop() { adapter_cleanup; }
