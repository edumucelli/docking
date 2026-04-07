#!/bin/bash
# Build a .deb package for docking.
#
# Usage: ./packaging/deb/build.sh
#
# Prerequisites:
#   sudo apt install debhelper dh-python python3-setuptools python3-wheel
#
# Output: ../docking_<version>-<debian_revision>_<arch>.deb

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CHANGELOG_PATH="${PROJECT_ROOT}/packaging/deb/debian/changelog"
CHANGELOG_BACKUP=""

cleanup() {
    rm -f "${PROJECT_ROOT}/debian"
    if [ -n "${CHANGELOG_BACKUP}" ] && [ -f "${CHANGELOG_BACKUP}" ]; then
        cp "${CHANGELOG_BACKUP}" "${CHANGELOG_PATH}"
        rm -f "${CHANGELOG_BACKUP}"
    fi
}

trap cleanup EXIT

PROJECT_VERSION="$(
  awk -F ' *= *' '
    $0 == "[project]" { in_project = 1; next }
    /^\[/ { in_project = 0 }
    in_project && $1 == "version" {
      gsub(/"/, "", $2)
      print $2
      exit
    }
  ' "${PROJECT_ROOT}/pyproject.toml"
)"

if [ -z "${PROJECT_VERSION}" ]; then
    echo "Failed to read [project].version from pyproject.toml"
    exit 1
fi

if [ ! -f "${CHANGELOG_PATH}" ]; then
    echo "Missing Debian changelog at ${CHANGELOG_PATH}"
    exit 1
fi

CURRENT_DEB_VERSION="$(sed -n '1s/^docking (\([^)]*\)).*/\1/p' "${CHANGELOG_PATH}")"
if [ -z "${CURRENT_DEB_VERSION}" ]; then
    echo "Failed to parse Debian version from ${CHANGELOG_PATH}"
    exit 1
fi

DEB_REVISION="${CURRENT_DEB_VERSION##*-}"
if [ -z "${DEB_REVISION}" ] || [ "${DEB_REVISION}" = "${CURRENT_DEB_VERSION}" ]; then
    DEB_REVISION="1"
fi

TARGET_DEB_VERSION="${PROJECT_VERSION}-${DEB_REVISION}"

cd "$PROJECT_ROOT"

# Ensure compiled gettext catalogs are present in the build context.
bash "${PROJECT_ROOT}/tools/i18n.sh" --compile

if [ "${CURRENT_DEB_VERSION}" != "${TARGET_DEB_VERSION}" ]; then
    CHANGELOG_BACKUP="$(mktemp)"
    cp "${CHANGELOG_PATH}" "${CHANGELOG_BACKUP}"

    DISTRO="$(sed -n '1s/^docking ([^)]*) \([^;]*\);.*/\1/p' "${CHANGELOG_PATH}")"
    if [ -z "${DISTRO}" ]; then
        DISTRO="unstable"
    fi

    URGENCY="$(sed -n '1s/.*; urgency=\(.*\)$/\1/p' "${CHANGELOG_PATH}")"
    if [ -z "${URGENCY}" ]; then
        URGENCY="medium"
    fi

    SIGNER="$(sed -n 's/^ -- \(.*\)  .*$/\1/p' "${CHANGELOG_PATH}" | head -n1)"
    if [ -z "${SIGNER}" ]; then
        SIGNER="Docking Maintainers <noreply@example.com>"
    fi

    tmp_changelog="$(mktemp)"
    {
        printf 'docking (%s) %s; urgency=%s\n\n' \
            "${TARGET_DEB_VERSION}" "${DISTRO}" "${URGENCY}"
        printf '  * Sync Debian package version with pyproject.toml (%s)\n\n' \
            "${PROJECT_VERSION}"
        printf ' -- %s  %s\n\n' "${SIGNER}" "$(date -R)"
        cat "${CHANGELOG_PATH}"
    } > "${tmp_changelog}"
    mv "${tmp_changelog}" "${CHANGELOG_PATH}"
fi

# Symlink debian/ to project root (dpkg-buildpackage expects it there)
if [ ! -e debian ]; then
    ln -s packaging/deb/debian debian
fi

# Build (unsigned source + binary)
dpkg-buildpackage -us -uc -b

echo ""
echo "Build complete. Package:"
ls -lh ../docking_*.deb 2>/dev/null || echo "  (check parent directory)"
