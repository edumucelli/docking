#!/bin/sh
# Refresh launcher/icon caches after install or removal of Docking desktop assets.

set -eu

desktop_dir="/usr/share/applications"
icon_theme_dir="/usr/share/icons/hicolor"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${desktop_dir}" >/dev/null 2>&1 || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f "${icon_theme_dir}" >/dev/null 2>&1 || true
fi
