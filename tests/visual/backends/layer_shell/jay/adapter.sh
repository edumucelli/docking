#!/usr/bin/env bash
source "$LAB_SCRIPTS/adapters/nested.sh"
adapter_prepare() {
    COMPOSITOR_LOG="$LAB_DIR/jay.log"
    export XDG_CURRENT_DESKTOP=jay XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland
    export LIBGL_ALWAYS_SOFTWARE=1 JAY_NO_REALTIME=1
    mkdir -p "$XDG_CONFIG_HOME/jay"
    # Grant the documented protocols only inside this private harness session.
    cat >"$XDG_CONFIG_HOME/jay/config.toml" <<'TOML'
[[clients]]
match = {}
capabilities = ["layer-shell", "foreign-toplevel-manager", "foreign-toplevel-list", "workspace-manager", "screencopy", "idle-notifier"]
TOML
}
adapter_start() { start_x11_child jay run --backends x11; }
adapter_capabilities() {
    session_probe_json | jq '{compositor:"jay", expected_backend:"wayland-layer-shell",
        placement:.layer_shell_supported, native_geometry:false, pointer:false,
        gtk_display_is_wayland, layer_shell_supported, screenshot_method:"private-xvfb"}'
}
adapter_screenshot() { capture_x11_parent "$1"; }
adapter_geometry() { /usr/bin/python3 "$LAB_SCRIPTS/probes/session_probe.py" geometry; }
adapter_pointer() { lab_pointer "$@"; }
adapter_stop() { adapter_cleanup; }
