# A headless Sway parent provides a visible framebuffer for nested compositors.
prepare_sway_parent() {
    mkdir -p "$XDG_CONFIG_HOME/sway"
    cat >"$XDG_CONFIG_HOME/sway/config" <<EOF
gaps inner 0
gaps outer 0
default_border none
default_floating_border none
output * resolution ${LAB_WIDTH:-1280}x${LAB_HEIGHT:-720}
xwayland disable
EOF
}

start_sway_parent() {
    local socket
    WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1 \
        sway -c "$XDG_CONFIG_HOME/sway/config" >"$LAB_DIR/parent.log" 2>&1 &
    ADAPTER_PARENT_PID=$!
    for _ in $(seq 80); do
        kill -0 "$ADAPTER_PARENT_PID" 2>/dev/null || return 1
        for socket in "$XDG_RUNTIME_DIR"/wayland-*; do
            if [ -S "$socket" ]; then
                export LAB_PARENT_DISPLAY="${socket##*/}"
                sleep 1
                return 0
            fi
        done
        sleep 0.25
    done
    return 1
}

capture_parent() {
    WAYLAND_DISPLAY="$LAB_PARENT_DISPLAY" grim -s 1 "$1"
}
