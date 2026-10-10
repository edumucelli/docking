#!/usr/bin/env bash
# Run one disposable container with a deadline and remove it before returning.
# Usage: bash tools/docker-run-timeout.sh SECONDS -- [docker run options] IMAGE CMD
set -euo pipefail

duration="${1:?duration required}"
shift
if [ "${1:-}" != -- ]; then
    echo "Expected -- before docker run arguments" >&2
    exit 2
fi
shift

# Know the name before starting Docker: a timed-out client may not have written
# a cidfile yet. Each retry gets its own container and cleanup target.
scratch="$(mktemp -d "${TMPDIR:-/tmp}/docking-vendor.XXXXXX")"
container_name="${scratch##*/}"

cleanup() {
    local status=$? remaining
    trap - EXIT
    if ! timeout --kill-after=5 30 docker rm --force "$container_name" >/dev/null 2>&1; then
        # Creation can fail before the container exists. Confirm absence rather
        # than confusing that case with a failed removal or an unavailable daemon.
        if ! remaining="$(timeout --kill-after=5 10 docker ps -aq --filter "name=^${container_name}$")" || [ -n "$remaining" ]; then
            echo "Cannot confirm removal of $container_name; refusing another attempt" >&2
            # retry.sh treats 126 as terminal. Do not start a second writer on
            # the shared vendor directory while the first might still be alive.
            status=126
        fi
    fi
    rmdir "$scratch"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# SIGTERM alone can leave Docker waiting for a container's foreground process.
# Force the client to exit, then remove the container in the EXIT trap. Avoid
# --rm here so successful runs also have a predictable cleanup operation.
timeout --kill-after=5 "$duration" docker run --name "$container_name" "$@"
