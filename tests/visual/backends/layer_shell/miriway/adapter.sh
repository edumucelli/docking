#!/usr/bin/env bash
source "$LAB_SCRIPTS/adapters/nested.sh"
adapter_prepare() {
    COMPOSITOR_LOG="$LAB_DIR/miriway.log"
    export XDG_CURRENT_DESKTOP=Miriway XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland
    export LIBGL_ALWAYS_SOFTWARE=1
    # Explicitly grant shell protocols to the isolated test clients.
    cat >"$XDG_CONFIG_HOME/miriway-shell.config" <<'CONFIG'
add-wayland-extensions=zwlr_layer_shell_v1:zwlr_foreign_toplevel_manager_v1
CONFIG
}
adapter_start() {
    start_x11_child miriway-shell --x11-output "${LAB_WIDTH:-1280}x${LAB_HEIGHT:-720}"
}
adapter_capabilities() {
    session_probe_json | jq '{compositor:"miriway", expected_backend:"wayland-layer-shell",
        placement:.layer_shell_supported, native_geometry:false, pointer:false,
        gtk_display_is_wayland, layer_shell_supported, screenshot_method:"private-xvfb"}'
}
adapter_screenshot() { capture_x11_parent "$1"; }
adapter_geometry() { /usr/bin/python3 "$LAB_SCRIPTS/probes/session_probe.py" geometry; }
adapter_pointer() { lab_pointer "$@"; }
adapter_stop() { adapter_cleanup; }
