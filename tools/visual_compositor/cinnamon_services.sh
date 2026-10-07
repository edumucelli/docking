#!/usr/bin/env bash
# Exercise all native Cinnamon services through the actual Docking UI.
set -euo pipefail
if [ "${1:-}" != --session ]; then
    repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    mkdir -p "$repo_dir/tools/visual_compositor/evidence"
    evidence_dir="$(mktemp -d "$repo_dir/tools/visual_compositor/evidence/cinnamon-services.XXXXXX")"
    docker run --rm --entrypoint cat "${DOCKING_LAB_IMAGE:-docking-lab:cinnamon}" /etc/passwd >"$evidence_dir/.passwd"
    docker run --rm --entrypoint cat "${DOCKING_LAB_IMAGE:-docking-lab:cinnamon}" /etc/group >"$evidence_dir/.group"
    printf 'lab:x:%s:%s:lab:/tmp:/bin/sh\n' "$(id -u)" "$(id -g)" >>"$evidence_dir/.passwd"
    printf 'lab:x:%s:\n' "$(id -g)" >>"$evidence_dir/.group"
    docker run --rm --network none -e HOME=/tmp --user "$(id -u):$(id -g)" \
        -v "$repo_dir:/src:ro" -v "$evidence_dir:/evidence" \
        -v "$evidence_dir/.passwd:/etc/passwd:ro" -v "$evidence_dir/.group:/etc/group:ro" \
        "${DOCKING_LAB_IMAGE:-docking-lab:cinnamon}" dbus-run-session -- \
        bash /src/tools/visual_compositor/cinnamon_services.sh --session
    exit
fi
export LAB_DIR=/evidence LAB_SCRIPTS=/src/tools/visual_compositor DOCKING_SOURCE=/src
export XDG_RUNTIME_DIR="$(mktemp -d /tmp/cinnamon-services-runtime.XXXXXX)"
export XDG_CONFIG_HOME=/tmp/cinnamon-services-config XDG_CACHE_HOME=/tmp/cinnamon-services-cache
export XDG_DATA_HOME=/tmp/cinnamon-services-data XDG_STATE_HOME=/tmp/cinnamon-services-state
export GSETTINGS_BACKEND=memory
mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME"
unset WAYLAND_DISPLAY DISPLAY DOCKING_BACKEND
source "$LAB_SCRIPTS/adapters/common.sh"
source "$LAB_SCRIPTS/adapters/cinnamon.sh"
trap adapter_cleanup EXIT
adapter_prepare
adapter_start
adapter_wait_ready
export PYTHONPATH="$(docking_source_pythonpath)"
dpkg-query -W cinnamon muffin > /evidence/versions.txt
timeout 150 /usr/bin/python3 "$LAB_SCRIPTS/probes/cinnamon_services_probe.py"
