#!/bin/sh
set -eu

fail() { printf '%s\n' "$*" >&2; exit 1; }

apt_dir=/etc/apt
source_file="$apt_dir/sources.list.d/docking.sources"
keyring="$apt_dir/keyrings/docking-cloudsmith.asc"
repository=https://dl.cloudsmith.io/public/docking/docking-apt
fingerprint=811B48CD4A69170DD98F4F49185CED80A7947754

apt_source() {
    distro=${ID:-}
    suite=${VERSION_CODENAME:-}
    if [ -n "${UBUNTU_CODENAME:-}" ]; then
        distro=ubuntu
        suite=$UBUNTU_CODENAME
    fi
    if [ "$distro" = debian ] && [ -z "$suite" ]; then
        case "$(cat "$1" 2>/dev/null || true)" in
            12|12.*|bookworm|bookworm/sid) suite=bookworm ;;
            13|13.*|trixie|trixie/sid) suite=trixie ;;
        esac
    fi
    case "$distro:$suite" in
        ubuntu:jammy|ubuntu:noble|ubuntu:resolute|debian:bookworm|debian:trixie) ;;
        *) fail "Unsupported APT base: $distro/$suite" ;;
    esac
    uri="$repository/deb/$distro"
    printf '%s\n' 'Types: deb' "URIs: $uri" "Suites: $suite" 'Components: main' \
        'Architectures: amd64 arm64' "Signed-By: $keyring"
}

conflict() {
    fail "Existing Docking configuration: $1. Resolve the conflicting Docking entry or file before retrying standard setup."
}
check_file() {
    [ ! -L "$2" ] || conflict "$2"
    if [ -e "$2" ]; then
        [ -f "$2" ] || conflict "$2"
        expected=$(sha256sum < "$1")
        saved=$(sha256sum < "$2") && [ "$expected" = "$saved" ] || conflict "$2"
    fi
}

install_file() (
    if [ -e "$2" ] || [ -L "$2" ]; then
        check_file "$1" "$2"
        exit 0
    fi
    directory=${2%/*}
    [ -d "$directory" ] || sudo install -d -m 0755 "$directory"
    staging=$(sudo mktemp "$directory/.docking.XXXXXXXX")
    trap 'sudo rm -f "$staging"' EXIT
    trap 'exit 1' HUP INT TERM
    sudo install -m 0644 -T "$1" "$staging"

    # Publish the complete file without replacing a concurrent writer's file.
    if ! sudo ln -T "$staging" "$2" 2>/dev/null; then
        [ -e "$2" ] || [ -L "$2" ] || fail "Could not publish Docking configuration: $2. Check the directory and retry."
        check_file "$1" "$2"
    fi
)

refresh_apt_lists() {
    sudo apt-get update -o APT::Update::Error-Mode=any ||
        printf '%s\n' 'APT refresh reported errors. Trying the available package lists.' >&2
    apt_lists_refreshed=true
}

prepare_tools() {
    manager=$1
    gpg_package=$2
    certificates=/etc/ssl/certs/ca-certificates.crt
    if [ "$manager" = zypper ]; then certificates=/etc/ssl/ca-bundle.pem; fi
    set --
    command -v curl >/dev/null 2>&1 || set -- "$@" curl
    command -v gpg >/dev/null 2>&1 || set -- "$@" "$gpg_package"
    [ -s "$certificates" ] || set -- "$@" ca-certificates
    if [ "$#" -gt 0 ]; then
        if [ "$manager" = apt-get ]; then
            refresh_apt_lists
        fi
        sudo "$manager" install "$@" || fail "Could not install setup tools. Check the package manager output, then retry."
    fi
}

verify_key() {
    curl -fsSL "$1/gpg.key" -o "$work/key.asc" || fail "Could not download the Docking signing key. Check your connection and retry."
    key_info=$(gpg --no-options --homedir "$work" --batch --with-colons --show-keys "$work/key.asc") ||
        fail "Could not read the Docking signing key."
    actual=$(printf '%s\n' "$key_info" | awk -F: '
        $1 == "pub" { keys++; primary = 1; next }
        $1 == "sub" { primary = 0 }
        $1 == "fpr" && primary { print $10; primary = 0 }
        END { if (keys != 1) exit 1 }
    ') && [ "$actual" = "$2" ] || fail "Docking signing key verification failed. Check the current installation guide."
}

install_apt() {
    apt_source /etc/debian_version > "$work/docking.sources"
    arch=$(dpkg --print-architecture)
    case "$arch" in
        amd64|arm64) ;;
        *) fail "Unsupported architecture: $arch" ;;
    esac

    check_file "$work/docking.sources" "$source_file"
    for entry in "$apt_dir/sources.list" "$apt_dir"/sources.list.d/*.list "$apt_dir"/sources.list.d/*.sources; do
        [ "$entry" != "$source_file" ] && [ -f "$entry" ] || continue
        contents=$(if [ -r "$entry" ]; then cat "$entry"; else sudo cat "$entry"; fi) ||
            fail "Cannot read APT source $entry. Check its permissions, then retry."
        if printf '%s\n' "$contents" | awk -v file="$entry" -v repo="$repository" '
            /^[[:space:]]*#/ { next }
            /^[[:space:]]*$/ { uris = 0; next }
            file ~ /[.]sources$/ && /^[^[:space:]]/ { uris = sub(/^[Uu][Rr][Ii][Ss]:/, "") }
            file ~ /[.]sources$/ ? uris : $1 ~ /^deb(-src)?$/ {
                sub(/#.*/, "")
                for (i = 1; i <= NF; i++) {
                    if (!match($i, /^[[:alpha:]]+:\/\/[^/]+/)) continue
                    url = tolower(substr($i, 1, RLENGTH)) substr($i, RLENGTH + 1)
                    sub(/^http:/, "https:", url)
                    if (url == repo || index(url, repo "/") == 1) found = 1
                }
            }
            END { exit !found }
        '; then
            conflict "$entry"
        fi
    done

    apt_lists_refreshed=false
    prepare_tools apt-get gnupg
    verify_key "$repository" "$fingerprint"
    check_file "$work/key.asc" "$keyring"
    if [ "$mode" = --install ] && [ "$apt_lists_refreshed" = false ]; then
        refresh_apt_lists
    fi
    install_file "$work/key.asc" "$keyring"
    install_file "$work/docking.sources" "$source_file"
    [ "$mode" = --install ] || return 0

    # Keep other cached indexes available for dependency resolution.
    sudo apt-get update -o "Dir::Etc::sourcelist=$source_file" -o Dir::Etc::sourceparts=- \
        -o APT::Get::List-Cleanup=0 -o APT::Update::Error-Mode=any ||
        fail "Could not refresh the signed Docking repository. Check the APT error above, then retry."

    policy=$(sudo env LC_ALL=C apt-cache policy "docking:$arch")
    if ! printf '%s\n' "$policy" | awk -v uri="$uri" -v suite="$suite/main" -v arch="$arch" '
        $1 == "Candidate:" { candidate = $2; next }
        $1 == "***" { version = $2; next }
        NF == 2 && $2 ~ /^-?[0-9]+$/ { version = $1; next }
        version == candidate && $NF == "Packages" {
            if ($2 == uri && $3 == suite && $4 == arch) found = 1
            else alternate = 1
        }
        END { exit (!found || alternate) }
    '; then
        printf '%s\n' "$policy" >&2
        fail "APT did not select a Docking version from this repository alone. Check the candidate, architecture and APT preferences."
    fi

    held=$(apt-mark showhold docking)
    [ -z "$held" ] || fail "Docking is held in APT. Review the hold before updating."
    sudo apt-get install "docking:$arch" || fail "Could not install Docking. Check the APT error above, then retry."
}

