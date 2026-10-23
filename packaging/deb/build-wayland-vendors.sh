#!/bin/bash
# Build native PyWayland fallbacks for each supported distribution Python version.
set -euo pipefail
mkdir -p "$1"
output="$(cd "$1" && pwd)"
for entry in 'ubuntu:22.04 3.10' 'debian:12 3.11' 'ubuntu:24.04 3.12' 'debian:13 3.13' 'ubuntu:26.04 3.14'; do
    read -r image minor <<< "$entry"
    if [ -f "$output/vendor-python$minor/.complete" ]; then continue; fi
    docker run --rm -e DEBIAN_FRONTEND=noninteractive -e PIP_BREAK_SYSTEM_PACKAGES=1 \
        -e PIP_UPLOADED_PRIOR_TO="$(date -d '7 days ago' -I)" \
        -e PYTHON_MINOR="$minor" -v "$output:/vendors" "$image" bash -euc '
            apt-get update
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
