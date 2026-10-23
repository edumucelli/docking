#!/bin/bash
# Run as root inside a clean native Debian/Ubuntu container.
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
apt-get install -y --no-install-recommends curl ca-certificates gnupg xvfb xauth dbus libglib2.0-bin sway
curl --retry 3 -fsSL https://dl.cloudsmith.io/public/docking/docking-apt/gpg.key -o /tmp/docking-cloudsmith.asc
fingerprint="$(gpg --batch --show-keys --with-colons /tmp/docking-cloudsmith.asc | awk -F: '$1 == "fpr" {print $10; exit}')"
test "$fingerprint" = 811B48CD4A69170DD98F4F49185CED80A7947754
install -d -m 0755 /etc/apt/keyrings
install -m 0644 /tmp/docking-cloudsmith.asc /etc/apt/keyrings/docking-cloudsmith.asc
sh "$checks/apt-source.sh" > /etc/apt/sources.list.d/docking.sources
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
# Fresh installation must resolve Docking from the signed repository.
apt-get install -y "docking:$arch=$expected"
test "$(dpkg-query -W -f='${Version}' docking)" = "$expected"
bash "$checks/runtime-smoke.sh"
previous=(/release/previous/*.deb)
if [ -f "${previous[0]}" ]; then
    prior="$(dpkg-deb -f "${previous[0]}" Version)"
    test "$(dpkg-deb -f "${previous[0]}" Package)" = docking
    test "$(dpkg-deb -f "${previous[0]}" Architecture)" = "$arch"
    dpkg --compare-versions "$prior" lt "$expected"
    # Remove Docking to begin with a real older installation, keeping dependencies.
    apt-get remove -y docking
    apt-get install -y "${previous[0]}"
    test "$(dpkg-query -W -f='${Version}' docking)" = "$prior"
    # Preserve the actual settings created by the first startup checks.
    find /home/docking-smoke/config-* -name dock.json -exec sha256sum {} \; > /tmp/docking-settings.sha256
    test -s /tmp/docking-settings.sha256
    apt-get install -y --only-upgrade "docking:$arch=$expected"
    test "$(dpkg-query -W -f='${Version}' docking)" = "$expected"
    sha256sum -c /tmp/docking-settings.sha256
    bash "$checks/runtime-smoke.sh"
    echo "Verified signed APT install and upgrade $prior -> $expected on $arch"
else
    echo "Verified signed APT install $expected on $arch; no previous stable architecture asset"
fi
