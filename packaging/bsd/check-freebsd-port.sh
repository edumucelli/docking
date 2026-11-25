#!/bin/sh
set -eu

work=$(mktemp -d /tmp/docking-port.XXXXXX)
trap 'rm -rf "$work"' EXIT
export PORTSDIR="$work/ports"
python=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
git clone --depth=1 --filter=blob:none --sparse \
    https://github.com/freebsd/freebsd-ports.git "$PORTSDIR"
git -C "$PORTSDIR" sparse-checkout set Mk Templates Keywords \
    "lang/python${python%.*}${python#*.}"

port="$PORTSDIR/x11/docking"
mkdir -p "$port"
cp -R "$(dirname "$0")/freebsd-port/." "$port/"
make -C "$port" BATCH=yes "DISTDIR=$work/distfiles" \
    "DEFAULT_VERSIONS=python=$python python3=$python" \
    checksum stage-qa check-plist package

package=$(make -C "$port" -V WRKDIR_PKGFILE "DEFAULT_VERSIONS=python=$python python3=$python")
pkg add "$package"
pkg check -d docking
test -x /usr/local/bin/docking
PYTHONPATH=/usr/local/lib/docking/python python3 -P - <<'EOF'
import cairo
import gi
import psutil
import docking.launcher

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

assert Gtk.MAJOR_VERSION == 3
EOF
pkg delete -y docking
