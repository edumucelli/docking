#!/bin/bash
# Build a .deb package for docking.
#
# Usage: ./packaging/deb/build.sh
#
# Prerequisites:
#   sudo apt install debhelper dh-python python3-setuptools python3-wheel
#
# Output: ../docking_0.1.0-1_all.deb

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

# Symlink debian/ to project root (dpkg-buildpackage expects it there)
if [ ! -e debian ]; then
    ln -s packaging/deb/debian debian
fi

# Build (unsigned source + binary)
dpkg-buildpackage -us -uc -b

echo ""
echo "Build complete. Package:"
ls -lh ../docking_*.deb 2>/dev/null || echo "  (check parent directory)"

# Clean up symlink
rm -f debian
