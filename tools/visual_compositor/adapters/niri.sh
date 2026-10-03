#!/bin/bash
# niri adapter: niri nested inside a headless sway.
#
# STATUS: verified. This adapter used to declare niri unrunnable, on the belief
# that it had no nested mode and therefore needed a DRM device this lab cannot
# provide. That belief was wrong (see Dockerfile.arch), and the correction is
# the whole shape of this file:
#
#   sway (headless, wlroots)  <- the parent, supplies an output
#     └── niri (winit backend) <- nested, a plain client of sway
#           └── Docking         <- layer-shell, on niri's own socket
#
# niri selects the winit backend by itself whenever WAYLAND_DISPLAY,
# WAYLAND_SOCKET or DISPLAY is set (src/niri.rs:741-755). Nothing else is
# required: no seat, no DRM device, no extra privilege.
#
# Why sway and not a decoration-free kiosk compositor: a single tiled window
# with no gaps and no borders fills sway's output exactly, so niri's output is
# the parent's output rather than a smaller window inside it. cage was tried
# first and left niri at 1272x688 inside a 1280x720 parent -- a 32px error that
# would have shifted every bottom-edge measurement and looked like a placement
# bug.
#
# Two live sockets exist in one XDG_RUNTIME_DIR while this runs (the parent's,
# then niri's). Both are found by looking for what is *new* rather than by
# globbing `wayland-*` and taking the first match, which would hand Docking the
# parent.
#
# NIRI_SOCKET is niri's IPC socket; Docking's niri backend uses it for window
# tracking, so it is exported for the dock and the probes too.

NIRI_LOG="${LAB_DIR}/niri.log"
NIRI_PARENT_LOG="${LAB_DIR}/niri-parent.log"

# Sockets present right now, one per line. Used to find what a launch added.
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
  "compositor": "niri",
  "expected_backend": "niri",
  "native_geometry": false,
  "pointer": false,
  "placement": $(if [ "$(echo "$probe" | jq -r '.layer_shell_supported')" = true ]; then echo true; else echo false; fi),
  "panel": $(if [ "${LAB_PANEL_HEIGHT:-0}" -gt 0 ]; then echo true; else echo false; fi),
  "screenshot_method": "grim",
  "gtk_display_is_wayland": $(echo "$probe" | jq -r '.gtk_display_is_wayland'),
  "layer_shell_supported": $(echo "$probe" | jq -r '.layer_shell_supported'),
  "note": "niri nested inside a headless sway; its own IPC reports exact output geometry. XDG_CURRENT_DESKTOP=niri selects Docking's native niri backend, which it prefers over generic layer-shell (selection.py:204-211)"
}
JSON
}

adapter_prepare() {
    export XDG_CURRENT_DESKTOP=niri
    export XDG_SESSION_TYPE=wayland
    export GDK_BACKEND=wayland

    # The parent. No bars, no gaps, no borders: a single tiled window must fill
    # the output exactly, so that niri's coordinates are the output's.
    # Deliberately no `output ... bg` line -- it takes a scaling mode, and
    # getting it wrong makes sway fall back to its *default* config, which
    # includes a bar. That failure is quiet and cost two runs to find.
    mkdir -p "$XDG_CONFIG_HOME/sway"
    cat >"$XDG_CONFIG_HOME/sway/config" <<'SWAY'
gaps inner 0
gaps outer 0
default_border none
default_floating_border none
SWAY

    # Closing the input probe must not leave an animated window snapshot behind
    # the dock. Headless nested render loops can retain a stable intermediate frame.
    mkdir -p "$XDG_CONFIG_HOME/niri"
    printf 'animations { off; }\n' >"$XDG_CONFIG_HOME/niri/config.kdl"

    # DISPLAY is unset so nothing in this lane can quietly fall back to X11;
    # niri is given WAYLAND_DISPLAY explicitly instead.
    unset DISPLAY WAYLAND_DISPLAY
}

