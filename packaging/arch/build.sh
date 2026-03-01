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

# Prefer git archive for clean source exports; fall back to tar if .git metadata
# is unavailable in CI/container checkouts.
if git -C "${ROOT_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "${ROOT_DIR}" archive \
    --format=tar.gz \
    --prefix="docking-${VERSION}/" \
    -o "${ROOT_DIR}/packaging/arch/docking-${VERSION}.tar.gz" \
    HEAD
else
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "${tmpdir}"' EXIT

  mkdir -p "${tmpdir}/docking-${VERSION}"
  cp -a "${ROOT_DIR}/." "${tmpdir}/docking-${VERSION}/"
  rm -rf \
    "${tmpdir}/docking-${VERSION}/.git" \
    "${tmpdir}/docking-${VERSION}/artifacts" \
    "${tmpdir}/docking-${VERSION}/.venv" \
    "${tmpdir}/docking-${VERSION}/.flatpak-builder" \
    "${tmpdir}/docking-${VERSION}/.rpmbuild" \
    "${tmpdir}/docking-${VERSION}/AppDir" \
    "${tmpdir}/docking-${VERSION}/build"

  tar -C "${tmpdir}" \
    -czf "${ROOT_DIR}/packaging/arch/docking-${VERSION}.tar.gz" \
    "docking-${VERSION}"
fi

makepkg --force --cleanbuild --nodeps --noconfirm

cp -f ./*.pkg.tar.* "${ARTIFACTS_DIR}/"
ls -lh "${ARTIFACTS_DIR}"/*.pkg.tar.*
