#!/bin/bash
# Build native PyWayland fallbacks for each supported RPM distribution Python.
#
# The RPM is built on Ubuntu runners, so the Python minor available at build
# time never matches Fedora or openSUSE. PyWayland ships CPython-specific native
# glue and needs newer protocol modules than the distributions package, so CI
# builds one fallback tree per target Python minor and hands them to the spec
# through DOCKING_EXTRA_PYWAYLAND.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../tools/retry.sh
. "${SCRIPT_DIR}/../../tools/retry.sh"

mkdir -p "$1"
output="$(cd "$1" && pwd)"
# Trees are keyed by Python minor alone, so the first image to build a given
# minor is reused by the rest. Older bases come first so a newer glibc never
# produces a fallback that an older distribution has to load.
for image in fedora:44 fedora:45 opensuse/leap:16.0 opensuse/tumbleweed:latest; do
    echo "Building the PyWayland fallback from ${image}"
    # Both families ship the protocol XML in a -devel package, but only Fedora
    # resolves python3-pip and pkg-config by those exact names.
    # Retried per image: one flaky mirror should not throw away the trees that
    # already built.  The .complete sentinel below keeps them from being rebuilt.
    retry -a 3 -d 20 -l "pywayland ${image}" -- \
    bash "${SCRIPT_DIR}/../../tools/docker-run-timeout.sh" 420 -- \
        -e PIP_RETRIES=5 -e PIP_TIMEOUT=60 \
        -v "$output:/vendors" "$image" bash -euc '
        if command -v dnf >/dev/null 2>&1; then
            dnf install -y --setopt=install_weak_deps=False --setopt=timeout=60 \
                gcc python3-devel python3-pip libffi-devel wayland-devel \
                wayland-protocols-devel pkgconf-pkg-config
        else
            zypper --non-interactive install --no-recommends \
                gcc python3-devel python3-pip libffi-devel wayland-devel \
                wayland-protocols-devel pkg-config
        fi
        # PyWayland generates code that includes <wayland-client-core.h> from the
        # top level. Fedora and Debian install it there, openSUSE only under
        # /usr/include/wayland, so that directory has to be on the include path.
        export CFLAGS="${CFLAGS:-} -I/usr/include/wayland"
        minor="$(python3 -c "import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}\")")"
        target="/vendors/vendor-python${minor}"
        if [ -f "${target}/.complete" ]; then
            echo "Reusing ${target} for python${minor}"
            exit 0
        fi
        # Deliberately after the sentinel check: an earlier image in the loop
        # donates its completed tree to later ones, and that reuse must survive.
        # But a tree with no .complete was killed mid-build, and pip --target
        # merges into an existing directory, so the retry has to start clean.
        rm -rf "${target}"
        python3 -m pip install --break-system-packages --no-compile --no-binary=pywayland \
            --target="${target}" "pywayland>=0.4.18,<0.5"
        rm -rf "${target}/bin"
        PYTHONPATH="${target}" python3 -c \
            "from pywayland.client import Display; from pywayland.protocol.ext_idle_notify_v1 import ExtIdleNotifierV1; from pywayland.protocol.ext_image_copy_capture_v1 import ExtImageCopyCaptureManagerV1"
        touch "${target}/.complete"
        echo "Built ${target} for python${minor}"
    '
done
