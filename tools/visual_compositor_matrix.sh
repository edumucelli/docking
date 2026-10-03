#!/usr/bin/env bash
# Run Docking against real Wayland compositors and check what it looks like.
#
# The container only produces evidence (screenshots + probed geometry); all
# comparison happens here on the host, where the imaging stack from the dev
# extra already exists.
#
# Usage:
#   bash tools/visual_compositor_matrix.sh --compositor sway
#   bash tools/visual_compositor_matrix.sh --compositor sway --behavior placement
#   bash tools/visual_compositor_matrix.sh --compositor sway --update-baselines
#   bash tools/visual_compositor_matrix.sh --compositor sway --case placement-bottom
#   bash tools/visual_compositor_matrix.sh --compositor sway --behavior interaction --geometry-only --require-supported
#
# Artifacts land in tools/visual_compositor/evidence/<compositor>-<timestamp>/.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAB_DIR="$REPO_DIR/tools/visual_compositor"

COMPOSITOR="sway"
BEHAVIOR=""
MODE="source"
UPDATE_BASELINES=0
GEOMETRY_ONLY=0
REQUIRE_SUPPORTED=0
REBUILD=0
CASES=()

usage() {
    sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --compositor) COMPOSITOR="$2"; shift 2 ;;
        --behavior) BEHAVIOR="$2"; shift 2 ;;
        --case) CASES+=("$2"); shift 2 ;;
        --mode) MODE="$2"; shift 2 ;;
        --update-baselines) UPDATE_BASELINES=1; shift ;;
        --geometry-only) GEOMETRY_ONLY=1; shift ;;
        --require-supported) REQUIRE_SUPPORTED=1; shift ;;
        --rebuild) REBUILD=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ "$GEOMETRY_ONLY" = 1 ] && [ "$UPDATE_BASELINES" = 1 ]; then
    echo "--geometry-only cannot be combined with --update-baselines" >&2
    exit 2
fi

if [ ! -f "$LAB_DIR/adapters/$COMPOSITOR.sh" ]; then
    echo "[lab] no adapter for '$COMPOSITOR'." >&2
    echo "[lab] available: $(cd "$LAB_DIR/adapters" && ls ./*.sh | grep -v common | xargs -n1 basename | sed 's/\.sh$//' | tr '\n' ' ')" >&2
    exit 2
fi

command -v docker >/dev/null 2>&1 || { echo "[lab] docker is required." >&2; exit 1; }

# Which base image a compositor needs. niri and cosmic-comp are not packaged by
# Debian at any version; both are in Arch's official Extra repository, so one
# extra base covers them.
case "$COMPOSITOR" in
    niri|cosmic|wayfire) DOCKERFILE="Dockerfile.arch"; LAB_IMAGE_BASE="docking-lab-arch" ;;
    *)           DOCKERFILE="Dockerfile";      LAB_IMAGE_BASE="docking-lab" ;;
esac
DOCKERFILE_PATH="$LAB_DIR/$DOCKERFILE"

# One image per compositor target: Cinnamon's dependency tree is an order of
# magnitude larger than sway's, so a single image would make the fast lanes slow.
IMAGE="${DOCKING_LAB_IMAGE:-$LAB_IMAGE_BASE:$COMPOSITOR}"

# Prefer the project venv: it carries Pillow/numpy/scikit-image from the dev extra.
if [ -x "$REPO_DIR/.venv/bin/python" ]; then
    PYTHON="$REPO_DIR/.venv/bin/python"
else
    PYTHON="$(command -v python3)"
fi

