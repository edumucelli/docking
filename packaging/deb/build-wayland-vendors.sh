#!/bin/bash
# Build native PyWayland fallbacks for each supported distribution Python version.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../tools/retry.sh
. "${SCRIPT_DIR}/../../tools/retry.sh"

mkdir -p "$1"
output="$(cd "$1" && pwd)"
for entry in 'ubuntu:22.04 3.10' 'debian:12 3.11' 'ubuntu:24.04 3.12' 'debian:13 3.13' 'ubuntu:26.04 3.14'; do
    read -r image minor <<< "$entry"
    if [ -f "$output/vendor-python$minor/.complete" ]; then continue; fi
    # Retried per image: a mirror blip on one distro should not throw away the
    # four trees that already built.  The .complete sentinel keeps finished
    # trees from being rebuilt on a later run.
    retry -a 3 -d 20 -l "pywayland ${image}" -- \
    bash "${SCRIPT_DIR}/../../tools/docker-run-timeout.sh" 420 -- \
        -e DEBIAN_FRONTEND=noninteractive -e PIP_BREAK_SYSTEM_PACKAGES=1 \
        -e PIP_UPLOADED_PRIOR_TO="$(date -d '7 days ago' -I)" \
        -e PIP_RETRIES=5 -e PIP_TIMEOUT=60 \
        -e PYTHON_MINOR="$minor" -v "$output:/vendors" "$image" bash -euc '
            # A killed attempt leaves a partially populated tree behind and pip
            # --target merges into an existing directory, so without this the
            # retry would build on top of half-written files -- and the import
            # probe below could still bless the result with .complete.
            rm -rf "/vendors/vendor-python${PYTHON_MINOR}"
            apt-get update -o Acquire::Retries=3 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30
            apt-get install -y --no-install-recommends \
                build-essential python3-dev python3-pip libffi-dev libwayland-dev wayland-protocols pkg-config
            python3 -m pip install --upgrade --ignore-installed pip
            python3 -m pip install --upgrade --no-compile --no-binary=pywayland \
                --target="/vendors/vendor-python${PYTHON_MINOR}" "pywayland>=0.4.18,<0.5"
            rm -rf /vendors/vendor-python${PYTHON_MINOR}/bin
            PYTHONPATH="/vendors/vendor-python${PYTHON_MINOR}" python3 -c \
                "from pywayland.client import Display; from pywayland.protocol.ext_idle_notify_v1 import ExtIdleNotifierV1; from pywayland.protocol.ext_image_copy_capture_v1 import ExtImageCopyCaptureManagerV1"
            touch /vendors/vendor-python${PYTHON_MINOR}/.complete
        '
done
