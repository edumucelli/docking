#!/bin/bash
# Shared lifecycle for compositor adapters.
#
# The process discipline here is lifted from packaging/deb/runtime-smoke.sh, which
# is the repo's proven headless-compositor runner. The comments cite the source
# line ranges so the two can be kept in step.

set -euo pipefail

# Empty-string sentinels, never stale PIDs: `[ -z "$pid" ] || kill "$pid"`.
# Provenance: runtime-smoke.sh:11-23.
ADAPTER_APP_PID=""
ADAPTER_PROBE_PID=""
ADAPTER_COMPOSITOR_PID=""
# Adapters whose compositor runs *nested* inside another one (niri, cosmic) own
# two processes. The parent must outlive the child, so it is torn down last.
ADAPTER_PARENT_PID=""

log_adapter() {
    printf '[adapter] %s\n' "$*" >&2
}

# The vendored dependency directory the packaged launcher also uses. Docking's
# Wayland runtime imports pywayland protocol bindings that Debian's
# python3-pywayland does not ship, so the image builds them from source into
# this layout (see the Dockerfile's pywayland-builder stage).
docking_vendor_path() {
    local minor
    minor="$(/usr/bin/python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    printf '/usr/lib/docking/vendor-python%s' "$minor"
}

# PYTHONPATH for running Docking from the mounted source tree. Source code is
# layered over the vendored dependency bundles, mirroring what
# packaging/deb/docking's launcher constructs
# (docs/HEADLESS_WAYLAND_TESTING.local.md:529-534).
docking_source_pythonpath() {
    printf '%s:%s' "${DOCKING_SOURCE:-/src}" "$(docking_vendor_path)"
}

# JSON describing the live session: whether a GTK client here actually gets a
# Wayland display, and whether layer-shell is advertised.
#
# Every adapter merges this into its capability report so the harness can refuse
# to present results from a session that was not really Wayland. If the probe
# itself fails the answer is reported as "not Wayland" rather than omitted:
# failing loudly beats claiming a Wayland result we cannot substantiate.
session_probe_json() {
    local probe="${LAB_SCRIPTS}/probes/session_probe.py"
    if [ ! -f "$probe" ]; then
        # Say so rather than swallowing it: a missing probe would otherwise
        # surface only as a misleading "session is not Wayland".
        log_adapter "session probe not found at $probe"
        echo '{"gtk_display_is_wayland": false, "layer_shell_supported": false}'
        return 0
    fi
    PYTHONPATH="$(docking_source_pythonpath)" /usr/bin/python3 "$probe" capabilities 2>/dev/null \
        || echo '{"gtk_display_is_wayland": false, "layer_shell_supported": false}'
}

# Print "WIDTHxHEIGHT" for a PNG, by reading the IHDR chunk directly.
#
# Compositors with no output-query IPC (labwc, wayfire, cage) still have to tell
# the host where the output is, because the host derives the expected dock band
# from it. The captured framebuffer size is the honest answer for a single
# output at the origin -- it is measured, not assumed from configuration.
png_dimensions() {
    /usr/bin/python3 - "$1" <<'PY'
import struct
import sys

with open(sys.argv[1], "rb") as handle:
    header = handle.read(24)
if header[:8] != b"\x89PNG\r\n\x1a\n":
    raise SystemExit(f"not a PNG: {sys.argv[1]}")
width, height = struct.unpack(">II", header[16:24])
print(f"{width}x{height}")
PY
}

# SIGTERM, bounded grace, then SIGKILL. A bare `wait` on a process that ignores
# SIGTERM blocks the whole run, so every teardown path goes through this.
# $1 = pid, $2 = grace attempts (default 20 x 0.25s = 5s)
terminate_pid() {
    local pid="$1"
    local attempts="${2:-20}"
    [ -n "$pid" ] || return 0
    kill -TERM "$pid" 2>/dev/null || true
    local _attempt
    for _attempt in $(seq "$attempts"); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.25
    done
    if kill -0 "$pid" 2>/dev/null; then
        log_adapter "pid $pid ignored SIGTERM; killing"
        kill -KILL "$pid" 2>/dev/null || true
    fi
    wait "$pid" 2>/dev/null || true
    return 0
}

