#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

bash "${ROOT_DIR}/tools/i18n.sh" --compile

mkdir -p artifacts

appimage-builder --recipe packaging/appimage/AppImageBuilder.yml --skip-test

mv -f ./*.AppImage artifacts/
ls -lh artifacts/*.AppImage