if [ "$REBUILD" = 1 ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "[lab] building $IMAGE (file $DOCKERFILE, target $COMPOSITOR)"
    docker build -f "$DOCKERFILE_PATH" --target "$COMPOSITOR" -t "$IMAGE" "$LAB_DIR"
fi

RUN_ID="$COMPOSITOR-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$LAB_DIR/evidence"
RUN_DIR="$(mktemp -d "$LAB_DIR/evidence/$RUN_ID.XXXXXX")"

if [ "$MODE" = "installed" ]; then
    echo "[lab] --mode installed needs a Docking .deb in the image, which the lab" >&2
    echo "[lab] image does not install. Use --mode source (the default)." >&2
    exit 2
fi

# Record what this run actually executed. Baselines are only meaningful for the
# image that produced them, so the identity is captured with the evidence and
# checked before any baseline is trusted.
IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE")"
"$PYTHON" - "$RUN_DIR/run-meta.json" "$IMAGE_ID" "$IMAGE" "$COMPOSITOR" \
    "${LAB_OUTPUTS:-1}" "${LAB_WIDTH:-1280}" "${LAB_HEIGHT:-720}" \
    "${LAB_PANEL_HEIGHT:-0}" "${LAB_PANEL_POSITION:-bottom}" <<'RUNMETA'
import json, sys
path, image_id, image_ref, compositor, count, width, height = sys.argv[1:8]
panel_height, panel_position = sys.argv[8:10]
json.dump(
    {
        "image_id": image_id,
        "image_ref": image_ref,
        "compositor": compositor,
        "panel": {"height": int(panel_height), "position": panel_position},
        "outputs": [
            {"count": int(count), "width": int(width), "height": int(height), "scale": 1}
        ],
    },
    open(path, "w"),
    indent=2,
)
RUNMETA

# The case matrix is defined host-side; the container is handed a plain list.
emit_args=(--emit "$RUN_DIR/cases.json")
[ -n "$BEHAVIOR" ] && emit_args+=(--behavior "$BEHAVIOR")
for case_name in "${CASES[@]:-}"; do
    [ -n "$case_name" ] && emit_args+=(--case "$case_name")
done
(cd "$REPO_DIR" && "$PYTHON" -m tools.visual_compositor.scenarios "${emit_args[@]}")
if [ "${LAB_OUTPUTS:-1}" != 1 ] && "$PYTHON" -c 'import json,sys; sys.exit(not any(c.get("display_scene") or c.get("display_change") for c in json.load(open(sys.argv[1]))))' "$RUN_DIR/cases.json"; then
    echo "Display scene cases create their own outputs; run them with LAB_OUTPUTS=1" >&2
    exit 2
fi

echo "[lab] run: $RUN_ID"
echo "[lab] evidence: $RUN_DIR"

# The container runs as the invoking UID so evidence lands host-owned, but a base
# image has no passwd entry for an arbitrary UID. dbus calls getpwuid and dies
# with "Looking up user ID N: not found" before the compositor even starts --
# which is why the Debian lanes worked (docking-smoke happens to be UID 1000) and
# the Arch ones did not. Give the run its own passwd/group with the host user
# appended, rather than depending on that coincidence.
LAB_UID="$(id -u)"
LAB_GID="$(id -g)"
docker run --rm --entrypoint cat "$IMAGE" /etc/passwd >"$RUN_DIR/.passwd"
docker run --rm --entrypoint cat "$IMAGE" /etc/group >"$RUN_DIR/.group"
printf 'lab:x:%s:%s:lab:/tmp:/bin/sh\n' "$LAB_UID" "$LAB_GID" >>"$RUN_DIR/.passwd"
printf 'lab:x:%s:\n' "$LAB_GID" >>"$RUN_DIR/.group"

set +e
device_args=()
if [ -n "${LAB_RENDER_DEVICE:-}" ]; then
    if [[ ! "$LAB_RENDER_DEVICE" =~ ^/dev/dri/renderD[0-9]+$ ]] || [ ! -c "$LAB_RENDER_DEVICE" ]; then
        echo "LAB_RENDER_DEVICE must name an existing DRM render node" >&2
        exit 2
    fi
    device_args+=(--device "$LAB_RENDER_DEVICE" --group-add "$(stat -c '%g' "$LAB_RENDER_DEVICE")")
fi
docker run --rm \
    "${device_args[@]}" \
    --user "$(id -u):$(id -g)" \
    -e HOME=/tmp \
    -e LAB_DIR=/lab-run \
    -e DOCKING_SOURCE=/src \
    -e "LAB_OUTPUTS=${LAB_OUTPUTS:-1}" \
    -e "LAB_WIDTH=${LAB_WIDTH:-1280}" \
    -e "LAB_HEIGHT=${LAB_HEIGHT:-720}" \
    -e "LAB_PANEL_HEIGHT=${LAB_PANEL_HEIGHT:-0}" \
    -e "LAB_PANEL_POSITION=${LAB_PANEL_POSITION:-bottom}" \
    -e "WLR_RENDER_DRM_DEVICE=${LAB_RENDER_DEVICE:-}" \
    -v "$REPO_DIR:/src:ro" \
    -v "$LAB_DIR:/lab:ro" \
    -v "$RUN_DIR:/lab-run" \
    -v "$RUN_DIR/.passwd:/etc/passwd:ro" \
    -v "$RUN_DIR/.group:/etc/group:ro" \
    "$IMAGE" \
    dbus-run-session -- bash /lab/run_session.sh "$COMPOSITOR" "$MODE"
session_status=$?
set -e

if [ "$session_status" -ne 0 ]; then
    echo "[lab] session failed (exit $session_status); see $RUN_DIR" >&2
    for log in "$RUN_DIR"/*.log; do
        [ -f "$log" ] || continue
        echo "--- $(basename "$log") (tail) ---" >&2
        tail -n 30 "$log" >&2
    done
    exit "$session_status"
fi

compare_args=(--compositor "$COMPOSITOR" --evidence "$RUN_DIR" --out "$RUN_DIR/diffs")
[ -n "$BEHAVIOR" ] && compare_args+=(--behavior "$BEHAVIOR")
for case_name in "${CASES[@]:-}"; do
    [ -n "$case_name" ] && compare_args+=(--case "$case_name")
done
[ "$UPDATE_BASELINES" = 1 ] && compare_args+=(--update-baselines)
[ "$GEOMETRY_ONLY" = 1 ] && compare_args+=(--geometry-only)
[ "$REQUIRE_SUPPORTED" = 1 ] && compare_args+=(--require-supported)

(cd "$REPO_DIR" && "$PYTHON" -m tools.visual_compositor.compare "${compare_args[@]}")
