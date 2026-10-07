# Packaging

Build scripts for distributing Docking in various formats.

## Translation Ownership

Docking translations have four distinct stages:

1. Extract source strings into the template:
   - `bash tools/i18n.sh --extract`
2. Validate template/catalog consistency:
   - `bash tools/i18n.sh --check-pot-sync`
   - `bash tools/i18n.sh --check-catalogs --require-complete`
3. Compile runtime catalogs:
   - `bash tools/i18n.sh --compile`
4. Ship compiled `.mo` files in package outputs

Current ownership model:
- `tools/i18n.sh` is the canonical translation workflow tool.
- CI validates and compiles catalogs.
- Package targets compile translations before install/build steps.
- Python package metadata declares compiled `.mo` files as runtime package data.

Verification helper:

```bash
python3 tools/check_translation_packaging.py
```

That check confirms:
- compiled `.mo` catalogs are declared in `pyproject.toml`
- packaging/CI paths still invoke `tools/i18n.sh --compile`

## DEB (Debian/Ubuntu)

```bash
# Install build dependencies
sudo apt install debhelper dh-python python3-dev python3-setuptools python3-wheel python3-pip python3-all pybuild-plugin-pyproject libwayland-dev wayland-protocols gettext

# Build .deb
./packaging/deb/build.sh

# Install
sudo apt install ../docking_*_*.deb

# If you used dpkg -i and dependencies were left unconfigured:
sudo apt-get -f install

# Verify
docking
```

### How it works

- **Runtime deps**: system GTK/GI packages plus `python3 (>= 3.10)`.
  Native Wayland placement is included through Debian/Ubuntu's
  `gir1.2-gtklayershell-0.1` package.
- **PyWayland**: the package recommends distro `python3-pywayland` where it is
  available. Release builds supply native Python 3.10–3.14 fallbacks under
  `/usr/lib/docking/vendor-pythonX.Y/` on both architectures. These avoid missing
  protocol bindings in distro PyWayland packages. Other Python versions use the
  distro package. To reproduce
  these fallbacks locally, run `bash packaging/deb/build-wayland-vendors.sh
  /tmp/docking-wayland` with Docker, then build with
  `DOCKING_EXTRA_PYWAYLAND=/tmp/docking-wayland bash packaging/deb/build.sh`.
- **Application code**: installed to `/usr/lib/docking/python/` and loaded via the
  `/usr/bin/docking` wrapper so the package stays compatible across supported
  Python 3 minors on the same architecture.
- **Vendored deps**: all pip dependencies go to `/usr/lib/docking/vendor/` to avoid
  file conflicts with Ubuntu's python3-* packages. The entrypoint adds this path to
  `sys.path` at startup.
- **Assets**: theme JSON files, clock SVG layers, and weather city database are
  declared as package data in `pyproject.toml`. Installed to
  `/usr/lib/docking/python/docking/assets/`.
- **Application icon**: add `org.docking.Docking` icon files under
  `packaging/deb/icons/hicolor/<size>x<size>/apps/org.docking.Docking.png` (and
  optional `packaging/deb/icons/hicolor/scalable/apps/org.docking.Docking.svg`).
  The deb build copies this tree to `/usr/share/icons/hicolor/`.
  Do not ship `status/org.docking.Docking.png`; status icons should use
  `org.docking.Docking-symbolic` only to avoid launcher/app-menu icon collisions.
- **Tests**: skipped during deb build (no pytest in build env); run in CI instead.
- **CI validation**: native amd64 and arm64 packages are installed on Ubuntu
  22.04/24.04/26.04 and Debian 12/13. Checks launch Docking on X11 and headless
  Sway, require its D-Bus API to respond, verify native Wayland protocols, and
  require graceful shutdown. Publication also verifies signed APT downloads,
  fresh installs, and upgrades from the previous stable release.
- **Release note**: GitHub Releases publish `linux-x86_64.deb` and `linux-aarch64.deb`.
- **Compression**: binary `.deb` artifacts are built with `xz` compression for
  compatibility with older `dpkg` versions that cannot unpack `control.tar.zst`.

## Package repositories (Cloudsmith)

Stable releases publish to Cloudsmith after the installation matrix passes:
`.deb` artifacts to `docking-apt` and `.rpm` artifacts to `docking-rpm`.
Publication uses OIDC, validates both architectures, and supports safe retries of
a specific release tag. Both formats publish from the same release, and CI installs
the built RPM in Fedora and openSUSE containers before any publication happens.

