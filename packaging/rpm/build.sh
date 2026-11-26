#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TOPDIR="${TOPDIR:-${ROOT_DIR}/.rpmbuild}"
ARTIFACTS_DIR="${ROOT_DIR}/artifacts"
FEDORA=0
if [ "${1:-}" = --fedora ]; then
  FEDORA=1
  if [ "$(rpm --eval '%{?fedora}')" = '' ]; then
    echo 'Build --fedora inside the target Fedora release.' >&2
    exit 1
  fi
fi

bash "${ROOT_DIR}/tools/i18n.sh" --compile

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

# CI supplies native PyWayland fallbacks for every supported distribution Python
# version, which the Ubuntu build host cannot provide on its own.
EXTRA_DEFINES=()
DEPENDENCY_FLAGS=(--nodeps)
if [ "$FEDORA" = 1 ]; then
  EXTRA_DEFINES+=(--define 'docking_fedora 1')
  DEPENDENCY_FLAGS=()
  curl --fail --silent --show-error --location \
    'https://files.pythonhosted.org/packages/20/93/bc9b258dcb46aafc587438355a9170c3be05a6abb5cd55bf1d600d848fa0/pywayland-0.4.18.tar.gz' \
    --output "${TOPDIR}/SOURCES/pywayland-0.4.18.tar.gz"
  (cd "${TOPDIR}/SOURCES" && sha256sum --check <<'EOF'
598ade02783aad05a328f663b51b694c5ab68bd5d1e0926c0da3c5212c566533  pywayland-0.4.18.tar.gz
EOF
  )
elif [ -n "${DOCKING_EXTRA_PYWAYLAND:-}" ]; then
  EXTRA_DEFINES+=(--define "extra_pywayland ${DOCKING_EXTRA_PYWAYLAND}")
fi

rpmbuild -bb "${DEPENDENCY_FLAGS[@]}" "${TOPDIR}/SPECS/docking.spec" \
  --define "_topdir ${TOPDIR}" \
  --define "pkg_version ${VERSION}" \
  "${EXTRA_DEFINES[@]}"

find "${TOPDIR}/RPMS" -name '*.rpm' -type f -exec cp -f {} "${ARTIFACTS_DIR}/" \;
ls -lh "${ARTIFACTS_DIR}"/*.rpm
