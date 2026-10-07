#!/bin/bash
# In-container orchestrator: start a compositor, then run each case against it.
#
# Reads $LAB_DIR/cases.json (written by the host from scenarios.py, the single
# source of truth for the case matrix) and writes per-case evidence directly
# into $LAB_DIR (which the host mounts as a scratch run directory):
#
#   <case>.png    full compositor output captured after the dock settled
#   <case>.json   output layout, dock geometry if the compositor exposes it,
#                 requested geometry from Docking's own placement log, and
#                 whether the capture was stable
#   <case>.log    Docking's log for the case
#
# Usage: run_session.sh <compositor> <mode>   (mode: source | installed)

set -euo pipefail

COMPOSITOR="${1:?compositor name required}"
MODE="${2:-source}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVIDENCE_DIR="${LAB_DIR:?LAB_DIR must be set}"
mkdir -p "$EVIDENCE_DIR"

# Two different directories, and conflating them silently breaks the probes:
# LAB_DIR is the writable run/evidence directory the host mounts, while
# LAB_SCRIPTS is where the adapters and probes are bind-mounted read-only.
export LAB_SCRIPTS="$HERE"

# Fresh, private runtime dir per run, owned by us, mode 0700. The Wayland socket
# lives here and a wrong mode or owner is a common silent failure
# (docs/HEADLESS_WAYLAND_TESTING.local.md:159). Created with mktemp so it is
# always owned by the running user regardless of how the container was started.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$(mktemp -d /tmp/docking-runtime.XXXXXX)}"
chmod 0700 "$XDG_RUNTIME_DIR"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$LAB_DIR/xdg/config}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$LAB_DIR/xdg/cache}"
export XDG_DATA_HOME="${XDG_DATA_HOME:-$LAB_DIR/xdg/data}"
export XDG_STATE_HOME="${XDG_STATE_HOME:-$LAB_DIR/xdg/state}"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME"

# Do not inherit any of the ambient session's display or compositor hints; a
# leaked WAYLAND_DISPLAY would silently point the lab at the wrong server.
unset WAYLAND_DISPLAY GDK_BACKEND DOCKING_BACKEND
unset GAMESCOPE_WAYLAND_DISPLAY HYPRLAND_INSTANCE_SIGNATURE NIRI_SOCKET SWAYSOCK

export GSETTINGS_BACKEND=memory
export GTK_THEME="${GTK_THEME:-Adwaita}"
# DEBUG is required: the placement line carrying the requested geometry is
# log.debug (docking/ui/placement.py:423-426). DOCKING_LOG_LEVEL is read at
# import time (docking/log.py:23), so it must be exported before launch.
export DOCKING_LOG_LEVEL=DEBUG

# Sourced only after the environment above exists: the adapters capture
# XDG_RUNTIME_DIR and friends at source time.
# shellcheck source=/dev/null
source "$HERE/adapters/common.sh"
# shellcheck source=/dev/null
source "$HERE/adapters/${COMPOSITOR}.sh"
trap adapter_cleanup EXIT

adapter_prepare
adapter_start
adapter_wait_ready
if declare -F adapter_start_frame_clock >/dev/null; then adapter_start_frame_clock; fi

# Before capabilities, because the panel's presence is part of what the adapter
# reports, and before any case, so the dock always starts into the same world.
start_lab_panel

source "$HERE/input.sh"
start_lab_input
adapter_capabilities | jq --argjson input "$LAB_INPUT_SUPPORTED" \
    '.pointer = $input | .interactive = ($input and .placement) | .window_actions = ($input and (.window_actions // false)) | .dodge = ($input and (.expected_backend == "wayfire" or (.expected_backend == "cosmic" and .cosmic_overlap_supported == true)))' \
    >"$EVIDENCE_DIR/capabilities.json"
record_import_origin "$EVIDENCE_DIR/import-origin.txt"
adapter_geometry >"$EVIDENCE_DIR/outputs.json"

# Docking's first-run pinned set is applets -- clock, calendar, weather,
# systemmonitor (docking/core/config.py:249-257, build_initial_pinned at :354).
# Those render live, time-varying data, so a dock left at its defaults never
# settles and no baseline can converge. Seed a small set of *static* launchers
# instead and pin exactly those in every case.
#
# Pattern follows packaging/deb/runtime-smoke.sh:48-57.
mkdir -p "$XDG_DATA_HOME/applications"
for probe in alpha beta gamma probe $(seq -f item-%02g 0 31); do
    cat >"$XDG_DATA_HOME/applications/lab-$probe.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Lab $probe
Exec=/usr/bin/true
Icon=application-x-executable
EOF
done

log_adapter "$COMPOSITOR up (wayland=$WAYLAND_DISPLAY)"

