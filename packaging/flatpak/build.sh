#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MANIFEST="${ROOT_DIR}/packaging/flatpak/cc.docking.Docking.json"
LOCAL_MANIFEST="$(mktemp "${ROOT_DIR}/packaging/flatpak/.cc.docking.Docking.local.XXXXXX.json")"
BUILD_DIR="${ROOT_DIR}/build-flatpak"
REPO_DIR="${ROOT_DIR}/flatpak-repo"
OUT_DIR="${ROOT_DIR}/artifacts"
BUNDLE="${OUT_DIR}/cc.docking.Docking.flatpak"

trap 'rm -f "${LOCAL_MANIFEST}"' EXIT

python3 - "${MANIFEST}" "${LOCAL_MANIFEST}" <<'PY'
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
manifest = json.loads(source.read_text(encoding="utf-8"))

# Some distro-packaged flatpak-builder versions expect appstream-compose inside
# the build sandbox even when the host AppStream package installs it elsewhere.
# Flathub can compose metadata from the canonical manifest; local release bundles
# only need the metainfo file exported.
manifest["appstream-compose"] = False

target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
PY

bash "${ROOT_DIR}/tools/i18n.sh" --compile

flatpak remote-add --user --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install --user -y flathub org.gnome.Platform//50 org.gnome.Sdk//50

flatpak-builder --force-clean --repo="${REPO_DIR}" "${BUILD_DIR}" "${LOCAL_MANIFEST}"

mkdir -p "${OUT_DIR}"
flatpak build-bundle "${REPO_DIR}" "${BUNDLE}" cc.docking.Docking

echo "Built Flatpak bundle:"
ls -lh "${BUNDLE}"
