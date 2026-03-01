#!/usr/bin/env bash
set -euo pipefail

mkdir -p artifacts

appimage-builder --recipe packaging/appimage/AppImageBuilder.yml --skip-test

mv -f ./*.AppImage artifacts/
ls -lh artifacts/*.AppImage
