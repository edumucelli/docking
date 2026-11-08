#!/bin/bash
# Weston intentionally tests reduced compatibility: its desktop shell does not
# provide layer-shell. The private Xvfb parent permits independent screenshots
# without relying on a compositor-specific screenshot privilege.
adapter_prepare() {
    COMPOSITOR_LOG="$LAB_DIR/weston.log"
    export XDG_CURRENT_DESKTOP=Weston XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland
    export LIBGL_ALWAYS_SOFTWARE=1
    unset DISPLAY
}

adapter_start() {
    xvfb-run -a -s "-screen 0 ${LAB_WIDTH:-1280}x${LAB_HEIGHT:-720}x24 +extension GLX +render -noreset" \
        bash -c 'printf "%s\n" "$DISPLAY" >"$LAB_DIR/outer-display";
                 printf "%s\n" "$XAUTHORITY" >"$LAB_DIR/outer-authority";
                 exec weston --backend=x11-backend.so --use-pixman --no-config \
                     --width="${LAB_WIDTH:-1280}" --height="${LAB_HEIGHT:-720}" \
                     --idle-time=0' >"$COMPOSITOR_LOG" 2>&1 &
    ADAPTER_COMPOSITOR_PID=$!
    wait_for_wayland_socket 80
}

adapter_wait_ready() {
    for _ in $(seq "${1:-60}"); do
        kill -0 "$ADAPTER_COMPOSITOR_PID" 2>/dev/null || { report_compositor_failure; return 1; }
        if [ "$(session_probe_json | jq -r .gtk_display_is_wayland)" = true ]; then
            sleep 1
            return 0
        fi
        sleep 0.25
    done
    return 1
}

adapter_capabilities() {
    session_probe_json | jq '{compositor:"weston", expected_backend:"reduced",
        placement:false, native_geometry:false, pointer:false,
        unsupported_reasons:{placement:"Weston desktop shell does not expose layer-shell or a Docking shell bridge; absolute dock placement is unavailable"},
        screenshot_method:"private-xvfb", gtk_display_is_wayland, layer_shell_supported}'
}

adapter_screenshot() {
    GDK_BACKEND=x11 XAUTHORITY="$(cat "$LAB_DIR/outer-authority")" \
        /usr/bin/python3 "$LAB_SCRIPTS/probes/x11_capture.py" \
        "$(cat "$LAB_DIR/outer-display")" "$1"
}
adapter_geometry() { /usr/bin/python3 "$LAB_SCRIPTS/probes/session_probe.py" geometry; }
adapter_pointer() { [ "$LAB_INPUT_SUPPORTED" = true ] && lab_pointer "$@"; }
adapter_stop() { adapter_cleanup; }
