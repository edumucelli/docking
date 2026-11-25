#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CHANGELOG_PATH="${PROJECT_ROOT}/packaging/deb/debian/changelog"
ARTIFACTS_DIR="${PROJECT_ROOT}/artifacts/ppa"

usage() {
    cat <<'EOF'
Build an Ubuntu source package for Launchpad PPA upload.

Usage:
  ./packaging/ppa/build.sh [--unsigned] <ubuntu-series> [ppa-revision]

Examples:
  ./packaging/ppa/build.sh resolute
  ./packaging/ppa/build.sh --unsigned noble

The build uses the matching release tag and the current Debian metadata.
Set DEBEMAIL and DEB_SIGN_KEYID for the Launchpad uploader. Use --unsigned for local checks.
EOF
}

fetch_source() {
    curl --fail --silent --show-error --location --retry 2 --output "$3" "$1"
    printf '%s  %s\n' "$2" "$3" | sha256sum --check
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    usage
    exit 0
fi

SIGN_OPTIONS=()
if [ "${1:-}" = "--unsigned" ]; then
    SIGN_OPTIONS=(-us -uc)
    ARTIFACTS_DIR="${ARTIFACTS_DIR}/unsigned"
    shift
fi

SERIES="${1:-}"
PPA_REVISION="${2:-1}"
if ! [[ "${SERIES}" =~ ^[a-z][a-z0-9]*$ ]] || [ "$#" -gt 2 ]; then
    usage >&2
    exit 1
fi

if ! [[ "${PPA_REVISION}" =~ ^[0-9]+$ ]]; then
    echo "PPA revision must be numeric: ${PPA_REVISION}" >&2
    exit 1
fi

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

RELEASE_REF="refs/tags/v${PROJECT_VERSION}"
if [ -z "${PROJECT_VERSION}" ] || ! git -C "${PROJECT_ROOT}" rev-parse --verify --quiet "${RELEASE_REF}^{commit}" > /dev/null; then
    echo "Missing release tag ${RELEASE_REF##*/}. Fetch the release tags first." >&2
    exit 1
fi

CURRENT_DEB_VERSION="$(dpkg-parsechangelog -l "${CHANGELOG_PATH}" -S Version)"
UPLOADER="$(dpkg-parsechangelog -l "${CHANGELOG_PATH}" -S Maintainer)"
if [ -n "${DEBEMAIL:-}" ]; then
    UPLOADER="${DEBFULLNAME:-${UPLOADER%% <*}} <${DEBEMAIL}>"
fi
BASE_REVISION="${CURRENT_DEB_VERSION##*-}"
if [ "${BASE_REVISION}" = "${CURRENT_DEB_VERSION}" ]; then
    BASE_REVISION=1
fi
TARGET_VERSION="${PROJECT_VERSION}-${BASE_REVISION}~ppa${PPA_REVISION}~${SERIES}1"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT
SOURCE_DIR="${WORK_DIR}/docking-${PROJECT_VERSION}"
ORIG_TARBALL="${WORK_DIR}/docking_${PROJECT_VERSION}.orig.tar.gz"
git -C "${PROJECT_ROOT}" archive --format=tar.gz \
    --prefix="docking-${PROJECT_VERSION}/" "${RELEASE_REF}" > "${ORIG_TARBALL}"
tar -xzf "${ORIG_TARBALL}" -C "${WORK_DIR}"

git -C "${PROJECT_ROOT}" ls-files -z -- packaging/deb/debian | \
    tar -C "${PROJECT_ROOT}" --null -T - -cf - | \
    tar -C "${SOURCE_DIR}" --strip-components=2 -xf -
mkdir -p "${SOURCE_DIR}/debian/source"
printf '3.0 (quilt)\n' > "${SOURCE_DIR}/debian/source/format"
if [[ "${SERIES}" == jammy || "${SERIES}" == noble ]]; then
    backport="${SOURCE_DIR}/debian/ppa-wayland"
    mkdir -p "${backport}/protocols"
    fetch_source 'https://files.pythonhosted.org/packages/20/93/bc9b258dcb46aafc587438355a9170c3be05a6abb5cd55bf1d600d848fa0/pywayland-0.4.18.tar.gz' \
        '598ade02783aad05a328f663b51b694c5ab68bd5d1e0926c0da3c5212c566533' "${WORK_DIR}/pywayland.tar.gz"
    fetch_source 'https://archive.ubuntu.com/ubuntu/pool/main/w/wayland-protocols/wayland-protocols_1.47.orig.tar.xz' \
        '5fd4349bcbc9bab9a46f8cf77d1f434296a7a052c87440a094f63fcf62a58e20' "${WORK_DIR}/protocols.tar.xz"
    tar -xzf "${WORK_DIR}/pywayland.tar.gz" -C "${backport}" --strip-components=1 \
        pywayland-0.4.18/LICENSE pywayland-0.4.18/pywayland
    tar -xJf "${WORK_DIR}/protocols.tar.xz" -C "${backport}/protocols" --strip-components=1 \
        wayland-protocols-1.47/COPYING \
        wayland-protocols-1.47/staging/ext-foreign-toplevel-list/ext-foreign-toplevel-list-v1.xml \
        wayland-protocols-1.47/staging/ext-idle-notify/ext-idle-notify-v1.xml \
        wayland-protocols-1.47/staging/ext-image-capture-source/ext-image-capture-source-v1.xml \
        wayland-protocols-1.47/staging/ext-image-copy-capture/ext-image-copy-capture-v1.xml
    sed -i 's/^    python3-pywayland <!pkg.docking.release>,$/    python3-cffi,\n    libffi-dev,/' \
        "${SOURCE_DIR}/debian/control"
    if ! grep -Fxq '    python3-cffi,' "${SOURCE_DIR}/debian/control"; then
        echo 'Could not set the PyWayland source build dependencies.' >&2
        exit 1
    fi
fi
{
    printf 'docking (%s) %s; urgency=medium\n\n' "${TARGET_VERSION}" "${SERIES}"
    printf '  * Launchpad PPA build for %s.\n\n' "${SERIES}"
    printf ' -- %s  %s\n\n' "${UPLOADER}" "$(date -R)"
    # The archive package for this release can sort above the PPA build.
    if dpkg --compare-versions "${CURRENT_DEB_VERSION}" ge "${TARGET_VERSION}"; then
        sed '1,/^ -- /d' "${CHANGELOG_PATH}"
    else
        cat "${CHANGELOG_PATH}"
    fi
} > "${SOURCE_DIR}/debian/changelog"

cd "${SOURCE_DIR}"
dpkg-buildpackage -S -sa -d -Zxz "${SIGN_OPTIONS[@]}"

PACKAGE="docking_${TARGET_VERSION}"
CHANGES="${WORK_DIR}/${PACKAGE}_source.changes"
if [ "${#SIGN_OPTIONS[@]}" -eq 0 ] && ! grep -q '^-----BEGIN PGP SIGNED MESSAGE-----$' "${CHANGES}"; then
    echo "The source package is unsigned. Configure your signing key and set DEB_SIGN_KEYID." >&2
    exit 1
fi

mkdir -p "${ARTIFACTS_DIR}"
cp "${ORIG_TARBALL}" "${WORK_DIR}/${PACKAGE}.dsc" \
    "${WORK_DIR}/${PACKAGE}.debian.tar.xz" "${WORK_DIR}/${PACKAGE}_source.buildinfo" \
    "${CHANGES}" "${ARTIFACTS_DIR}/"
printf '\nBuilt Launchpad source package at %s/%s\n' "${ARTIFACTS_DIR}" "${CHANGES##*/}"
