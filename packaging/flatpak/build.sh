#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MANIFEST="${ROOT_DIR}/packaging/flatpak/org.docking.Docking.json"
BUILD_DIR="${ROOT_DIR}/build-flatpak"
REPO_DIR="${ROOT_DIR}/flatpak-repo"
OUT_DIR="${ROOT_DIR}/artifacts"
BUNDLE="${OUT_DIR}/org.docking.Docking.flatpak"

flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install -y flathub org.gnome.Platform//46 org.gnome.Sdk//46

flatpak-builder --force-clean --repo="${REPO_DIR}" "${BUILD_DIR}" "${MANIFEST}"

mkdir -p "${OUT_DIR}"
flatpak build-bundle "${REPO_DIR}" "${BUNDLE}" org.docking.Docking

echo "Built Flatpak bundle:"
ls -lh "${BUNDLE}"
