#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CHANGELOG_PATH="${PROJECT_ROOT}/packaging/deb/debian/changelog"
ARTIFACTS_DIR="${PROJECT_ROOT}/artifacts/ppa"
CHANGELOG_BACKUP=""

usage() {
    cat <<'EOF'
Build a signed Ubuntu source package for Launchpad PPA upload.

Usage:
  ./packaging/ppa/build.sh <ubuntu-series> [ppa-revision]

Examples:
  ./packaging/ppa/build.sh noble
  ./packaging/ppa/build.sh jammy 2

Notes:
  - This reuses packaging/deb/debian/ as the Debian metadata source.
  - The upload version is generated as:
      <project-version>-<debian-revision>~ppa<ppa-revision>~<series>1
  - The source package is signed; make sure your GPG key is configured.
EOF
}

cleanup() {
    rm -f "${PROJECT_ROOT}/debian"
    if [ -n "${CHANGELOG_BACKUP}" ] && [ -f "${CHANGELOG_BACKUP}" ]; then
        cp "${CHANGELOG_BACKUP}" "${CHANGELOG_PATH}"
        rm -f "${CHANGELOG_BACKUP}"
    fi
}

trap cleanup EXIT

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    usage
    exit 0
fi

SERIES="${1:-}"
PPA_REVISION="${2:-1}"

if [ -z "${SERIES}" ]; then
    usage
    exit 1
fi

if ! [[ "${PPA_REVISION}" =~ ^[0-9]+$ ]]; then
    echo "PPA revision must be numeric: ${PPA_REVISION}"
    exit 1
fi

if [ ! -f "${CHANGELOG_PATH}" ]; then
    echo "Missing Debian changelog at ${CHANGELOG_PATH}"
    exit 1
fi

if ! command -v debuild >/dev/null 2>&1; then
    echo "debuild is required. Install devscripts."
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

if [ -z "${PROJECT_VERSION}" ]; then
    echo "Failed to read [project].version from pyproject.toml"
    exit 1
fi

CURRENT_DEB_VERSION="$(sed -n '1s/^docking (\([^)]*\)).*/\1/p' "${CHANGELOG_PATH}")"
if [ -z "${CURRENT_DEB_VERSION}" ]; then
    echo "Failed to parse Debian version from ${CHANGELOG_PATH}"
    exit 1
fi

BASE_REVISION="${CURRENT_DEB_VERSION##*-}"
if [ -z "${BASE_REVISION}" ] || [ "${BASE_REVISION}" = "${CURRENT_DEB_VERSION}" ]; then
    BASE_REVISION="1"
fi

TARGET_VERSION="${PROJECT_VERSION}-${BASE_REVISION}~ppa${PPA_REVISION}~${SERIES}1"

SIGNER="$(sed -n 's/^ -- \(.*\)  .*$/\1/p' "${CHANGELOG_PATH}" | head -n1)"
if [ -z "${SIGNER}" ]; then
    SIGNER="Docking Maintainers <noreply@example.com>"
fi

cd "${PROJECT_ROOT}"

bash "${PROJECT_ROOT}/tools/i18n.sh" --compile

CHANGELOG_BACKUP="$(mktemp)"
cp "${CHANGELOG_PATH}" "${CHANGELOG_BACKUP}"

tmp_changelog="$(mktemp)"
{
    printf 'docking (%s) %s; urgency=medium\n\n' \
        "${TARGET_VERSION}" "${SERIES}"
    printf '  * Launchpad PPA build for %s.\n\n' "${SERIES}"
    printf ' -- %s  %s\n\n' "${SIGNER}" "$(date -R)"
    cat "${CHANGELOG_PATH}"
} > "${tmp_changelog}"
mv "${tmp_changelog}" "${CHANGELOG_PATH}"

if [ ! -e "${PROJECT_ROOT}/debian" ]; then
    ln -s packaging/deb/debian "${PROJECT_ROOT}/debian"
fi

mkdir -p "${ARTIFACTS_DIR}"
rm -f \
    "${ARTIFACTS_DIR}/docking_${TARGET_VERSION}"* \
    "${PROJECT_ROOT}/../docking_${TARGET_VERSION}"*

debuild -S -sa

find "${PROJECT_ROOT}/.." -maxdepth 1 -type f \
    \( -name "docking_${TARGET_VERSION}.dsc" \
    -o -name "docking_${TARGET_VERSION}.debian.tar.*" \
    -o -name "docking_${TARGET_VERSION}.orig.tar.*" \
    -o -name "docking_${TARGET_VERSION}_source.buildinfo" \
    -o -name "docking_${TARGET_VERSION}_source.changes" \) \
    -exec cp -f {} "${ARTIFACTS_DIR}/" \;

echo ""
echo "Built Launchpad source package:"
ls -lh "${ARTIFACTS_DIR}/docking_${TARGET_VERSION}"*
