#!/bin/sh
# Print a dnf/zypper repository definition for the docking RPM repository.
#
# Stable releases publish to any-distro/any-version, so every RPM-based
# distribution reads the same signed index.
set -eu

REPOSITORY="${DOCKING_RPM_REPOSITORY:-docking-rpm}"
BASE_URL="${DOCKING_RPM_BASE_URL:-https://dl.cloudsmith.io/public/docking/${REPOSITORY}}"
FINGERPRINT="${DOCKING_RPM_FINGERPRINT:-04C7240DAD480161C0DFC791859E104126138494}"
KEY_ID="${DOCKING_RPM_KEY_ID:-859E104126138494}"

if [ -z "$FINGERPRINT" ] || [ -z "$KEY_ID" ]; then
    echo "Set DOCKING_RPM_FINGERPRINT and DOCKING_RPM_KEY_ID" >&2
    exit 1
fi

cat <<EOF
[docking-rpm]
name=Docking (${REPOSITORY})
baseurl=${BASE_URL}/rpm/any-distro/any-version/\$basearch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=${BASE_URL}/gpg.${KEY_ID}.key
sslverify=1
type=rpm-md
metadata_expire=300

[docking-rpm-noarch]
name=Docking noarch (${REPOSITORY})
baseurl=${BASE_URL}/rpm/any-distro/any-version/noarch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=${BASE_URL}/gpg.${KEY_ID}.key
sslverify=1
type=rpm-md
metadata_expire=300
EOF
