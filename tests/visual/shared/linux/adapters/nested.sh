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
    WLR_BACKENDS=headless WLR_RENDERER="${LAB_PARENT_RENDERER:-pixman}" \
        WLR_RENDERER_ALLOW_SOFTWARE=1 WLR_LIBINPUT_NO_DEVICES=1 \
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

# X11 nesting gives compositors with restricted capture protocols an independent
# framebuffer and real XTest input, without connecting to the user's display.
start_x11_child() {
    local display_file="$LAB_DIR/outer-display"
    Xvfb -displayfd 3 -screen 0 "${LAB_WIDTH:-1280}x${LAB_HEIGHT:-720}x24" \
        -nolisten tcp -ac +extension GLX -noreset 3>"$display_file" \
        >"$LAB_DIR/xvfb.log" 2>&1 &
    ADAPTER_PARENT_PID=$!
    for _ in $(seq 80); do
        kill -0 "$ADAPTER_PARENT_PID" 2>/dev/null || return 1
        [ -s "$display_file" ] && break
        sleep 0.1
    done
    export DISPLAY=":$(cat "$display_file")"
    printf '%s\n' "$DISPLAY" >"$display_file"
    touch "$LAB_DIR/outer-authority"
    "$@" >"$COMPOSITOR_LOG" 2>&1 &
    ADAPTER_COMPOSITOR_PID=$!
    wait_for_wayland_socket 160
    local window
    for _ in $(seq 80); do
        window="$(xdotool search --onlyvisible --pid "$ADAPTER_COMPOSITOR_PID" 2>/dev/null | head -1 || true)"
        [ -n "$window" ] && break
        sleep 0.1
    done
    [ -n "$window" ] || { report_compositor_failure; return 1; }
    xdotool windowsize "$window" "${LAB_WIDTH:-1280}" "${LAB_HEIGHT:-720}"
    xdotool windowmove "$window" 0 0
    xdotool windowfocus "$window"
    unset DISPLAY
}

capture_x11_parent() {
    GDK_BACKEND=x11 XAUTHORITY="$(cat "$LAB_DIR/outer-authority")" \
        /usr/bin/python3 "$LAB_SCRIPTS/probes/x11_capture.py" \
        "$(cat "$LAB_DIR/outer-display")" "$1"
}
