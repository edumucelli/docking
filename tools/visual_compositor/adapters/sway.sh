#!/bin/bash
# Sway adapter: headless wlroots compositor, the layer-shell path.
#
# Sway is the pilot compositor where Docking's `wayland-layer-shell` backend runs
# against a compositor that fully implements the protocol, so it is the baseline
# lane.
#
# POINTER: headless wlroots creates no input devices (`WLR_LIBINPUT_NO_DEVICES=1`),
# so no client is offered a `wl_pointer`. `swaymsg seat - cursor set` moves the
# cursor but delivers no enter event, so it cannot drive hover/autohide-reveal.
# `adapter_pointer` is therefore declared unavailable rather than implemented as a
# no-op that would make pointer scenarios look like they ran.
#
# IMPORTANT: sway exposes NO compositor-side rectangle for a layer-shell surface.
# Verified against sway-ipc.7.scd: GET_TREE's `shell` field documents only
# xdg_shell/xwayland and there is no layer-surface node type, and GET_OUTPUTS
# returns output bounds only. So `adapter_geometry` returns the OUTPUT layout and
# a null dock rect; the host locates the dock by pixel localization instead.
# Do not "fix" this by reading Docking's own GetHoverAnchor: that reports the
# requested position (docking/platform/backends/wayland/services.py:110-114),
# which is the very thing under test.

SWAY_CONFIG="${XDG_RUNTIME_DIR:-/tmp}/sway.conf"

adapter_capabilities() {
    local probe multi=false panel=false
    probe="$(session_probe_json)"
    [ "${LAB_OUTPUTS:-1}" -gt 1 ] && multi=true
    [ "${LAB_PANEL_HEIGHT:-0}" -gt 0 ] && panel=true
    jq -n --argjson probe "$probe" --argjson multi "$multi" --argjson panel "$panel" '{
        compositor: "sway",
        expected_backend: "wayland-layer-shell",
        native_geometry: false,
        logical_screenshot: true,
        output_changes: true,
        pointer: false,
        placement: $probe.layer_shell_supported,
        multi_output: $multi,
        panel: $panel,
        screenshot_method: "grim",
        gtk_display_is_wayland: $probe.gtk_display_is_wayland,
        layer_shell_supported: $probe.layer_shell_supported
    }'
}

adapter_prepare() {
    # Sway owns swaybar; do not also launch the shared synthetic panel.
    export LAB_PANEL_PROBE=0
    [ "${LAB_PANEL_LAYER:-top}" = overlay ] && export LAB_PANEL_PROBE=1
    COMPOSITOR_LOG="${LAB_DIR}/sway.log"
    local outputs="${LAB_OUTPUTS:-1}"
    local width="${LAB_WIDTH:-1280}"
    local height="${LAB_HEIGHT:-720}"

    export XDG_CURRENT_DESKTOP=sway
    export XDG_SESSION_TYPE=wayland
    export GDK_BACKEND=wayland

    # WLR_HEADLESS_OUTPUTS must be set before sway starts; the per-output
    # `resolution` directives below then apply in order.
    export WLR_BACKENDS=headless
    export WLR_RENDERER=pixman
    export WLR_LIBINPUT_NO_DEVICES=1
    export WLR_HEADLESS_OUTPUTS="$outputs"

    {
        local index
        for index in $(seq 1 "$outputs"); do
            # Lay outputs out left to right at explicit positions. Without this
            # the placement is compositor-chosen, and the harness's per-output
            # bands would not correspond to anything stable.
            printf 'output HEADLESS-%s resolution %sx%s position %s,0\n' \
                "$index" "$width" "$height" "$(( (index - 1) * width ))"
        done

        # A real panel, so the dock has something to respect. swaybar sets a
        # genuine exclusive zone, which shrinks the usable area the dock must
        # place itself inside -- the scenario where a dock that only knows its
        # own geometry overlaps the panel.
        if [ "${LAB_PANEL_HEIGHT:-0}" -gt 0 ] && [ "$LAB_PANEL_PROBE" = 0 ]; then
            printf 'bar {\n  position %s\n  height %s\n  status_command /bin/false\n' \
                "${LAB_PANEL_POSITION:-bottom}" "${LAB_PANEL_HEIGHT}"
            printf '  font pango:DejaVu Sans Mono 10\n  colors {\n'
            printf '    background #303030\n    statusline #ffffff\n'
            printf '    separator #666666\n    focused_workspace #005577 #005577 #ffffff\n'
            printf '    active_workspace #333333 #333333 #ffffff\n'
            printf '    inactive_workspace #222222 #222222 #888888\n'
            printf '    urgent_workspace #900000 #900000 #ffffff\n'
            printf '    binding_mode #900000 #900000 #ffffff\n  }\n}\n'
        fi

        # XWayland would add a second, unrelated display path to the lab.
        printf 'xwayland disable\n'
    } >"$SWAY_CONFIG"
}

