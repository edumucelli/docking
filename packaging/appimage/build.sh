#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RECIPE_TEMPLATE="${ROOT_DIR}/packaging/appimage/AppImageBuilder.yml"

bash "${ROOT_DIR}/tools/i18n.sh" --compile

mkdir -p artifacts

case "$(uname -m)" in
  x86_64)
    apt_arch="amd64"
    apt_mirror="http://archive.ubuntu.com/ubuntu/"
    appimage_arch="x86_64"
    typelib_arch_dir="x86_64-linux-gnu"
    ;;
  aarch64|arm64)
    apt_arch="arm64"
    apt_mirror="http://ports.ubuntu.com/ubuntu-ports/"
    appimage_arch="aarch64"
    typelib_arch_dir="aarch64-linux-gnu"
    ;;
  *)
    echo "Unsupported AppImage build architecture: $(uname -m)" >&2
    exit 1
    ;;
esac

tmp_recipe="$(mktemp)"
trap 'rm -f "${tmp_recipe}"' EXIT

sed \
  -e "s|__APT_ARCH__|${apt_arch}|g" \
  -e "s|__APT_MIRROR__|${apt_mirror}|g" \
  -e "s|__APPIMAGE_ARCH__|${appimage_arch}|g" \
  -e "s|__GI_TYPELIB_PATH__|\\\$APPDIR/usr/lib/${typelib_arch_dir}/girepository-1.0:\\\$APPDIR/usr/lib/girepository-1.0|g" \
  "${RECIPE_TEMPLATE}" > "${tmp_recipe}"

run_appimage_builder() {
  python3 - "${tmp_recipe}" <<'PY'
import runpy
import sys

import apt_pkg
from appimagebuilder.modules.deploy.apt import package as apt_package

apt_pkg.init_system()


def _compare_versions(self, other):
    return apt_pkg.version_compare(self.version, other.version)


apt_package.Package.__gt__ = lambda self, other: _compare_versions(self, other) > 0
apt_package.Package.__lt__ = lambda self, other: _compare_versions(self, other) < 0
apt_package.Package.__ge__ = lambda self, other: _compare_versions(self, other) >= 0
apt_package.Package.__le__ = lambda self, other: _compare_versions(self, other) <= 0

sys.argv = ["appimage-builder", "--recipe", sys.argv[1], "--skip-test"]
runpy.run_module("appimagebuilder", run_name="__main__")
PY
}

attempt=1
max_attempts=3
until run_appimage_builder; do
  if (( attempt >= max_attempts )); then
    echo "AppImage build failed after ${attempt} attempts." >&2
    exit 1
  fi
  sleep_seconds=$(( attempt * 15 ))
  echo "AppImage build attempt ${attempt} failed; retrying in ${sleep_seconds}s..." >&2
  sleep "${sleep_seconds}"
  attempt=$(( attempt + 1 ))
done

mv -f ./*.AppImage artifacts/
ls -lh artifacts/*.AppImage
