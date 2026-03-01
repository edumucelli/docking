#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TOPDIR="${TOPDIR:-${ROOT_DIR}/.rpmbuild}"
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

rm -rf "${TOPDIR}"
mkdir -p "${TOPDIR}"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
mkdir -p "${ARTIFACTS_DIR}"

git -C "${ROOT_DIR}" archive \
  --format=tar.gz \
  --prefix="docking-${VERSION}/" \
  -o "${TOPDIR}/SOURCES/docking-${VERSION}.tar.gz" \
  HEAD

cp "${ROOT_DIR}/packaging/rpm/docking.spec" "${TOPDIR}/SPECS/docking.spec"

rpmbuild -bb --nodeps "${TOPDIR}/SPECS/docking.spec" \
  --define "_topdir ${TOPDIR}" \
  --define "pkg_version ${VERSION}"

find "${TOPDIR}/RPMS" -name '*.rpm' -type f -exec cp -f {} "${ARTIFACTS_DIR}/" \;
ls -lh "${ARTIFACTS_DIR}"/*.rpm