See the [maintainer guide](cloudsmith/README.md) for configuration, the re-signing
caveat that makes RPM identity checks different from APT, and retries. User
instructions live in the
[APT](../README.md#debian-and-ubuntu-apt) and
[RPM](../README.md#fedora-and-opensuse-rpm) sections.

## PPA (Launchpad)

```bash
# Install tooling
sudo apt install devscripts dput-ng

# Build signed source package for a Ubuntu series
./packaging/ppa/build.sh noble

# Upload to your Launchpad PPA
./packaging/ppa/upload.sh <launchpad-id>/<ppa-name>
```

Notes:

- PPA uploads reuse the Debian metadata in `packaging/deb/debian/`.
- Launchpad accepts signed source packages, not prebuilt `.deb` files.
- Built source artifacts are copied to `artifacts/ppa/`.
- See `packaging/ppa/README.md` for versioning and upload examples.

## PyPI

```bash
python -m build
twine upload dist/*
```

Users install with: `pip install docking`

## Flatpak

```bash
# Install tooling
sudo apt install flatpak flatpak-builder

# Build bundle
./packaging/flatpak/build.sh
```

Output bundle:

- `artifacts/cc.docking.Docking.flatpak`

Install locally:

```bash
flatpak install --user ./artifacts/cc.docking.Docking.flatpak
flatpak run cc.docking.Docking
```

### Notes

- App ID is `cc.docking.Docking`; system packages keep the shared
  `org.docking.Docking` desktop file and icons.
- Native Wayland layer-shell placement is built into the Flatpak through a
  bundled `gtk-layer-shell` module, and live protocol support is included through
  `pywayland`.
- Flatpak build installs hicolor icons and a local `hicolor/index.theme` so
  AppStream icon checks pass in sandboxed builds.
- The Flatpak keeps `--socket=x11` for compatibility and also enables
  `--socket=wayland` so backend selection can use native Wayland where the
  compositor exposes the required protocols.

## Snap

```bash
# Install tooling
sudo apt install snapcraft

# Build snap package
(
  cd packaging/snap
  sudo snapcraft pack --destructive-mode --output ../../artifacts/docking.snap
)
```

Install locally:

```bash
sudo snap install --dangerous artifacts/docking.snap
```

Notes:

- Snap manifest: `packaging/snap/snapcraft.yaml`
- Snap `grade` is `stable`; `confinement` remains `devmode` to support current desktop integration paths.

## AppImage

```bash
# Install tooling
sudo apt install python3-apt python3-pip libfuse2 libgdk-pixbuf2.0-bin libglib2.0-bin libgtk-3-bin squashfs-tools
python3 -m pip install --upgrade pip
python3 -m pip install appimage-builder

# Build AppImage
./packaging/appimage/build.sh
```

Output artifact:

- `artifacts/Docking-<arch>.AppImage` (`x86_64` or `aarch64`)

Run locally:

```bash
chmod +x artifacts/Docking-x86_64.AppImage
./artifacts/Docking-x86_64.AppImage
```

Notes:

- AppImage recipe: `packaging/appimage/AppImageBuilder.yml`
- Build script: `packaging/appimage/build.sh`
- Runtime dependencies are bundled from Ubuntu 22.04 packages listed in the recipe.
- The AppImage build script selects the correct Ubuntu mirror, package architecture, typelib path, and output name for `x86_64` vs `aarch64`.
- The AppImage bundles GtkLayerShell runtime libraries.

## RPM

The Ubuntu build instructions below require Docker to be installed and usable
by your user for the target-specific native fallback builds.

```bash
# Install tooling
sudo apt install rpm python3-pip python3-venv gettext python3-dev libwayland-dev wayland-protocols pkg-config gcc

# Use current Python build tooling, including on Ubuntu 22.04.
RPM_BUILD_ENV="$(mktemp -d)"
python3 -m venv "$RPM_BUILD_ENV"
. "$RPM_BUILD_ENV/bin/activate"
python -m pip install --upgrade pip setuptools wheel

# Build native fallbacks for the Fedora and openSUSE Python versions.
bash packaging/rpm/build-wayland-vendors.sh "$RPM_BUILD_ENV/wayland-vendors"

# Build RPM package
DOCKING_EXTRA_PYWAYLAND="$RPM_BUILD_ENV/wayland-vendors" ./packaging/rpm/build.sh
deactivate
```

Output artifact:

- `artifacts/docking-*.rpm`

Install locally (RPM-based distros):

```bash
# Fedora
sudo dnf install ./artifacts/docking-*.rpm

# openSUSE (local build artifacts are unsigned)
sudo zypper install --allow-unsigned-rpm ./artifacts/docking-*.rpm
```

Notes:

- RPM spec: `packaging/rpm/docking.spec`
- Build script: `packaging/rpm/build.sh`
- The RPM is architecture-specific because the packaged vendored Python wheels can include native binaries.
- CI builds the RPM on both x86_64 and ARM64 runners and publishes both variants.
- Python API dependencies used by weather are vendored under `/usr/lib/docking/vendor`.
- `Requires` in the spec serve Fedora and openSUSE from one artifact, so the
  names that differ between them are written as RPM boolean dependencies, such as
  `(gtk3 or typelib-1_0-Gtk-3_0)`.
- CI runs `packaging/rpm/build-wayland-vendors.sh` and passes the result through
  `DOCKING_EXTRA_PYWAYLAND`, because the Ubuntu build host cannot produce PyWayland
  glue for the Fedora and openSUSE Python versions, and both distributions package
  PyWayland older than the protocol modules the Wayland backend imports.

Stable releases also publish the RPM to the `docking-rpm` Cloudsmith repository; see
the [maintainer guide](cloudsmith/README.md) and the
[RPM installation instructions](../README.md#fedora-and-opensuse-rpm).

## Arch

```bash
# Install tooling
sudo pacman -S --needed base-devel git python python-pip gettext

# Build package
./packaging/arch/build.sh

# Install locally
sudo pacman -U artifacts/docking-*.pkg.tar.*
```

## Nix

```bash
# Build package output
./packaging/nix/build.sh

# Run from the build output
./result-nix/bin/docking
```
