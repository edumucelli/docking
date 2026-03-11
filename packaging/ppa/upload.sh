#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ARTIFACTS_DIR="${PROJECT_ROOT}/artifacts/ppa"

usage() {
    cat <<'EOF'
Upload the latest built PPA source package to Launchpad.

Usage:
  ./packaging/ppa/upload.sh <launchpad-id>/<ppa-name> [changes-file]

Examples:
  ./packaging/ppa/upload.sh edumucelli/docking
  ./packaging/ppa/upload.sh edumucelli/docking artifacts/ppa/docking_0.1.40-1~ppa1~noble1_source.changes
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    usage
    exit 0
fi

TARGET="${1:-}"
CHANGES_FILE="${2:-}"

if [ -z "${TARGET}" ]; then
    usage
    exit 1
fi

if ! command -v dput >/dev/null 2>&1; then
    echo "dput is required. Install dput-ng or dput."
    exit 1
fi

if [ -z "${CHANGES_FILE}" ]; then
    CHANGES_FILE="$(
        find "${ARTIFACTS_DIR}" -maxdepth 1 -type f -name 'docking_*_source.changes' \
            -printf '%T@ %p\n' | sort -n | tail -n1 | cut -d' ' -f2-
    )"
fi

if [ -z "${CHANGES_FILE}" ] || [ ! -f "${CHANGES_FILE}" ]; then
    echo "Could not find a source .changes file to upload."
    exit 1
fi

dput "ppa:${TARGET}" "${CHANGES_FILE}"