# Terminate only the processes this run started. Never pkill/killall: the lab
# must not touch anything it does not own (see the warning in
# docs/HEADLESS_WAYLAND_TESTING.local.md:880).
adapter_cleanup() {
    local status=$?
    trap - EXIT
    if [ -n "${ADAPTER_FRAME_PID:-}" ]; then terminate_pid "$ADAPTER_FRAME_PID"; ADAPTER_FRAME_PID=""; fi
    if [ -n "${ADAPTER_INPUT_PID:-}" ]; then
        terminate_pid "$ADAPTER_INPUT_PID"
        ADAPTER_INPUT_PID=""
    fi
    if [ -n "$ADAPTER_PROBE_PID" ]; then
        terminate_pid "$ADAPTER_PROBE_PID"
        ADAPTER_PROBE_PID=""
    fi
    if [ -n "$ADAPTER_APP_PID" ]; then
        terminate_pid "$ADAPTER_APP_PID"
        ADAPTER_APP_PID=""
    fi
    stop_lab_panel
    if [ -n "$ADAPTER_COMPOSITOR_PID" ]; then
        terminate_pid "$ADAPTER_COMPOSITOR_PID"
        ADAPTER_COMPOSITOR_PID=""
    fi
    # After the child: a nested compositor is a client of the parent, so killing
    # the parent first would leave the child to die of a broken connection.
    if [ -n "$ADAPTER_PARENT_PID" ]; then
        terminate_pid "$ADAPTER_PARENT_PID"
        ADAPTER_PARENT_PID=""
    fi
    return "$status"
}

# Wait for the compositor to publish a Wayland socket.
#
# Discovery globs $XDG_RUNTIME_DIR/wayland-* and tests [ -S ] rather than
# hard-coding wayland-0; the liveness check inside the loop fails fast when the
# compositor dies instead of burning the whole timeout, and the assert after the
# loop refuses to proceed on timeout.
# Provenance: runtime-smoke.sh:33-41.
wait_for_wayland_socket() {
    local attempts="${1:-80}"
    local socket
    for _ in $(seq "$attempts"); do
        if ! kill -0 "$ADAPTER_COMPOSITOR_PID" 2>/dev/null; then
            # The compositor exited rather than being slow. Say why instead of
            # letting the loop burn its whole budget and reporting a bare
            # timeout -- for a compositor that cannot start at all, the
            # difference between "no socket" and "died with this error" is the
            # whole diagnosis.
            log_adapter "compositor exited during startup"
            report_compositor_failure
            return 1
        fi
        for socket in "$XDG_RUNTIME_DIR"/wayland-*; do
            if [ -S "$socket" ]; then
                export WAYLAND_DISPLAY="${socket##*/}"
                return 0
            fi
        done
        sleep 0.5
    done
    log_adapter "no Wayland socket appeared within $((attempts / 2))s"
    report_compositor_failure
    return 1
}

# Start the synthetic layer-shell panel when a panel is configured.
#
# sway uses swaybar, which is a real panel; compositors that ship none (labwc,
# wayfire) use probes/panel_probe.py, which is a real layer-shell surface with a
# real exclusive zone. Having the case runnable on more than one compositor --
# and against a different panel implementation -- is what makes the resulting
# finding a statement about Docking rather than about swaybar.
ADAPTER_PANEL_PID=""

start_lab_panel() {
    [ "${LAB_PANEL_HEIGHT:-0}" -gt 0 ] || return 0
    [ "${LAB_PANEL_PROBE:-1}" = 1 ] || return 0

    /usr/bin/python3 "${LAB_SCRIPTS}/probes/panel_probe.py" \
        "$LAB_PANEL_HEIGHT" "${LAB_PANEL_POSITION:-bottom}" \
        >"${LAB_DIR}/panel.log" 2>&1 &
    ADAPTER_PANEL_PID=$!

    # Give it time to map and publish its exclusive zone before the dock starts,
    # otherwise the dock may legitimately not see a reservation yet and the case
    # would be timing-dependent rather than behavioural.
    local _
    for _ in $(seq 40); do
        kill -0 "$ADAPTER_PANEL_PID" 2>/dev/null || {
            log_adapter "panel probe exited during startup"
            tail -n 10 "${LAB_DIR}/panel.log" >&2 || true
            ADAPTER_PANEL_PID=""
            return 1
        }
        sleep 0.25
    done
    return 0
}

stop_lab_panel() {
    [ -n "$ADAPTER_PANEL_PID" ] || return 0
    terminate_pid "$ADAPTER_PANEL_PID"
    ADAPTER_PANEL_PID=""
}

# Adapters that want their log surfaced on failure set COMPOSITOR_LOG.
report_compositor_failure() {
    [ -n "${COMPOSITOR_LOG:-}" ] || return 0
    [ -f "$COMPOSITOR_LOG" ] || return 0
    log_adapter "--- last lines of $COMPOSITOR_LOG ---"
    tail -n 25 "$COMPOSITOR_LOG" >&2 || true
    log_adapter "--- end ---"
}

