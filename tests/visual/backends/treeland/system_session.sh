#!/usr/bin/env bash
# Treeland requires AccountsService even in a nested desktop session.
set -euo pipefail
test -f /.dockerenv
test "$(id -u)" = 0
test "$LAB_UID" -gt 0
mkdir -p /run/dbus
dbus-uuidgen --ensure=/etc/machine-id
dbus-uuidgen --ensure
dbus-daemon --system --fork --nopidfile
# Arch's dbus package delegates activation to systemd, absent in this container.
/usr/lib/polkit-1/polkitd --no-debug >"${LAB_DIR:-/evidence}/polkit.log" 2>&1 &
# Placement assertions distinguish dock pixels from a plain desktop background.
python3 -c 'import cairo; cairo.ImageSurface(cairo.FORMAT_RGB24, 1, 1).write_to_png("/tmp/docking-background.png")'
schema=/usr/share/dsg/configs/org.deepin.dde.treeland/org.deepin.dde.treeland.user.json
jq '.contents.defaultBackground.value = "/tmp/docking-background.png"' "$schema" >/tmp/treeland-user.json
cp /tmp/treeland-user.json "$schema"
install -d -o deepin-daemon -g deepin-daemon /var/lib/dde-dconfig-daemon
runuser -u deepin-daemon -- env HOME=/var/lib/dde-dconfig-daemon DSG_DATA_DIRS=/usr/share/dsg \
    /usr/bin/dde-dconfig-daemon -e false >"${LAB_DIR:-/evidence}/dconfig.log" 2>&1 &
# The wrapper supplies a private passwd entry; it also needs a locked shadow entry.
printf 'lab:!:0:0:99999:7:::\n' >>/etc/shadow
/usr/lib/accounts-daemon >"${LAB_DIR:-/evidence}/accounts.log" 2>&1 &
for _ in $(seq 80); do
    if gdbus call --system --dest org.freedesktop.Accounts \
        --object-path /org/freedesktop/Accounts \
        --method org.freedesktop.Accounts.FindUserById "$LAB_UID" >/dev/null 2>&1 \
        && [ "$(gdbus call --system --dest org.freedesktop.DBus \
            --object-path /org/freedesktop/DBus --method org.freedesktop.DBus.NameHasOwner \
            org.desktopspec.ConfigManager)" = '(true,)' ]; then
        exec setpriv --reuid="$LAB_UID" --regid="$LAB_GID" --keep-groups "$@"
    fi
    sleep 0.1
done
cat "${LAB_DIR:-/evidence}/accounts.log" "${LAB_DIR:-/evidence}/dconfig.log" >&2
exit 1
