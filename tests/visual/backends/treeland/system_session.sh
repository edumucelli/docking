#!/usr/bin/env bash
# Treeland requires AccountsService even in a nested desktop session.
set -euo pipefail
test -f /.dockerenv
test "$(id -u)" = 0
test "$LAB_UID" -gt 0
mkdir -p /run/dbus
dbus-uuidgen --ensure
dbus-daemon --system --fork --nopidfile
# The wrapper supplies a private passwd entry; it also needs a locked shadow entry.
printf 'lab:!:0:0:99999:7:::\n' >>/etc/shadow
/usr/lib/accounts-daemon >"${LAB_DIR:-/evidence}/accounts.log" 2>&1 &
for _ in $(seq 80); do
    if gdbus call --system --dest org.freedesktop.Accounts \
        --object-path /org/freedesktop/Accounts \
        --method org.freedesktop.Accounts.FindUserById "$LAB_UID" >/dev/null 2>&1; then
        exec setpriv --reuid="$LAB_UID" --regid="$LAB_GID" --keep-groups "$@"
    fi
    sleep 0.1
done
cat "${LAB_DIR:-/evidence}/accounts.log" >&2
exit 1
