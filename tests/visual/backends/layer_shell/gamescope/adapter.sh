#!/usr/bin/env bash
source "$LAB_SCRIPTS/adapters/nested.sh"
adapter_prepare() {
    COMPOSITOR_LOG="$LAB_DIR/gamescope.log"
    export XDG_CURRENT_DESKTOP=gamescope XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland
    export LIBGL_ALWAYS_SOFTWARE=1
}
adapter_start() {
    start_x11_child gamescope --backend sdl --expose-wayland \
        -W "${LAB_WIDTH:-1280}" -H "${LAB_HEIGHT:-720}" -- /bin/sleep infinity
    # Exercise the same automatic display selection as a normal GameScope launch.
    export GAMESCOPE_WAYLAND_DISPLAY="$WAYLAND_DISPLAY"
}
adapter_capabilities() {
    session_probe_json | jq '{compositor:"gamescope", expected_backend:"wayland-layer-shell",
        placement:.layer_shell_supported, native_geometry:false, pointer:false,
        gtk_display_is_wayland, layer_shell_supported, screenshot_method:"private-xvfb"}'
}
adapter_screenshot() { capture_x11_parent "$1"; }
adapter_geometry() { /usr/bin/python3 "$LAB_SCRIPTS/probes/session_probe.py" geometry; }
adapter_pointer() { lab_pointer "$@"; }
adapter_stop() { adapter_cleanup; }
