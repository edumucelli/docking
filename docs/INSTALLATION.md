# Installation

[Back to Docking](../README.md)

Install a release package using the instructions below. For a source checkout,
see the [development guide](DEVELOPMENT.md#source-setup). Compositor setup and
feature coverage are in the [Wayland guide](WAYLAND_SUPPORT.md).

The [website](https://docking.cc/#install) also provides expandable APT and RPM
installation instructions.

## Debian and Ubuntu (APT)

On Ubuntu 22.04/24.04/26.04 and Debian 12/13, add Docking's APT repository once
to receive updates through your package manager. Both amd64 and arm64 are
available. Ubuntu derivatives that expose
`UBUNTU_CODENAME`, including Linux Mint 21.x/22.x, use their Ubuntu base.
Derivative desktops are not separately covered by the Debian/Ubuntu CI matrix.

Download the public signing key and check its fingerprint before continuing:

```bash
sudo apt update
sudo apt install curl ca-certificates gnupg
curl -fsSL https://dl.cloudsmith.io/public/docking/docking-apt/gpg.key \
  -o /tmp/docking-cloudsmith.asc
gpg --show-keys --with-fingerprint /tmp/docking-cloudsmith.asc
```

The fingerprint must be `811B48CD4A69170DD98F4F49185CED80A7947754`.

Copy the complete block below. It detects the distribution base and stops before
writing the source if that base is unsupported or cannot be identified.

```bash
(
set -eu
sudo install -d -m 0755 /etc/apt/keyrings
sudo install -m 0644 /tmp/docking-cloudsmith.asc /etc/apt/keyrings/docking-cloudsmith.asc
. /etc/os-release
APT_DISTRO="${ID:-}"
APT_SUITE="${VERSION_CODENAME:-}"
if [ -n "${UBUNTU_CODENAME:-}" ]; then
    APT_DISTRO=ubuntu
    APT_SUITE="$UBUNTU_CODENAME"
fi
if [ "$APT_DISTRO" = debian ] && [ -z "$APT_SUITE" ]; then
    DEBIAN_VERSION=$(cat /etc/debian_version 2>/dev/null || true)
    case "$DEBIAN_VERSION" in
        12|12.*|bookworm|bookworm/sid) APT_SUITE=bookworm ;;
        13|13.*|trixie|trixie/sid) APT_SUITE=trixie ;;
    esac
fi
case "$APT_DISTRO:$APT_SUITE" in
    ubuntu:jammy|ubuntu:noble|ubuntu:resolute|debian:bookworm|debian:trixie) ;;
    *) echo "Unsupported APT base: $APT_DISTRO/$APT_SUITE" >&2; exit 1 ;;
esac
sudo tee /etc/apt/sources.list.d/docking.sources > /dev/null <<EOF
Types: deb
URIs: https://dl.cloudsmith.io/public/docking/docking-apt/deb/${APT_DISTRO}
Suites: ${APT_SUITE}
Components: main
Architectures: amd64 arm64
Signed-By: /etc/apt/keyrings/docking-cloudsmith.asc
EOF
sudo apt update
sudo apt install docking
)
```

For subsequent updates, run `sudo apt update` and `sudo apt upgrade`. Existing
`.deb` installations can upgrade directly through APT.

## Fedora and openSUSE (RPM)

Repository installation is available after the first verified RPM publication.
Until then, use the [RPM release downloads](https://github.com/edumucelli/docking/releases/latest).

On Fedora 44/45 and openSUSE Tumbleweed/Leap 16.0, add Docking's RPM repository
once to receive updates through your package manager. Both x86_64 and aarch64 are
available.

Download the public signing key and check its fingerprint before continuing:

```bash
if command -v zypper > /dev/null 2>&1; then
    sudo zypper --non-interactive install curl gpg2
else
    sudo dnf install -y curl gnupg2
fi
curl -fsSL https://dl.cloudsmith.io/public/docking/docking-rpm/gpg.key \
  -o /tmp/docking-cloudsmith.asc
gpg --show-keys --with-fingerprint /tmp/docking-cloudsmith.asc
```

The fingerprint must be `04C7240DAD480161C0DFC791859E104126138494`.

Copy the complete block below. It installs the key, writes the signed repository
definition, and selects DNF on Fedora or Zypper on openSUSE.

```bash
(
set -eu
sudo rpm --import /tmp/docking-cloudsmith.asc
# dnf reads /etc/yum.repos.d, while openSUSE ships only /etc/zypp/repos.d.
if command -v zypper > /dev/null 2>&1; then
    REPO_DIR=/etc/zypp/repos.d
else
    REPO_DIR=/etc/yum.repos.d
fi
sudo install -d -m 0755 "$REPO_DIR"
sudo tee "$REPO_DIR/docking-rpm.repo" > /dev/null <<'EOF'
[docking-rpm]
name=Docking
baseurl=https://dl.cloudsmith.io/public/docking/docking-rpm/rpm/any-distro/any-version/$basearch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=https://dl.cloudsmith.io/public/docking/docking-rpm/gpg.key
sslverify=1
type=rpm-md
metadata_expire=300

[docking-rpm-noarch]
name=Docking noarch
baseurl=https://dl.cloudsmith.io/public/docking/docking-rpm/rpm/any-distro/any-version/noarch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=https://dl.cloudsmith.io/public/docking/docking-rpm/gpg.key
sslverify=1
type=rpm-md
metadata_expire=300
EOF
if command -v zypper > /dev/null 2>&1; then
    sudo zypper --gpg-auto-import-keys refresh
    sudo zypper install docking
else
    sudo dnf install docking
fi
)
```

For subsequent updates, run `sudo dnf upgrade` or `sudo zypper update`. Existing
`.rpm` installations can upgrade directly through the repository.

## FreeBSD and OpenBSD


BSD packages are published as GitHub Release assets, starting with the first
release containing BSD support. Current build targets are FreeBSD 15.1/amd64
(Python 3.12) and OpenBSD 7.9/amd64 (Python 3.13). Use a package matching the OS
release and architecture. The package includes Docking, translations, icons, and
its Python runtime dependencies; GTK, Cairo, GI and psutil use native packages.

Run the dependency installation and package-manager commands as root. Downloads
and checksum verification can run as an ordinary user.

On FreeBSD 15.1/amd64:

```sh
pkg install python312 py312-pygobject py312-pycairo py312-psutil \
  gtk3 libwnck3 dbus libXScrnSaver
FILE=docking-latest-freebsd-15.1-amd64.pkg
BASE=https://github.com/edumucelli/docking/releases/latest/download
fetch "$BASE/$FILE"
fetch "$BASE/$FILE.json"
```

On OpenBSD 7.9/amd64:

```sh
pkg_add py3-gobject3 py3-cairo py3-psutil gtk+3 libwnck3 dbus
FILE=docking-latest-openbsd-7.9-amd64.tgz
BASE=https://github.com/edumucelli/docking/releases/latest/download
ftp -o "$FILE" "$BASE/$FILE"
ftp -o "$FILE.json" "$BASE/$FILE.json"
```

Verify the downloaded bytes against the release manifest before installing:

```sh
python3 - "$FILE" <<'PYTHON'
import hashlib, json, sys
from pathlib import Path
package = Path(sys.argv[1])
manifest = json.loads(Path(str(package) + ".json").read_text())
assert hashlib.sha256(package.read_bytes()).hexdigest() == manifest["sha256"], "Checksum mismatch"
print("Verified Docking", manifest["version"], manifest["os"], manifest["release"])
PYTHON
```

Install the verified package as root:

```sh
# FreeBSD
pkg add "./$FILE"

# OpenBSD: GitHub artifacts are unsigned; this exception applies to this file.
pkg_add -D unsigned "./$FILE"
```

Start `docking` inside the desktop session. For updates, download and verify the
new release, then use `pkg add -f` on FreeBSD or `pkg_add -u -D unsigned` on
OpenBSD. These are direct release downloads; no third-party package repository
is configured. The release also includes versioned filenames and
`SHA256SUMS-bsd`.

## Release downloads

The latest prebuilt packages are available on
[GitHub Releases](https://github.com/edumucelli/docking/releases) and linked
directly below.

- `AppImage`: [x64](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-x86_64.AppImage), [arm64](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-aarch64.AppImage)
- `Debian .deb`: [x64](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-x86_64.deb), [arm64](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-aarch64.deb)
- `RPM`: [x64](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-x86_64.rpm), [arm64](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-aarch64.rpm)
- `Flatpak`: [x64](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-x86_64.flatpak), [arm64](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-aarch64.flatpak)
- `Snap`: [x64](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-x86_64.snap), [arm64](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-aarch64.snap)
- `Arch package`: [x64](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-x86_64.pkg.tar.zst), [arm64](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-aarch64.pkg.tar.xz)
- `Nix`: [x64 store path](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-x86_64-nix-store-path.txt), [x64 output tarball](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-x86_64-nix-output.tar.gz), [arm64 store path](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-aarch64-nix-store-path.txt), [arm64 output tarball](https://github.com/edumucelli/docking/releases/latest/download/docking-latest-linux-aarch64-nix-output.tar.gz)

Typical local install/run commands after downloading a release asset:

```bash
# Choose the suffix matching the downloaded asset.
ARCH=x86_64  # Use aarch64 for ARM64.

# AppImage
chmod +x "docking-latest-linux-${ARCH}.AppImage"
./docking-latest-linux-${ARCH}.AppImage

# Debian / RPM
sudo apt install "./docking-latest-linux-${ARCH}.deb"
sudo dnf install "./docking-latest-linux-${ARCH}.rpm"   # or: sudo zypper install ...

# Arch (use the command matching your architecture)
sudo pacman -U "./docking-latest-linux-x86_64.pkg.tar.zst"
sudo pacman -U "./docking-latest-linux-aarch64.pkg.tar.xz"

# Flatpak / Snap
flatpak install --user "./docking-latest-linux-${ARCH}.flatpak"
sudo snap install --dangerous "./docking-latest-linux-${ARCH}.snap"

# Nix output tarball
mkdir docking-nix-output
tar -C docking-nix-output -xf "docking-latest-linux-${ARCH}-nix-output.tar.gz"
./docking-nix-output/bin/docking
```

## Source Requirements

- Linux desktop with X11 or Wayland, or FreeBSD/OpenBSD with X11.
- BSD native packages: [FreeBSD and OpenBSD](#freebsd-and-openbsd).
- Python 3.10+
- Wayland backends:
  - GNOME / Mutter 45+ through the companion `docking-bridge@docking.org` extension
  - KDE Plasma 6 through the native KWin backend
  - COSMIC through native toplevel, workspace, overlap, and preview protocols
  - Sway, Hyprland, Niri, and Wayfire through compositor-specific IPC plus layer-shell
  - wlroots-style compositors through layer-shell and advertised Wayland protocols
  - reduced mode when compositor integration is unavailable
- System packages (Ubuntu/Debian):

```bash
sudo apt install \
  python3-venv \
  python3-gi python3-gi-cairo \
  gir1.2-gtk-3.0 gir1.2-gdkpixbuf-2.0 gir1.2-wnck-3.0 gir1.2-pango-1.0 \
  gir1.2-nm-1.0 gir1.2-gstreamer-1.0 \
  libcairo2-dev libgirepository1.0-dev pkg-config
```

The WhatsApp applet additionally needs WebKitGTK. Install
`gir1.2-webkit2-4.1`, or `gir1.2-webkit2-4.0` on systems such as Ubuntu 22.04.

Native Wayland layer-shell source installs also need the system
`gtk-layer-shell` GIR package:

```bash
# Debian / Ubuntu
sudo apt install gir1.2-gtklayershell-0.1

# Fedora
sudo dnf install gtk-layer-shell

# Arch
sudo pacman -S gtk-layer-shell
```

Release packages include or depend on this where native Wayland support is
advertised. Source installs must install it separately.

Live Wayland protocol clients in source installs need the `[wayland]` extra:

```bash
# Debian / Ubuntu build dependencies for pywayland
sudo apt install libwayland-dev wayland-protocols

pip install -e ".[wayland]"
```
