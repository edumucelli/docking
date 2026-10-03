# Shared input service. A capability is true only after a GTK client receives
# a known motion at the requested coordinates; a protocol request alone is not proof.
ADAPTER_INPUT_PID=""
LAB_INPUT_SUPPORTED=false
LAB_INPUT_SOCKET="$XDG_RUNTIME_DIR/lab-input.sock"

lab_pointer() {
    local origin_x origin_y width height geometry
    geometry="$(adapter_geometry)"
    origin_x="$(jq '[.outputs[].x]|min' <<<"$geometry")"
    origin_y="$(jq '[.outputs[].y]|min' <<<"$geometry")"
    width="$(jq '[.outputs[]|(.x+.width)]|max' <<<"$geometry")"
    height="$(jq '[.outputs[]|(.y+.height)]|max' <<<"$geometry")"
    PYTHONPATH="$(docking_source_pythonpath)" /usr/bin/python3 \
        "$LAB_SCRIPTS/probes/input_probe.py" "$LAB_INPUT_SOCKET" --send \
        "$(( $1 - origin_x ))" "$(( $2 - origin_y ))" \
        "$((width-origin_x))" "$((height-origin_y))" "${3:-0}"
}

start_lab_input() {
    local -a input_args=()
    local input_display="$WAYLAND_DISPLAY"
    if [ -f "$LAB_DIR/outer-display" ]; then
        input_args+=(--x11-display "$(cat "$LAB_DIR/outer-display")"
                     --x11-authority "$(cat "$LAB_DIR/outer-authority")")
    elif [ -n "${LAB_PARENT_DISPLAY:-}" ]; then
        input_display="$LAB_PARENT_DISPLAY"
    fi
    WAYLAND_DISPLAY="$input_display" PYTHONPATH="$(docking_source_pythonpath)" \
        /usr/bin/python3 "$LAB_SCRIPTS/probes/input_probe.py" "$LAB_INPUT_SOCKET" \
        "${input_args[@]}" >"$LAB_DIR/input.log" 2>&1 &
    ADAPTER_INPUT_PID=$!
    for _ in $(seq 40); do
        [ -S "$LAB_INPUT_SOCKET" ] && break
        kill -0 "$ADAPTER_INPUT_PID" 2>/dev/null || break
        sleep 0.1
    done
    if [ ! -S "$LAB_INPUT_SOCKET" ]; then
        terminate_pid "$ADAPTER_INPUT_PID"
        ADAPTER_INPUT_PID=""
        return 0
    fi
    /usr/bin/python3 "$LAB_SCRIPTS/probes/client_probe.py" input \
        "$LAB_DIR/input-delivery.json" >"$LAB_DIR/input-client.log" 2>&1 &
    ADAPTER_PROBE_PID=$!
    local probe_x probe_y geometry
    geometry="$(adapter_geometry)"
    probe_x="$(jq '.outputs[0] | .x + ([320, (.width / 2 | floor)] | min)' <<<"$geometry")"
    probe_y="$(jq '.outputs[0] | .y + ([240, (.height / 2 | floor)] | min)' <<<"$geometry")"
    for _ in $(seq 20); do
        lab_pointer 100 100 272 || true
        sleep 0.1
        lab_pointer "$probe_x" "$probe_y" || true
        sleep 0.2
        if [ -f "$LAB_DIR/input-delivery.json" ] && \
            jq -e --argjson x "$probe_x" --argjson y "$probe_y" \
                '(.x-$x|fabs)<4 and (.y-$y|fabs)<4' \
                "$LAB_DIR/input-delivery.json" >/dev/null; then
            LAB_INPUT_SUPPORTED=true
            break
        fi
    done
    terminate_pid "$ADAPTER_PROBE_PID"
    ADAPTER_PROBE_PID=""
    # Niri's top-left hot corner opens its overview and changes the whole scene.
    if [ "$COMPOSITOR" = niri ] || [ "$COMPOSITOR" = kwin ] || [[ "$COMPOSITOR" = gnome* ]]; then lab_pointer 100 100 || true
    else lab_pointer 0 0 || true; fi
}
