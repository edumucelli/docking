#!/bin/sh
# Print a deb822 source for Debian, Ubuntu, or an Ubuntu derivative.
set -eu
. "${DOCKING_OS_RELEASE:-/etc/os-release}"
APT_DISTRO="${ID:-}"
APT_SUITE="${VERSION_CODENAME:-}"
if [ -n "${UBUNTU_CODENAME:-}" ]; then
    APT_DISTRO=ubuntu
    APT_SUITE="$UBUNTU_CODENAME"
fi
if [ "$APT_DISTRO" = debian ] && [ -z "$APT_SUITE" ]; then
    DEBIAN_VERSION=$(cat "${DOCKING_DEBIAN_VERSION:-/etc/debian_version}" 2>/dev/null || true)
    case "$DEBIAN_VERSION" in
        12|12.*|bookworm|bookworm/sid) APT_SUITE=bookworm ;;
        13|13.*|trixie|trixie/sid) APT_SUITE=trixie ;;
    esac
fi
case "$APT_DISTRO:$APT_SUITE" in
    ubuntu:jammy|ubuntu:noble|ubuntu:resolute|debian:bookworm|debian:trixie) ;;
    *) echo "Unsupported APT base: $APT_DISTRO/$APT_SUITE" >&2; exit 1 ;;
esac
cat <<EOF
Types: deb
URIs: https://dl.cloudsmith.io/public/docking/docking-apt/deb/${APT_DISTRO}
Suites: ${APT_SUITE}
Components: main
Architectures: amd64 arm64
Signed-By: /etc/apt/keyrings/docking-cloudsmith.asc
EOF