# Capture until two consecutive frames are byte-identical. Reproduces the
# intent of runtime-smoke.sh:71's `sleep 2`, but bounded and actually tied to
# the compositor having stopped changing rather than to a guessed duration.
# Returns 0 when stable; 1 when the budget was exhausted.
capture_until_stable() {
    local dest="$1"
    local attempts="${LAB_SETTLE_ATTEMPTS:-12}"
    local previous=""
    local current
    local index
    for index in $(seq "$attempts"); do
        current="$EVIDENCE_DIR/.settle-$index.png"
        adapter_screenshot "$current"
        if [ -n "$previous" ] && cmp -s "$previous" "$current"; then
            mv "$current" "$dest"
            rm -f "$previous"
            return 0
        fi
        rm -f "$previous"
        previous="$current"
        sleep 0.5
    done
    # Not stable within budget: keep the last frame and report it so the host
    # can fail the case on evidence rather than on a container-side guess.
    mv "$previous" "$dest"
    return 1
}

run_case() {
    local name="$1"
    local case_json="$2"
    local case_dir="$EVIDENCE_DIR"
    local config_dir="$XDG_CONFIG_HOME/docking"

    mkdir -p "$config_dir"
    echo "$case_json" | jq '.overrides' >"$config_dir/dock.json"
    unset LAB_STACK_STATE
    if [ "$(echo "$case_json" | jq -r '.action // empty')" = stack ]; then
        export LAB_STACK_STATE="$case_dir/$name.stack.json"
        PYTHONPATH="$(docking_source_pythonpath)" /usr/bin/python3 \
            "$HERE/probes/stack_probe.py" seed "$config_dir/dock.json" "$LAB_DIR/stack-folder"
    fi

    if [ "$(echo "$case_json" | jq -r '.action // empty')" = window-switching ]; then
        for app in alpha beta; do
            cat >"$XDG_DATA_HOME/applications/lab-$app.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Lab $app
Exec=/usr/bin/python3 $HERE/probes/cinnamon_window_probe.py --client $app
StartupWMClass=lab-$app
Icon=application-x-executable
EOF
        done
    fi

    log_adapter "case: $name"
    if declare -F adapter_scene >/dev/null; then
        adapter_scene "$(echo "$case_json" | jq -r ' .display_scene // empty')"
    fi
    adapter_geometry >"$case_dir/$name.before.json"
    local reservation=false
    if [ "$(echo "$case_json" | jq -r '.maximize_window // false')" = true ] \
        && [ "$(jq -r '.reservation_probe // false' "$EVIDENCE_DIR/capabilities.json")" = true ]; then
        reservation=true
    fi
    if ! start_docking "$case_dir/$name.log" "$MODE"; then
        jq -n '{status:"fail", reason:"docking did not start"}' >"$case_dir/$name.json"
        return 0
    fi

    local action change action_ok=true
    action="$(echo "$case_json" | jq -r '.action // empty')"
    change="$(echo "$case_json" | jq -r '.display_change // empty')"
    local action_supported=true requirement
    requirement="$(echo "$case_json" | jq -r '.requires // empty')"
    if [ -n "$requirement" ] && [ "$(jq -r --arg cap "$requirement" '.[$cap] // false' "$EVIDENCE_DIR/capabilities.json")" != true ]; then
        action_supported=false
    fi
    if [ -n "$action" ] && [ "$LAB_INPUT_SUPPORTED" = true ] && [ "$action_supported" = true ]; then
        source "$HERE/actions.sh"
        run_lab_action "$action" "$(echo "$case_json" | jq -r '.edge')" || action_ok=false
    fi
    if [ -n "$change" ] && declare -F adapter_change_output >/dev/null; then
        capture_until_stable "$case_dir/$name.before-change.png" || action_ok=false
        adapter_geometry >"$case_dir/$name.before-change.json"
        adapter_change_output "$change" || action_ok=false
    fi
    local settled=true
    capture_until_stable "$case_dir/$name.png" || settled=false

    adapter_geometry >"$case_dir/$name.raw-geometry.json"

    # Requested geometry, parsed from Docking's own placement debug line. This is
    # NOT the oracle (it is what the dock asked for, not what it got) -- it is
    # recorded so the host can report requested-vs-observed disagreement, which
    # is exactly the signal the Cinnamon bug produced.
    local requested
    requested="$(grep -oE 'win=\(-?[0-9]+,-?[0-9]+\) size=[0-9]+x[0-9]+' "$case_dir/$name.log" \
        | tail -1 || true)"

    # What Docking says about where it is. GetHoverAnchor resolves through
    # window_screen_position(), i.e. the same backend-owned position that
    # anchors popups, menus, tooltips and the dodge rectangle. Comparing it
    # against the pixels is the assertion that catches a dock reporting a
    # position it never reached -- the shape of #347.
    local self_reported first_item last_item last_anchor
    first_item="$(jq -r '.pinned[0] | if type == "object" then .target else . end' "$config_dir/dock.json")"
    last_item="$(jq -r '.pinned[-1] | if type == "object" then .target else . end' "$config_dir/dock.json")"
    self_reported="$(gdbus call --session --dest org.docking.Docking \
        --object-path /org/docking/Docking \
        --method org.docking.Docking.Items1.GetHoverAnchor "$first_item" \
        2>/dev/null | head -1 || true)"

    last_anchor="$(gdbus call --session --dest org.docking.Docking \
        --object-path /org/docking/Docking \
        --method org.docking.Docking.Items1.GetHoverAnchor "$last_item" 2>/dev/null || true)"
    jq -n \
        --arg last_anchor "$last_anchor" \
        --arg name "$name" \
        --argjson settled "$settled" \
        --arg requested "$requested" \
        --arg self_reported "$self_reported" \
        --slurpfile before "$case_dir/$name.before.json" \
        --slurpfile geom "$case_dir/$name.raw-geometry.json" \
        '{
            name: $name,
            started: true,
            settled: $settled,
            requested_geometry: $requested,
            self_reported_anchor: $self_reported,
            last_item_anchor: $last_anchor,
            external_outputs: ($before[0].outputs // []),
            outputs: ($geom[0].outputs // []),
            capture_origin: {
                x: ([$geom[0].outputs[].x] | min),
                y: ([$geom[0].outputs[].y] | min)
            },
            dock_rect: ($geom[0].dock_rect // null)
        }' >"$case_dir/$name.json"
    rm -f "$case_dir/$name.raw-geometry.json"
    if [ "$action_ok" != true ]; then
        jq '.status="fail" | .reason="input or display action did not complete"' \
            "$case_dir/$name.json" >"$case_dir/$name.tmp"
        mv "$case_dir/$name.tmp" "$case_dir/$name.json"
    fi

    if [ "$reservation" = true ]; then
        adapter_start_maximized_window
        adapter_reservation >"$case_dir/$name.maximized.json"
        adapter_screenshot "$case_dir/$name.maximized.png"
        jq --slurpfile before "$case_dir/$name.before.json" \
            --slurpfile maximized "$case_dir/$name.maximized.json" \
            '.reservation = {before: $before[0], maximized: $maximized[0]}' \
            "$case_dir/$name.json" >"$case_dir/$name.tmp"
        mv "$case_dir/$name.tmp" "$case_dir/$name.json"
    fi

    if ! kill -0 "$ADAPTER_APP_PID" 2>/dev/null; then
        jq '.status = "fail" | .reason = "docking exited during the case"' \
            "$case_dir/$name.json" >"$case_dir/$name.tmp"
        mv "$case_dir/$name.tmp" "$case_dir/$name.json"
    fi
    if ! assert_no_startup_failure "$case_dir/$name.log"; then
        jq '.status = "fail" | .reason = "startup failure signature in log"' \
            "$case_dir/$name.json" >"$case_dir/$name.tmp" \
            && mv "$case_dir/$name.tmp" "$case_dir/$name.json"
    fi

    # A teardown failure is a result, not noise: a dock that will not exit can
    # still be running against the compositor while the next case starts, so it
    # must fail this case rather than be discarded.
    local stopped=true
    if [ "$reservation" = true ] && [ "$(echo "$case_json" | jq -r '.crash_dock // false')" = true ]; then
        kill -KILL "$ADAPTER_APP_PID"
        wait "$ADAPTER_APP_PID" 2>/dev/null || true
        ADAPTER_APP_PID=""
    elif ! stop_docking; then
        stopped=false
        jq '.status = "fail"
            | .reason = "docking failed to shut down within 10s"' \
            "$case_dir/$name.json" >"$case_dir/$name.tmp" \
            && mv "$case_dir/$name.tmp" "$case_dir/$name.json"
    fi
    jq --argjson stopped "$stopped" '.stopped = $stopped' \
        "$case_dir/$name.json" >"$case_dir/$name.tmp"
    mv "$case_dir/$name.tmp" "$case_dir/$name.json"
    if [ "$reservation" = true ]; then
        adapter_reservation >"$case_dir/$name.released.json"
        jq --slurpfile released "$case_dir/$name.released.json" \
            '.reservation.released = $released[0]' \
            "$case_dir/$name.json" >"$case_dir/$name.tmp"
        mv "$case_dir/$name.tmp" "$case_dir/$name.json"
        adapter_screenshot "$case_dir/$name.released.png"
        terminate_pid "$ADAPTER_PROBE_PID"
        ADAPTER_PROBE_PID=""
    fi
    sleep 0.5
}

# Iterate the host-provided case matrix.
case_count="$(jq 'length' "$LAB_DIR/cases.json")"
index=0
while [ "$index" -lt "$case_count" ]; do
    case_json="$(jq -c ".[$index]" "$LAB_DIR/cases.json")"
    case_name="$(echo "$case_json" | jq -r '.name')"
    run_case "$case_name" "$case_json"
    index=$((index + 1))
done

adapter_stop
log_adapter "session complete"