rpm_source() {
    for architecture in '$basearch' noarch; do
        case "$architecture" in
            noarch) name='Docking noarch'; id=docking-rpm-noarch ;;
            *) name=Docking; id=docking-rpm ;;
        esac
        cat <<EOF
[$id]
name=$name
baseurl=$rpm_repository/rpm/any-distro/any-version/$architecture
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=file://$rpm_keyring
sslverify=1
type=rpm-md
metadata_expire=300
skip_if_unavailable=0
EOF
        [ "$manager" != zypper ] || printf '%s\n' 'autorefresh=1'
        printf '\n'
    done
}

install_rpm() {
    rpm_repository=https://dl.cloudsmith.io/public/docking/docking-rpm
    rpm_fingerprint=04C7240DAD480161C0DFC791859E104126138494
    rpm_keyring=/etc/pki/rpm-gpg/docking-cloudsmith.asc
    case "$ID:${VERSION_ID:-}" in
        fedora:44|fedora:45)
            repo_dir=/etc/yum.repos.d
            manager=dnf
            gpg_package=gnupg2
            ;;
        opensuse-leap:16.0|opensuse-tumbleweed:*)
            repo_dir=/etc/zypp/repos.d
            manager=zypper
            gpg_package=gpg2
            ;;
        *) fail "Unsupported RPM base: $ID/${VERSION_ID:-}" ;;
    esac
    arch=$(uname -m)
    case "$arch" in
        x86_64|aarch64) ;;
        *) fail "Unsupported architecture: $arch" ;;
    esac
    command -v "$manager" >/dev/null || fail "Could not find $manager."
    repo_file="$repo_dir/docking-rpm.repo"
    rpm_source > "$work/docking-rpm.repo"
    check_file "$work/docking-rpm.repo" "$repo_file"
    for entry in "$repo_dir"/*.repo; do
        [ "$entry" != "$repo_file" ] && [ -f "$entry" ] || continue
        contents=$(if [ -r "$entry" ]; then cat "$entry"; else sudo cat "$entry"; fi) ||
            fail "Cannot read RPM source $entry. Check its permissions, then retry."
        if printf '%s\n' "$contents" | grep -Eq '^[[:space:]]*\[docking-rpm(-noarch)?\]'; then
            conflict "$entry"
        fi
    done

    prepare_tools "$manager" "$gpg_package"
    verify_key "$rpm_repository" "$rpm_fingerprint"
    check_file "$work/key.asc" "$rpm_keyring"
    install_file "$work/key.asc" "$rpm_keyring"
    sudo rpm --import "$rpm_keyring"
    install_file "$work/docking-rpm.repo" "$repo_file"
    [ "$mode" = --install ] || return 0

    if [ "$manager" = dnf ]; then
        action=install
        if rpm -q docking >/dev/null 2>&1; then action=upgrade; fi
        sudo dnf --refresh "$action" --from-repo=docking-rpm docking
    else
        sudo zypper refresh docking-rpm docking-rpm-noarch
        sudo zypper install --from docking-rpm docking
    fi
}

read_release() {
    unset ID VERSION_ID VERSION_CODENAME UBUNTU_CODENAME
    . "$1"
}

mode=${1:---install}
case "$mode" in
    --install|--configure) read_release /etc/os-release ;;
    --print-source)
        read_release "${DOCKING_OS_RELEASE:-/etc/os-release}"
        apt_source "${DOCKING_DEBIAN_VERSION:-/etc/debian_version}"
        exit
        ;;
    *) fail "Unknown installer option: $mode" ;;
esac

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
trap 'exit 1' HUP INT TERM
case "${ID:-}" in
    fedora|opensuse-leap|opensuse-tumbleweed) install_rpm ;;
    *) install_apt ;;
esac
