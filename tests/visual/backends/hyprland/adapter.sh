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
}
adapter_start() {
    start_sway_parent
    # Hyprland needs XDG shell v6 and DRM buffers. Xvfb falls back to llvmpipe,
    # so nest Weston inside VirGL-backed Sway to preserve both. Screenshots
    # and physical input still come from the outer Sway session.
    WAYLAND_DISPLAY="$LAB_PARENT_DISPLAY" weston --backend=wayland-backend.so --renderer=gl \
        --shell=kiosk-shell.so --socket=wayland-hypr-parent --no-config --idle-time=0 \
        --width="${LAB_WIDTH:-1280}" --height="${LAB_HEIGHT:-720}" --fullscreen \
        >"$LAB_DIR/weston-parent.log" 2>&1 &
    ADAPTER_NESTED_PARENT_PID=$!
    for _ in $(seq 120); do
        kill -0 "$ADAPTER_NESTED_PARENT_PID" 2>/dev/null || { tail -60 "$LAB_DIR/weston-parent.log"; return 1; }
        [ -S "$XDG_RUNTIME_DIR/wayland-hypr-parent" ] && break
        sleep 0.25
    done
    [ -S "$XDG_RUNTIME_DIR/wayland-hypr-parent" ] || return 1
    WAYLAND_DISPLAY=wayland-hypr-parent Hyprland \
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
            [ "${display##*/}" = wayland-hypr-parent ] && continue
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
adapter_wait_ready() { hyprctl -j monitors >"$LAB_DIR/monitors.json"; }
adapter_screenshot() { capture_parent "$1"; }
adapter_stop() { adapter_cleanup; }
