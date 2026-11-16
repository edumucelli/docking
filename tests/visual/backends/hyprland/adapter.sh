# Hyprland's public nested Aquamarine backend. Requires working EGL/DRM.
source "$LAB_SCRIPTS/adapters/nested.sh"
adapter_prepare() {
    # Instance signatures are long; Unix socket paths must fit sun_path.
    export XDG_RUNTIME_DIR="$(mktemp -d /tmp/h.XXXXXX)"
    COMPOSITOR_LOG="$LAB_DIR/hyprland.log"
    export XDG_CURRENT_DESKTOP=Hyprland XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland
    export AQ_BACKENDS=wayland LIBGL_ALWAYS_SOFTWARE="${LAB_SOFTWARE_RENDERING:-1}"
    unset DISPLAY
    prepare_sway_parent
    mkdir -p "$XDG_CONFIG_HOME/hypr"
    # Exercise the supported Hyprlang dispatch API rather than generated Lua defaults.
    cat >"$XDG_CONFIG_HOME/hypr/hyprland.conf" <<EOF
monitor = , ${LAB_WIDTH:-1280}x${LAB_HEIGHT:-720}@60, auto, 1
workspace = 1, default:true, persistent:true
workspace = 2, persistent:true
general {
    border_size = 0
    gaps_in = 0
    gaps_out = 0
}
animations {
    enabled = false
}
misc {
    disable_hyprland_logo = true
    disable_splash_rendering = true
}
debug {
    disable_logs = false
    enable_stdout_logs = true
}
EOF
    mkdir -p "$XDG_CONFIG_HOME/niri"
    printf 'animations { off; }\nhotkey-overlay { skip-at-startup; }\nlayout { gaps 0; border { off; }; focus-ring { off; }; }\nwindow-rule { open-fullscreen true; }\n' \
        >"$XDG_CONFIG_HOME/niri/config.kdl"
}
adapter_start() {
    start_sway_parent
    # Hyprland needs newer shell protocols and DRM buffers. Xvfb falls back to
    # llvmpipe, so keep the working VirGL-backed Sway session. Weston supplies
    # XDG shell v6 but its wl_compositor v5 is still too old for Hyprland;
    # nest Niri instead. Screenshots and input still come from outer Sway.
    WAYLAND_DISPLAY="$LAB_PARENT_DISPLAY" niri >"$LAB_DIR/niri-parent.log" 2>&1 &
    ADAPTER_NESTED_PARENT_PID=$!
    local parent_display=""
    for _ in $(seq 120); do
        kill -0 "$ADAPTER_NESTED_PARENT_PID" 2>/dev/null || { tail -60 "$LAB_DIR/niri-parent.log"; return 1; }
        parent_display="$(sed -n 's/.*listening on Wayland socket: //p' "$LAB_DIR/niri-parent.log" | tail -1)"
        [ -n "$parent_display" ] && break
        sleep 0.25
    done
    [ -S "$XDG_RUNTIME_DIR/$parent_display" ] || return 1
    WAYLAND_DISPLAY="$parent_display" Hyprland --config "$XDG_CONFIG_HOME/hypr/hyprland.conf" \
        >"$COMPOSITOR_LOG" 2>&1 &
    ADAPTER_COMPOSITOR_PID=$!
    for _ in $(seq 120); do
        if ! kill -0 "$ADAPTER_COMPOSITOR_PID" 2>/dev/null; then
            for log in "$XDG_RUNTIME_DIR"/hypr/*/hyprland.log; do
                [ -f "$log" ] || continue
                cp "$log" "$LAB_DIR/hyprland-backend.log"
                tail -60 "$log"
            done
            report_compositor_failure
            return 1
        fi
        for display in "$XDG_RUNTIME_DIR"/wayland-*; do
            [ -S "$display" ] || continue
            [ "${display##*/}" = "$LAB_PARENT_DISPLAY" ] && continue
            [ "${display##*/}" = "$parent_display" ] && continue
            for socket in "$XDG_RUNTIME_DIR"/hypr/*/.socket.sock; do
                [ -S "$socket" ] || continue
                export HYPRLAND_INSTANCE_SIGNATURE="$(basename "$(dirname "$socket")")"
                export WAYLAND_DISPLAY="${display##*/}"
                return 0
            done
        done
        sleep 0.25
    done
    return 1
}
adapter_wait_ready() {
    hyprctl -j monitors >"$LAB_DIR/monitors.json"
    # Startup notices animate across the capture while the first dock maps.
    hyprctl dismissnotify -1
}
adapter_capabilities() {
    session_probe_json | jq '{compositor:"hyprland", expected_backend:"hyprland",
        placement:.layer_shell_supported, native_geometry:false, pointer:false,
        gtk_display_is_wayland, layer_shell_supported, screenshot_method:"parent-grim"}'
}
adapter_geometry() { /usr/bin/python3 "$LAB_SCRIPTS/probes/session_probe.py" geometry; }
adapter_screenshot() { capture_parent "$1"; }
adapter_pointer() { lab_pointer "$@"; }
adapter_stop() { adapter_cleanup; }