adapter_start() {
    sway --config "$SWAY_CONFIG" >"${LAB_DIR:-/tmp}/sway.log" 2>&1 &
    ADAPTER_COMPOSITOR_PID=$!
    wait_for_wayland_socket 80

    # We are not a child of sway, so SWAYSOCK was never exported for us. Discover
    # the IPC socket explicitly rather than relying on swaymsg's own search.
    local socket
    for _ in $(seq 40); do
        for socket in "$XDG_RUNTIME_DIR"/sway-ipc.*.sock; do
            if [ -S "$socket" ]; then
                export SWAYSOCK="$socket"
                break 2
            fi
        done
        sleep 0.25
    done
    [ -n "${SWAYSOCK:-}" ] || { log_adapter "no sway IPC socket appeared"; return 1; }
}

# Sway has no shell API, but the IPC socket answering get_outputs proves the
# compositor itself is up, which is the check that matters here.
adapter_wait_ready() {
    local attempts="${1:-40}"
    for _ in $(seq "$attempts"); do
        kill -0 "$ADAPTER_COMPOSITOR_PID"
        if swaymsg -t get_outputs >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.25
    done
    log_adapter "sway IPC never answered"
    return 1
}

adapter_screenshot() {
    local path="$1"
    timeout 15 grim -s 1 "$path"
}

# Output layout only. `dock_rect` is deliberately null on sway: there is no
# compositor query for a layer surface. The host derives the expected rect from
# the output geometry and localizes the dock from pixels.
adapter_geometry() {
    swaymsg -t get_outputs \
        | jq '{
            outputs: [ .[] | select(.active) | {
                name: .name,
                x: .rect.x, y: .rect.y,
                width: .rect.width, height: .rect.height,
                scale: .scale
            } ],
            dock_rect: null
          }'
}

adapter_scene() {
    local scene="${1:-}"
    swaymsg 'output HEADLESS-1 enable scale 1 transform normal' >/dev/null
    swaymsg "output HEADLESS-1 resolution ${LAB_WIDTH:-1280}x${LAB_HEIGHT:-720} position 0 0" >/dev/null
    if swaymsg -t get_outputs | jq -e 'any(.[]; .name == "HEADLESS-2")' >/dev/null; then
        if [ "${LAB_OUTPUTS:-1}" -gt 1 ]; then
            swaymsg "output HEADLESS-2 enable scale 1 transform normal resolution ${LAB_WIDTH:-1280}x${LAB_HEIGHT:-720} position ${LAB_WIDTH:-1280} 0" >/dev/null
        else swaymsg 'output HEADLESS-2 disable' >/dev/null; fi
    fi
    case "$scene" in
        narrow) swaymsg 'output HEADLESS-1 resolution 360x720' >/dev/null ;;
        scale2) swaymsg 'output HEADLESS-1 scale 2' >/dev/null ;;
        rotated) swaymsg 'output HEADLESS-1 transform 90' >/dev/null ;;
        dual|mixed|negative)
            if ! swaymsg -t get_outputs | jq -e 'any(.[]; .name == "HEADLESS-2")' >/dev/null; then
                swaymsg create_output >/dev/null
            fi
            swaymsg "output HEADLESS-2 enable scale 1 transform normal resolution ${LAB_WIDTH:-1280}x${LAB_HEIGHT:-720} position ${LAB_WIDTH:-1280} 0" >/dev/null
            if [ "$scene" = mixed ]; then
                swaymsg 'output HEADLESS-1 scale 2' >/dev/null
                swaymsg 'output HEADLESS-2 position 640 0' >/dev/null
            elif [ "$scene" = negative ]; then
                swaymsg 'output HEADLESS-2 position -1280 0' >/dev/null
            fi ;;
    esac
    sleep 0.5
}

adapter_change_output() {
    case "$1" in
        resolution) swaymsg 'output HEADLESS-1 resolution 800x600' >/dev/null ;;
        scale) swaymsg 'output HEADLESS-1 scale 2' >/dev/null ;;
        remove) swaymsg 'output HEADLESS-2 disable' >/dev/null ;;
        *) return 1 ;;
    esac
    sleep 1
}

adapter_pointer() { [ "$LAB_INPUT_SUPPORTED" = true ] && lab_pointer "$@"; }

adapter_stop() {
    if [ -n "$ADAPTER_COMPOSITOR_PID" ]; then
        terminate_pid "$ADAPTER_COMPOSITOR_PID"
        ADAPTER_COMPOSITOR_PID=""
    fi
}

adapter_start_frame_clock() {
    /usr/bin/python3 "$LAB_SCRIPTS/probes/frame_probe.py" >"$LAB_DIR/background.log" 2>&1 &
    ADAPTER_FRAME_PID=$!
    sleep 0.5
}
