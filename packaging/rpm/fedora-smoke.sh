#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

packages=${1:?RPM artifact directory required}
fedora=$(rpm --eval '%{fedora}')
arch=$(uname -m)
runtime=("$packages"/docking-[0-9]*.fc"$fedora"."$arch".rpm)
devel=("$packages"/docking-devel-*.fc"$fedora"."$arch".rpm)
debuginfo=("$packages"/docking-debuginfo-*.fc"$fedora"."$arch".rpm)
debugsource=("$packages"/docking-debugsource-*.fc"$fedora"."$arch".rpm)
test "${#runtime[@]}:${#devel[@]}:${#debuginfo[@]}:${#debugsource[@]}" = 1:1:1:1

rpm -qlp "${debuginfo[0]}" | grep -E '_ffi.*\.so.*\.debug$'
rpm -qlp "${debugsource[0]}" | grep -E 'pywayland.*\.c$'

dnf install -y --setopt=install_weak_deps=False "${runtime[0]}" \
  xorg-x11-server-Xvfb xorg-x11-xauth dbus-daemon glib2 sway
bash "$(dirname "$0")/../deb/runtime-smoke.sh"

dnf install -y --setopt=install_weak_deps=False "${devel[0]}"
python3 -I -c 'from docking.applets.services import AppletServices'
