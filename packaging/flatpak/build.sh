#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MANIFEST="${ROOT_DIR}/packaging/flatpak/cc.docking.Docking.json"
BUILD_DIR="${ROOT_DIR}/build-flatpak"
REPO_DIR="${ROOT_DIR}/flatpak-repo"
OUT_DIR="${ROOT_DIR}/artifacts"
BUNDLE="${OUT_DIR}/cc.docking.Docking.flatpak"

bash "${ROOT_DIR}/tools/i18n.sh" --compile

flatpak remote-add --user --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install --user -y flathub org.gnome.Platform//50 org.gnome.Sdk//50

flatpak-builder --force-clean --repo="${REPO_DIR}" "${BUILD_DIR}" "${MANIFEST}"

mkdir -p "${OUT_DIR}"
flatpak build-bundle "${REPO_DIR}" "${BUNDLE}" cc.docking.Docking

echo "Built Flatpak bundle:"
ls -lh "${BUNDLE}"
