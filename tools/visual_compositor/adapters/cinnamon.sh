#!/bin/bash
# Cinnamon adapter: Muffin nested inside Xvfb. The #347 lane.
#
# This is the compositor the whole investigation started from: on Cinnamon
# Wayland without layer-shell, `window.move()` is silently ignored and the dock
# renders wherever Muffin puts it.
#
# NESTING: Muffin's `--nested` mode needs an outer display to nest into, which is
# what Xvfb provides. Muffin then supplies the Wayland server Docking connects to,
# so native test applications connect to Wayland even though Cinnamon has an
# X11 parent. Docking may use XWayland for its dock role on older Muffin.
# `--nested` and `--wayland` come from Muffin's option context, which
# cinnamon merges into its own -- cinnamon's src/main.c only defines --version.
#
# GEOMETRY: unlike sway, Cinnamon *can* report the dock's real rectangle, via
# org.Cinnamon.Eval -> global.get_window_actors() -> get_frame_rect(). That makes
# this the reference lane for geometry rather than a pixel-derived one.

PROBE="${LAB_SCRIPTS}/probes/cinnamon_probe.py"

adapter_capabilities() {
    # Probed, not hard-coded: whether Muffin implements layer-shell decides which
    # backend runs, and it varies by version (Muffin gained layer-shell in 6.7;
    # this image has 6.6.x). Claiming one answer would be wrong on the other.
    PYTHONPATH="$(docking_source_pythonpath)" /usr/bin/python3 "$PROBE" capabilities
}

adapter_start_maximized_window() {
    /usr/bin/python3 "$PROBE" maximized-window >"$LAB_DIR/maximized-window.log" 2>&1 &
    ADAPTER_PROBE_PID=$!
}

adapter_reservation() {
    /usr/bin/python3 "$PROBE" reservation
}

adapter_prepare() {
    COMPOSITOR_LOG="${LAB_DIR}/cinnamon.log"
    local width="${LAB_WIDTH:-1280}"
    local height="${LAB_HEIGHT:-720}"
    local monitors="${LAB_OUTPUTS:-1}"

    export XDG_CURRENT_DESKTOP=X-Cinnamon
    export XDG_SESSION_DESKTOP=cinnamon-wayland
    export XDG_SESSION_TYPE=wayland
    # Exercise Docking's automatic transport selection. Test clients still
    # connect to this compositor's Wayland socket by default.
    unset GDK_BACKEND
    export LIBGL_ALWAYS_SOFTWARE=1

    # Muffin kept Mutter's variable names -- a naming mistake the original
    # investigation lost time to (docs/HEADLESS_WAYLAND_TESTING.local.md:161-165):
    # MUFFIN_DEBUG_* are silently ignored and the screen stays 800x600.
    export MUTTER_DEBUG_DUMMY_MODE_SPECS="${width}x${height}"
    export MUTTER_DEBUG_NUM_DUMMY_MONITORS="$monitors"

    # Cinnamon reads this home-relative path during startup.
    mkdir -p "$HOME/.config/autostart"
}

adapter_start() {
    # xvfb-run allocates a free display and tears its X server down with the
    # child, so the display number is never hard-coded.
    # Muffin initializes its XWayland window manager through GDK X11. This
    # applies only to Cinnamon; native test clients use its Wayland socket.
    xvfb-run -a -s "-screen 0 ${LAB_WIDTH:-1280}x${LAB_HEIGHT:-720}x24 +extension GLX +render -noreset" \
        bash -c 'printf "%s\n" "$DISPLAY" >"$LAB_DIR/outer-display";
                 printf "%s\n" "$XAUTHORITY" >"$LAB_DIR/outer-authority";
                 exec env GDK_BACKEND=x11 cinnamon --nested --wayland' \
        >"${LAB_DIR}/cinnamon.log" 2>&1 &
    ADAPTER_COMPOSITOR_PID=$!

    # Never expose the outer X display to clients. Once ready, the adapter
    # publishes only the compositor's own XWayland display for Docking's surface.
    unset DISPLAY

    # Cinnamon is a full desktop: allow well beyond the wlroots budget.
    wait_for_wayland_socket 240
}

adapter_wait_ready() {
    # Two gates, because a Wayland socket existing is not the same as the shell
    # being up: org.Cinnamon has no owner until the JS shell finishes starting.
    # (docs/HEADLESS_WAYLAND_TESTING.local.md:502-512)
    local attempts="${1:-120}"
    for _ in $(seq "$attempts"); do
        kill -0 "$ADAPTER_COMPOSITOR_PID"
        if /usr/bin/python3 "$PROBE" geometry >/dev/null 2>&1; then
            # Missing optional system services generate startup banners that
            # overlap the pixel oracle. Keep the errors in cinnamon.log.
            /usr/bin/python3 "$PROBE" quiet-startup
            local environment
            environment="$(/usr/bin/python3 "$PROBE" xwayland-environment)"
            export DISPLAY="$(jq -r '.DISPLAY // empty' <<<"$environment")"
            export XAUTHORITY="$(jq -r '.XAUTHORITY // empty' <<<"$environment")"
            return 0
        fi
        sleep 0.5
    done
    log_adapter "Cinnamon shell API never became ready"
    return 1
}

adapter_screenshot() {
    /usr/bin/python3 "$PROBE" screenshot "$1"
}

adapter_geometry() {
    /usr/bin/python3 "$PROBE" geometry | jq '{outputs, dock_rect}'
}

adapter_pointer() { [ "$LAB_INPUT_SUPPORTED" = true ] && lab_pointer "$@"; }

adapter_stop() {
    if [ -n "$ADAPTER_COMPOSITOR_PID" ]; then
        terminate_pid "$ADAPTER_COMPOSITOR_PID"
        ADAPTER_COMPOSITOR_PID=""
    fi
}
