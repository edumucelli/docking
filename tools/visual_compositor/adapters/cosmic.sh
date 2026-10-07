#!/bin/bash
# COSMIC adapter (cosmic-comp): cosmic nested inside a headless sway.
#
# STATUS: verified for native placement. This adapter used to declare cosmic unrunnable
# for want of a DRM device. That diagnosis was wrong for the same reason niri's
# was -- cosmic was launched with no parent display, so it fell through to KMS,
# and KMS is where libseat fails. The route below is the one that fixed niri,
# verified end to end with Docking's native COSMIC backend.
#
# The switch is different from niri's, though. niri selects its windowed backend
# from a display variable; cosmic reads COSMIC_BACKEND explicitly
# (src/backend/mod.rs:20-46), accepting `x11`, `winit` and `kms`. Unset, it
# tries X11 then winit when a display variable is set, and goes straight to KMS
# otherwise. Neither the winit nor the X11 backend touches libseat.
#
#   sway (headless, wlroots)     <- parent, supplies the output
#     └── cosmic (winit backend) <- nested, a plain client of sway
#           └── Docking
#
# Same two traps as niri, both of which cost a run to find there:
#   * a single tiled window with no gaps and no borders is the whole output, so
#     the child's coordinates are the output's. cage instead left niri at
#     1272x688 inside a 1280x720 parent, which would read as a placement bug;
#   * two live sockets share one XDG_RUNTIME_DIR, so they are found by diffing
#     against a snapshot rather than by globbing and taking the first match.
#
# GEOMETRY: Docking's COSMIC backend reads the zcosmic_toplevel_info_v1 and
# zcosmic_overlap_notification_v1 protocols, which report toplevels, not layer
# surfaces. The dock's own rect therefore still comes from pixels.

COSMIC_LOG="${LAB_DIR}/cosmic.log"
COSMIC_PARENT_LOG="${LAB_DIR}/cosmic-parent.log"

_wayland_sockets() {
    local socket
    for socket in "$XDG_RUNTIME_DIR"/wayland-*; do
        [ -S "$socket" ] || continue
        printf '%s\n' "${socket##*/}"
    done
}

# Wait for a socket that was not in $1 (a newline-separated snapshot).
_wait_new_socket() {
    local before="$1"
    local attempts="${2:-120}"
    local socket
    for _ in $(seq "$attempts"); do
        while read -r socket; do
            [ -n "$socket" ] || continue
            case "$before" in
                *"$socket"*) continue ;;
            esac
            printf '%s\n' "$socket"
            return 0
        done < <(_wayland_sockets)
        sleep 0.25
    done
    return 1
}

adapter_capabilities() {
    local probe
    probe="$(session_probe_json)"
    cat <<JSON
{
  "compositor": "cosmic",
  "expected_backend": "cosmic",
  "native_geometry": false,
  "cosmic_overlap_supported": $(echo "$probe" | jq '.cosmic_overlap_supported // false'),
  "pointer": false,
  "placement": $(if [ "$(echo "$probe" | jq -r '.layer_shell_supported')" = true ]; then echo true; else echo false; fi),
  "screenshot_method": "grim",
  "gtk_display_is_wayland": $(echo "$probe" | jq -r '.gtk_display_is_wayland'),
  "layer_shell_supported": $(echo "$probe" | jq -r '.layer_shell_supported'),
  "note": "cosmic nested inside a headless sway via COSMIC_BACKEND=winit; not yet executed"
}
JSON
}

adapter_prepare() {
    export XDG_CURRENT_DESKTOP=COSMIC
    export XDG_SESSION_TYPE=wayland
    export GDK_BACKEND=wayland

    # See the niri adapter: sway's config must parse, and `output ... bg` takes a
    # scaling mode. Getting it wrong drops sway back to its default config,
    # which has a bar, which offsets the nested window.
    mkdir -p "$XDG_CONFIG_HOME/sway"
    cat >"$XDG_CONFIG_HOME/sway/config" <<'SWAY'
gaps inner 0
gaps outer 0
default_border none
default_floating_border none
SWAY

    unset DISPLAY WAYLAND_DISPLAY
}

