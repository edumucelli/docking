#!/bin/bash
# Run as root inside a clean Fedora or openSUSE container.
#
# Verifies the signed RPM repository end to end: key fingerprint, repository
# metadata signature, fresh install, contents identical to the GitHub release
# asset, runtime startup, and an upgrade from the preceding stable release.
set -euo pipefail

checks="$(cd "$(dirname "$0")/../deb" && pwd)"
cloudsmith="$(cd "$(dirname "$0")" && pwd)"

EXPECTED_FINGERPRINT="${DOCKING_RPM_FINGERPRINT:-04C7240DAD480161C0DFC791859E104126138494}"
BASE_URL="${DOCKING_RPM_BASE_URL:-https://dl.cloudsmith.io/public/docking/docking-rpm}"

if command -v dnf >/dev/null 2>&1; then
    family=fedora
elif command -v zypper >/dev/null 2>&1; then
    family=suse
else
    echo "Unsupported RPM base: neither dnf nor zypper is available" >&2
    exit 1
fi

refresh() {
    if [ "$family" = fedora ]; then
        dnf -q makecache
    else
        zypper --non-interactive --gpg-auto-import-keys refresh >/dev/null
    fi
}

# Package versions the enabled repositories offer for docking.
repo_versions() {
    if [ "$family" = fedora ]; then
        dnf -q repoquery --available --queryformat '%{VERSION}-%{RELEASE}\n' docking
    else
        # zypper only reports versions with --details, and folds them into a
        # single edition attribute that already reads VERSION-RELEASE.
        zypper --non-interactive --xmlout search --match-exact --type package \
            --details docking | sed -n 's/.*edition="\([^"]*\)".*/\1/p'
    fi
}

repo_install() {
    if [ "$family" = fedora ]; then
        dnf install -y "$@"
    else
        zypper --non-interactive install "$@"
    fi
}

# Weak dependencies are skipped so a successful install proves the hard
# Requires in the spec are complete on their own.
repo_install_strict() {
    if [ "$family" = fedora ]; then
        dnf install -y --setopt=install_weak_deps=False "$@"
    else
        zypper --non-interactive install --no-recommends "$@"
    fi
}

installed_docking() {
    rpm --query --queryformat '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}' docking
}

# The exact contents a package would install, independent of the Cloudsmith
# GPG signature added on upload (which changes the file checksum).
package_payload() {
    rpm --query --queryformat '[%{FILENAMES}\t%{FILEDIGESTS}\n]' "$@" | sort
}

# Bootstrap tooling only. Nothing here can satisfy a docking dependency, so the
# install check further down still exercises the spec's Requires.
if [ "$family" = fedora ]; then
    dnf install -y -q --setopt=install_weak_deps=False gnupg2 curl
else
    zypper --non-interactive install --no-recommends gnupg curl
fi

current=(/release/current/*.rpm)
test "${#current[@]}" -eq 1
arch="$(uname -m)"
test "$(rpm -qp --queryformat '%{NAME}' "${current[0]}")" = docking
test "$(rpm -qp --queryformat '%{ARCH}' "${current[0]}")" = "$arch"
expected="$(rpm -qp --queryformat '%{VERSION}-%{RELEASE}' "${current[0]}")"
expected_nevra="$(rpm -qp --queryformat '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}' "${current[0]}")"

# Repository signing keys are generated per repository, so the fingerprint is
# pinned here rather than trusted on first use.
key=/tmp/docking-cloudsmith.asc
curl --retry 3 -fsSL "$BASE_URL/gpg.key" -o "$key"
fingerprint="$(gpg --batch --show-keys --with-colons "$key" | awk -F: '$1 == "fpr" {print $10; exit}')"
test "$fingerprint" = "$EXPECTED_FINGERPRINT"
rpm --import "$key"

# openSUSE does not ship /etc/yum.repos.d, so each manager reads its own path.
if [ "$family" = fedora ]; then
    repo_dir=/etc/yum.repos.d
else
    repo_dir=/etc/zypp/repos.d
fi
mkdir -p "$repo_dir"
sh "$cloudsmith/rpm-repo.sh" > "$repo_dir/docking-rpm.repo"

# Publication metadata can be ready before every public index is refreshed.
ready=false
for attempt in {1..30}; do
    if refresh && repo_versions | grep -Fxq "$expected"; then
        ready=true
        break
    fi
    sleep 10
done
test "$ready" = true
echo "Repository advertises docking $expected for $arch"

# A minimal base has almost nothing installed, so this install genuinely
# exercises the declared runtime dependencies rather than the test harness.
repo_install_strict docking

test "$(installed_docking)" = "$expected_nevra"
package_payload docking > /tmp/docking-installed.files
package_payload -p "${current[0]}" > /tmp/docking-release.files
test -s /tmp/docking-installed.files
cmp /tmp/docking-installed.files /tmp/docking-release.files
echo "Repository package contents match the $expected release assets"

# Runtime tooling is installed only after the dependency check above, so it
# cannot mask a missing Requires in the package.
if [ "$family" = fedora ]; then
    repo_install xorg-x11-server-Xvfb xorg-x11-xauth dbus-daemon glib2 sway
else
    repo_install xvfb-run xauth dbus-1-daemon glib2-tools sway
fi
bash "$checks/runtime-smoke.sh"

previous=(/release/previous/*.rpm)
if [ -f "${previous[0]}" ]; then
    prior="$(rpm -qp --queryformat '%{VERSION}-%{RELEASE}' "${previous[0]}")"
    prior_nevra="$(rpm -qp --queryformat '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}' "${previous[0]}")"
    test "$(rpm -qp --queryformat '%{NAME}' "${previous[0]}")" = docking
    test "$(rpm -qp --queryformat '%{ARCH}' "${previous[0]}")" = "$arch"
    test "$prior" != "$expected"
fi

if [ -f "${previous[0]}" ] && repo_versions | grep -Fxq "$prior"; then
    # Start from a real older installation, keeping dependencies in place.
    if [ "$family" = fedora ]; then
        dnf remove -y docking
        repo_install "docking-${prior}"
    else
        zypper --non-interactive remove -y docking
        repo_install --oldpackage "docking=${prior}"
    fi
    test "$(installed_docking)" = "$prior_nevra"
    # Preserve the actual settings created by the first startup checks.
    find /home/docking-smoke/config-* -name dock.json -exec sha256sum {} \; > /tmp/docking-settings.sha256
    test -s /tmp/docking-settings.sha256
    if [ "$family" = fedora ]; then
        dnf upgrade -y docking
    else
        zypper --non-interactive update -y docking
    fi
    test "$(installed_docking)" = "$expected_nevra"
    sha256sum -c /tmp/docking-settings.sha256
    bash "$checks/runtime-smoke.sh"
    echo "Verified signed RPM install and upgrade $prior -> $expected on $arch"
elif [ -f "${previous[0]}" ]; then
    # The repository only ever holds published releases, so a preceding stable
    # tag can predate the first RPM publication.
    echo "::notice::Repository does not offer $prior; fresh install only on $arch"
else
    echo "Verified signed RPM install $expected on $arch; no previous stable asset"
fi
