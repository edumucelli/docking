#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PKG_DIR="${ROOT_DIR}/packaging/arch"
ARTIFACTS_DIR="${ROOT_DIR}/artifacts"

VERSION=$(
  awk -F ' *= *' '
    $0 == "[project]" { in_project = 1; next }
    /^\[/ { in_project = 0 }
    in_project && $1 == "version" {
      gsub(/"/, "", $2)
      print $2
      exit
    }
  ' "${ROOT_DIR}/pyproject.toml"
)

if [ -z "${VERSION}" ]; then
  echo "Failed to read [project].version from pyproject.toml"
  exit 1
fi

if ! command -v makepkg >/dev/null 2>&1; then
  echo "makepkg is required (run this on Arch Linux / Arch container)."
  exit 1
fi

mkdir -p "${ARTIFACTS_DIR}"
cd "${PKG_DIR}"

rm -f "${ROOT_DIR}/packaging/arch/docking-${VERSION}.tar.gz"
git -C "${ROOT_DIR}" archive \
  --format=tar.gz \
  --prefix="docking-${VERSION}/" \
  -o "${ROOT_DIR}/packaging/arch/docking-${VERSION}.tar.gz" \
  HEAD

makepkg --force --cleanbuild --nodeps --noconfirm

cp -f ./*.pkg.tar.* "${ARTIFACTS_DIR}/"
ls -lh "${ARTIFACTS_DIR}"/*.pkg.tar.*
