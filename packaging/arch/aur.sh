#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" != 2 || ! "$1" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Usage: bash packaging/arch/aur.sh <release-tag> <output-directory>" >&2
  exit 2
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tag="$1"
version="${tag#v}"
destination="$2"
commit="$(git -C "$root" rev-parse --short=7 "refs/tags/$tag^{commit}")"
recipe="$root/packaging/arch/PKGBUILD"
archive="https://codeload.github.com/edumucelli/docking/tar.gz/refs/tags/$tag"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

curl --fail --silent --show-error --location --retry 3 "$archive" -o "$scratch/source.tar.gz"
checksum="$(sha256sum "$scratch/source.tar.gz" | cut -d ' ' -f 1)"
mkdir "$scratch/docking" "$scratch/docking-git"

{
  echo '# Maintainer: Docking contributors <hello@docking.cc>'
  sed -e "s/^pkgver=.*/pkgver=$version/" \
      -e "s|^source=.*|source=(\"docking-$version.tar.gz::$archive\")|" \
      -e "s/^sha256sums=.*/sha256sums=('$checksum')/" "$recipe"
  echo "conflicts=('docking-bin' 'docking-git')"
} > "$scratch/docking/PKGBUILD"

{
  echo '# Maintainer: Docking contributors <hello@docking.cc>'
  sed -e 's/^pkgname=.*/pkgname=docking-git/' \
      -e "s/^pkgver=.*/pkgver=$version.r0.g$commit/" \
      -e 's|^source=.*|source=("docking::git+https://github.com/edumucelli/docking.git#branch=master")|' \
      -e 's/^_source_directory=.*/_source_directory=docking/' "$recipe"
  cat <<'EOF'

makedepends+=('git')
provides=("docking=$pkgver")
conflicts=('docking' 'docking-bin')

pkgver() {
  cd "$srcdir/$_source_directory"
  git describe --long --tags --match 'v[0-9]*' --abbrev=7 |
    sed -E 's/^v//; s/-([0-9]+)-g([0-9a-f]+)$/.r\1.g\2/'
}
EOF
} > "$scratch/docking-git/PKGBUILD"

for package in docking docking-git; do
  (cd "$scratch/$package" && makepkg --printsrcinfo > .SRCINFO)
  install -d "$destination/$package"
  install -m644 "$scratch/$package/PKGBUILD" "$scratch/$package/.SRCINFO" "$destination/$package/"
done