# A Wayland socket existing is not the same as the desktop being ready.
# Provenance: docs/HEADLESS_WAYLAND_TESTING.local.md:502-512 (the second gate).
# Adapters override this when they have a shell/greeter API to poll; the default
# is a no-op so a compositor with no shell API can still run.
adapter_wait_ready() {
    return 0
}

# Launch Docking and wait until its D-Bus service answers.
#
# GetCount is the readiness probe; the loop re-checks liveness so a crashed app
# fails immediately rather than after the full budget.
# Provenance: runtime-smoke.sh:58-72.
#
# $1 = log path
# $2 = launch mode: "source" or "installed"
start_docking() {
    local log_path="$1"
    local mode="${2:-source}"

    export DOCKING_LOG_LEVEL="${DOCKING_LOG_LEVEL:-INFO}"

    if [ "$mode" = "installed" ]; then
        if [ ! -x /usr/bin/docking ]; then
            log_adapter "mode=installed but /usr/bin/docking is not in this image"
            return 1
        fi
        /usr/bin/docking >"$log_path" 2>&1 &
    else
        # Source-over-vendored: bypass the installed launcher, whose own
        # PYTHONPATH prepending can still select installed code
        # (docs/HEADLESS_WAYLAND_TESTING.local.md:645-655).
        PYTHONPATH="$(docking_source_pythonpath)" \
            /usr/bin/python3 -m docking.launcher \
            >"$log_path" 2>&1 &
    fi
    ADAPTER_APP_PID=$!

    local ready=false
    for _ in $(seq 60); do
        kill -0 "$ADAPTER_APP_PID" 2>/dev/null || break
        if gdbus call --session --dest org.docking.Docking \
            --object-path /org/docking/Docking \
            --method org.docking.Docking.Items1.GetCount >/dev/null 2>&1; then
            ready=true
            break
        fi
        sleep 0.5
    done
    if [ "$ready" != true ]; then
        # Reap the failed process here. Returning with ADAPTER_APP_PID still set
        # would let the next case overwrite it, leaking a process that keeps
        # running against the compositor and can influence later cases.
        log_adapter "Docking did not answer on D-Bus"
        terminate_pid "$ADAPTER_APP_PID"
        ADAPTER_APP_PID=""
        return 1
    fi
    return 0
}

# SIGTERM, then 10s grace, then SIGUSR1 for a faulthandler stack dump before the
# kill, then hard fail. The dump is what makes a wedged teardown diagnosable.
# Provenance: runtime-smoke.sh:83-97.
stop_docking() {
    [ -n "$ADAPTER_APP_PID" ] || return 0
    kill -TERM "$ADAPTER_APP_PID" 2>/dev/null || true
    for _ in $(seq 40); do
        kill -0 "$ADAPTER_APP_PID" 2>/dev/null || break
        sleep 0.25
    done
    if kill -0 "$ADAPTER_APP_PID" 2>/dev/null; then
        kill -USR1 "$ADAPTER_APP_PID" 2>/dev/null || true
        sleep 1
        log_adapter "Docking did not shut down within 10s"
        kill -KILL "$ADAPTER_APP_PID" 2>/dev/null || true
        wait "$ADAPTER_APP_PID" 2>/dev/null || true
        ADAPTER_APP_PID=""
        return 1
    fi
    wait "$ADAPTER_APP_PID" 2>/dev/null || true
    ADAPTER_APP_PID=""
    return 0
}

# Failure signatures the packaged smoke test already treats as fatal
# (runtime-smoke.sh:98).
assert_no_startup_failure() {
    local log_path="$1"
    if grep -E 'Traceback|Failed to start runtime stage|Forcing shutdown after the cleanup timeout' "$log_path"; then
        log_adapter "startup failure signature found in $log_path"
        return 1
    fi
    return 0
}

# Prove which code was exercised. A SHA alone does not describe the running tree,
# so record the resolved import origin.
# Provenance: packaging/deb/smoke.py:15-25 and
# docs/HEADLESS_WAYLAND_TESTING.local.md:535-539.
record_import_origin() {
    local out_path="$1"
    PYTHONPATH="$(docking_source_pythonpath)" \
        /usr/bin/python3 - <<'ORIGIN' >"$out_path" 2>&1 || true
import docking
print(docking.__file__)
print(docking.__version__)
ORIGIN
}
