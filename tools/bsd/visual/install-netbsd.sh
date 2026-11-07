#!/bin/sh
# The VM image contains the base system but omits NetBSD's native X11 sets.
set -eu

if [ "$(uname -s)" != NetBSD ]; then
    printf '%s\n' 'Run this setup only in the disposable NetBSD desktop VM.' >&2
    exit 1
fi

export PATH="/usr/pkg/bin:/usr/pkg/sbin:/usr/X11R7/bin:$PATH"
if [ ! -x /usr/X11R7/bin/Xvfb ]; then
    release=$(uname -r)
    architecture=$(uname -m)
    directory=$(mktemp -d /tmp/docking-x11.XXXXXX)
    trap 'rm -rf "$directory"' EXIT HUP INT TERM
    for set in xbase xetc xfont xserver; do
        ftp -o "$directory/$set.tar.xz" \
            "https://cdn.netbsd.org/pub/NetBSD/NetBSD-$release/$architecture/binary/sets/$set.tar.xz"
        tar -xpf "$directory/$set.tar.xz" -C /
    done
fi

pkg_add python312 py312-gobject3 py312-cairo py312-test py312-psutil py312-pip \
    py312-numpy gtk3+ libwnck3 xfce4-wm xfce4-conf dbus gettext-tools polkit \
    librsvg adwaita-icon-theme dejavu-ttf xterm