adapter_start() {
    COMPOSITOR_LOG="$COSMIC_LOG"
    local outputs="${LAB_OUTPUTS:-1}"

    local before
    before="$(_wayland_sockets)"

    WLR_BACKENDS=headless \
    WLR_RENDERER=pixman \
    WLR_LIBINPUT_NO_DEVICES=1 \
    WLR_HEADLESS_OUTPUTS="$outputs" \
        sway -c "$XDG_CONFIG_HOME/sway/config" >"$COSMIC_PARENT_LOG" 2>&1 &
    ADAPTER_PARENT_PID=$!

    local parent_display=""
    for _ in $(seq 80); do
        if ! kill -0 "$ADAPTER_PARENT_PID" 2>/dev/null; then
            log_adapter "parent sway exited during startup"
            COMPOSITOR_LOG="$COSMIC_PARENT_LOG"
            report_compositor_failure
            return 1
        fi
        parent_display="$(_wait_new_socket "$before" 1)" && break
        sleep 0.25
    done
    if [ -z "$parent_display" ]; then
        log_adapter "parent sway never published a Wayland socket"
        return 1
    fi
    export LAB_PARENT_DISPLAY="$parent_display"
    log_adapter "parent sway up on $parent_display"
    sleep 2

    # Nothing here logs its socket name the way niri does, so the child's socket
    # is found by diffing against the parent's. That is race-prone in principle
    # (anything else opening a socket in between would be mistaken for cosmic's)
    # and is the first thing to make explicit if this lane misbehaves.
    before="$(_wayland_sockets)"
    WAYLAND_DISPLAY="$parent_display" COSMIC_BACKEND=winit \
        cosmic-comp >"$COSMIC_LOG" 2>&1 &
    ADAPTER_COMPOSITOR_PID=$!

    local cosmic_display=""
    for _ in $(seq 120); do
        if ! kill -0 "$ADAPTER_COMPOSITOR_PID" 2>/dev/null; then
            log_adapter "cosmic-comp exited during startup"
            report_compositor_failure
            return 1
        fi
        cosmic_display="$(_wait_new_socket "$before" 1)" && break
        sleep 0.25
    done
    if [ -z "$cosmic_display" ]; then
        log_adapter "cosmic-comp published no Wayland socket"
        report_compositor_failure
        return 1
    fi
    export WAYLAND_DISPLAY="$cosmic_display"
    log_adapter "cosmic nested on $WAYLAND_DISPLAY (parent $parent_display)"
    return 0
}

adapter_wait_ready() {
    local attempts="${1:-60}"
    local probe="$XDG_RUNTIME_DIR/.ready.png"
    for _ in $(seq "$attempts"); do
        kill -0 "$ADAPTER_COMPOSITOR_PID"
        if grim "$probe" 2>/dev/null; then
            rm -f "$probe"
            return 0
        fi
        sleep 0.5
    done
    log_adapter "cosmic-comp never produced a capturable frame"
    report_compositor_failure
    return 1
}

adapter_screenshot() {
    grim "$1"
}

adapter_geometry() {
    local probe="$XDG_RUNTIME_DIR/.geometry.png"
    grim "$probe"
    local dims width height
    dims="$(png_dimensions "$probe")"
    rm -f "$probe"
    width="${dims%x*}"
    height="${dims#*x}"

    jq -n --argjson w "$width" --argjson h "$height" \
        '{outputs: [{name: "headless-1", x: 0, y: 0, width: $w, height: $h, scale: 1}],
          dock_rect: null}'
}

adapter_pointer() { [ "$LAB_INPUT_SUPPORTED" = true ] && lab_pointer "$@"; }

adapter_stop() {
    # Child first: the nested compositor is a client of the parent.
    if [ -n "$ADAPTER_COMPOSITOR_PID" ]; then
        terminate_pid "$ADAPTER_COMPOSITOR_PID"
        ADAPTER_COMPOSITOR_PID=""
    fi
    if [ -n "$ADAPTER_PARENT_PID" ]; then
        terminate_pid "$ADAPTER_PARENT_PID"
        ADAPTER_PARENT_PID=""
    fi
}
