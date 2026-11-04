#!/bin/bash
# Prepare desktop caches before exercising an installed Arch-family package.
set -euo pipefail
checks="$(cd "$(dirname "$0")/../deb" && pwd)"

# CachyOS pacman can refuse post-install hooks when Docker prevents their
# network namespace isolation. Rebuild the caches without relaxing isolation.
update-mime-database /usr/share/mime
glib-compile-schemas /usr/share/glib-2.0/schemas

bash "$checks/runtime-smoke.sh"