adapter_start() {
    COMPOSITOR_LOG="$NIRI_LOG"
    local outputs="${LAB_OUTPUTS:-1}"

    local before
    before="$(_wayland_sockets)"

    WLR_BACKENDS=headless \
    WLR_RENDERER=pixman \
    WLR_LIBINPUT_NO_DEVICES=1 \
    WLR_HEADLESS_OUTPUTS="$outputs" \
        sway -c "$XDG_CONFIG_HOME/sway/config" >"$NIRI_PARENT_LOG" 2>&1 &
    ADAPTER_PARENT_PID=$!

    # Liveness check inline: a parent that dies at startup should say so now
    # rather than after the whole socket budget.
    local parent_display=""
    for _ in $(seq 80); do
        if ! kill -0 "$ADAPTER_PARENT_PID" 2>/dev/null; then
            log_adapter "parent sway exited during startup"
            COMPOSITOR_LOG="$NIRI_PARENT_LOG"
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

    # Give sway a moment to finish configuring its output before a client sizes
    # itself against it.
    sleep 2

    before="$(_wayland_sockets)"
    WAYLAND_DISPLAY="$parent_display" niri >"$NIRI_LOG" 2>&1 &
    ADAPTER_COMPOSITOR_PID=$!

    # niri announces its socket by name. Reading that line is exact and race-free
    # -- unlike diffing sockets, it cannot be confused by anything else that
    # happens to open one.
    local niri_display=""
    for _ in $(seq 120); do
        kill -0 "$ADAPTER_COMPOSITOR_PID" 2>/dev/null || {
            log_adapter "niri exited during startup"
            report_compositor_failure
            return 1
        }
        # `|| true` is load-bearing. This runs under `set -e`/`pipefail`, and a
        # variable assignment takes the exit status of its command
        # substitution: on the first poll niri has not written the line yet, so
        # grep exits 1 and the whole session aborts -- killing niri a few
        # milliseconds after it started, which reads as the compositor crashing.
        niri_display="$(grep -oE 'listening on Wayland socket: [A-Za-z0-9._-]+' "$NIRI_LOG" \
            2>/dev/null | tail -1 | awk '{print $NF}' || true)"
        [ -n "$niri_display" ] && break
        sleep 0.25
    done
    if [ -z "$niri_display" ]; then
        log_adapter "niri published no Wayland socket name"
        report_compositor_failure
        return 1
    fi
    export WAYLAND_DISPLAY="$niri_display"
    log_adapter "niri nested on $WAYLAND_DISPLAY (parent $parent_display)"

    local socket
    for _ in $(seq 40); do
        for socket in "$XDG_RUNTIME_DIR"/niri.*.sock; do
            if [ -S "$socket" ]; then
                export NIRI_SOCKET="$socket"
                return 0
            fi
        done
        sleep 0.25
    done
    # The Wayland socket exists but the IPC socket did not appear; the dock's
    # niri backend needs it, so treat that as a failed start rather than
    # continuing into a run that cannot exercise the backend.
    log_adapter "niri Wayland socket came up but no NIRI_SOCKET appeared"
    return 1
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
    log_adapter "niri never produced a capturable frame"
    report_compositor_failure
    return 1
}

# Captures niri itself, not the parent. grim against a wlroots compositor works
# here because niri implements wlr-screencopy for compatibility; going through
# the parent would work too but would drag the parent's frame and bar into every
# measurement.
adapter_screenshot() {
    grim "$1"
}

# Exact, from niri's own IPC -- no pixel inference for this lane. `niri msg
# outputs` reports "Logical position: x, y" and "Logical size: WxH" per output.
adapter_geometry() {
    local dump
    # Same reason as the socket poll above: a bare assignment would abort the
    # session on failure instead of reaching the check on the next line.
    dump="$(niri msg outputs 2>/dev/null || true)"
    if [ -z "$dump" ]; then
        log_adapter "niri msg outputs returned nothing"
        printf '{"outputs": [], "dock_rect": null}\n'
        return 0
    fi
    # The dump goes in as an argument, not down a pipe: with `python3 - <<'PY'`
    # the heredoc *is* python's stdin, so anything piping into it writes to a
    # pipe with no reader and dies of SIGPIPE (exit 141), taking the session
    # with it under `set -e`.
    /usr/bin/python3 - "$dump" <<'PY'
import json
import re
import sys

text = sys.argv[1]
outputs = []
# Each output starts a block; the fields we want follow it.
for block in re.split(r"^Output ", text, flags=re.MULTILINE)[1:]:
    name = block.split("\n", 1)[0].split('"')[1] if '"' in block else "?"
    pos = re.search(r"Logical position:\s*(-?\d+),\s*(-?\d+)", block)
    size = re.search(r"Logical size:\s*(\d+)x(\d+)", block)
    scale = re.search(r"Scale:\s*([\d.]+)", block)
    if not pos or not size:
        continue
    outputs.append(
        {
            "name": name,
            "x": int(pos.group(1)),
            "y": int(pos.group(2)),
            "width": int(size.group(1)),
            "height": int(size.group(2)),
            "scale": float(scale.group(1)) if scale else 1.0,
        }
    )
print(json.dumps({"outputs": outputs, "dock_rect": None}))
PY
}

adapter_pointer() { [ "$LAB_INPUT_SUPPORTED" = true ] && lab_pointer "$@"; }

adapter_stop() {
    # The nested compositor first: it is a client of the parent, so the parent
    # has to outlive it. common.sh's cleanup handles both if we never get here.
    if [ -n "$ADAPTER_COMPOSITOR_PID" ]; then
        terminate_pid "$ADAPTER_COMPOSITOR_PID"
        ADAPTER_COMPOSITOR_PID=""
    fi
    if [ -n "$ADAPTER_PARENT_PID" ]; then
        terminate_pid "$ADAPTER_PARENT_PID"
        ADAPTER_PARENT_PID=""
    fi
}
