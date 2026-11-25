#!/bin/bash
# Run as root inside a clean Debian, Ubuntu, or Mint development container.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
checks="$(cd "$(dirname "$0")/../deb" && pwd)"
arch="$(dpkg --print-architecture)"
current=(/release/current/*.deb)
test "${#current[@]}" -eq 1
test "$(dpkg-deb -f "${current[0]}" Package)" = docking
test "$(dpkg-deb -f "${current[0]}" Architecture)" = "$arch"
expected="$(dpkg-deb -f "${current[0]}" Version)"
apt-get update -o APT::Update::Error-Mode=any
apt-get install -y --no-install-recommends sudo curl ca-certificates gnupg xvfb xauth dbus libglib2.0-bin sway
desktops=()
if [ -n "${1:-}" ]; then
    # Ubuntu's base-files can sort newer than Mint's release identity package.
    apt-get install -y --allow-downgrades --no-install-recommends "base-files/$1"
    resolver=$(cat /etc/resolv.conf)
    apt-get install -y --no-install-recommends "cinnamon/$1" marco mate-settings-daemon-common xfwm4 wmctrl
    # The desktop installs resolver services that do not run in this container.
    printf '%s\n' "$resolver" > /etc/resolv.conf
    . /etc/os-release
    test "$ID" = linuxmint
    test "$VERSION_CODENAME" = "$1"
    desktops=(cinnamon marco xfwm4)
fi
sh "$checks/../install.sh" --configure
# Publication metadata can be ready before every public APT index is refreshed.
ready=false
for attempt in {1..30}; do
    if apt-get update -o APT::Update::Error-Mode=any && \
        [ "$(apt-cache policy docking | awk '/Candidate:/ {print $2}')" = "$expected" ]; then
        ready=true
        break
    fi
    sleep 10
done
apt-cache policy docking
test "$ready" = true
mkdir -p /tmp/docking-download
cd /tmp/docking-download
apt-get download "docking:$arch=$expected"
sha256sum "${current[0]}" docking_*.deb
test "$(sha256sum "${current[0]}" | cut -d ' ' -f 1)" = "$(sha256sum docking_*.deb | cut -d ' ' -f 1)"
# Run the published setup in this disposable container with APT confirmation preanswered.
printf '%s\n' 'APT::Get::Assume-Yes "true";' > /etc/apt/apt.conf.d/99-docking-verification
rm /etc/apt/sources.list.d/docking.sources /etc/apt/keyrings/docking-cloudsmith.asc
sh "$checks/../install.sh"
test "$(dpkg-query -W -f='${Version}' docking)" = "$expected"
bash "$checks/runtime-smoke.sh" "${desktops[@]}"
previous=(/release/previous/*.deb)
if [ -f "${previous[0]}" ]; then
    prior="$(dpkg-deb -f "${previous[0]}" Version)"
    test "$(dpkg-deb -f "${previous[0]}" Package)" = docking
    test "$(dpkg-deb -f "${previous[0]}" Architecture)" = "$arch"
    dpkg --compare-versions "$prior" lt "$expected"
    # Start with an older standalone installation before adding the repository.
    apt-get remove -y docking
    rm /etc/apt/sources.list.d/docking.sources /etc/apt/keyrings/docking-cloudsmith.asc
    apt-get install -y "${previous[0]}"
    test "$(dpkg-query -W -f='${Version}' docking)" = "$prior"
    # Preserve the actual settings created by the first startup checks.
    find /home/docking-smoke/config-* -name dock.json -exec sha256sum {} \; > /tmp/docking-settings.sha256
    test -s /tmp/docking-settings.sha256
    sh "$checks/../install.sh"
    test "$(dpkg-query -W -f='${Version}' docking)" = "$expected"
    sha256sum -c /tmp/docking-settings.sha256
    bash "$checks/runtime-smoke.sh" "${desktops[@]}"
    echo "Verified signed APT install and upgrade $prior -> $expected on $arch"
else
    echo "Verified signed APT install $expected on $arch; no previous stable architecture asset"
fi
