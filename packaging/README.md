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
- compiled `.mo` catalogs are declared in both `pyproject.toml` and `setup.cfg`
- packaging/CI paths still invoke `tools/i18n.sh --compile`

## DEB (Debian/Ubuntu)

```bash
# Install build dependencies
sudo apt install debhelper dh-python python3-setuptools python3-wheel python3-pip python3-all pybuild-plugin-pyproject

# Build .deb
./packaging/deb/build.sh

# Install
sudo dpkg -i ../docking_*_*.deb
sudo apt-get -f install  # fix any missing deps

# Verify
docking
```

### How it works

- **Runtime deps**: system GTK/GI packages plus `python3 (>= 3.10)`
- **Application code**: installed to `/usr/lib/docking/python/` and loaded via the
  `/usr/bin/docking` wrapper so the package stays compatible across supported
  Python 3 minors on the same architecture.
- **Vendored deps**: all pip dependencies go to `/usr/lib/docking/vendor/` to avoid
  file conflicts with Ubuntu's python3-* packages. The entrypoint adds this path to
  `sys.path` at startup.
- **Assets**: theme JSON files, clock SVG layers, and weather city database are bundled
  via `package_data` in `setup.cfg` (shim for Ubuntu 22.04's older setuptools that
  can't read PEP 621 from `pyproject.toml`). Installed to
  `/usr/lib/docking/python/docking/assets/`.
- **Application icon**: add `org.docking.Docking` icon files under
  `packaging/deb/icons/hicolor/<size>x<size>/apps/org.docking.Docking.png` (and
  optional `packaging/deb/icons/hicolor/scalable/apps/org.docking.Docking.svg`).
  The deb build copies this tree to `/usr/share/icons/hicolor/`.
  Do not ship `status/org.docking.Docking.png`; status icons should use
  `org.docking.Docking-symbolic` only to avoid launcher/app-menu icon collisions.
- **Tests**: skipped during deb build (no pytest in build env); run in CI instead.
- **CI validation**: the generated architecture-specific package is installed and checked on both x86_64 and ARM64 runners.
- **Release note**: GitHub Releases publish `linux-x86_64.deb` and `linux-aarch64.deb`.
- **Compression**: binary `.deb` artifacts are built with `xz` compression for
  compatibility with older `dpkg` versions that cannot unpack `control.tar.zst`.

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

- App ID is `cc.docking.Docking`, matching the project-owned `docking.cc`
  domain for Flathub verification.
- Flatpak build installs hicolor icons and a local `hicolor/index.theme` so
  AppStream icon checks pass in sandboxed builds.
- The app requires X11 window management behavior, so the Flatpak manifest enables
  `--socket=x11` and host filesystem access.

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
sudo apt install python3-pip libfuse2
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

## RPM

```bash
# Install tooling
sudo apt install rpm python3-pip

# Build RPM package
./packaging/rpm/build.sh
```

Output artifact:

- `artifacts/docking-*.rpm`

Install locally (RPM-based distros):

```bash
sudo rpm -Uvh artifacts/docking-*.rpm
```

Notes:

- RPM spec: `packaging/rpm/docking.spec`
- Build script: `packaging/rpm/build.sh`
- The RPM is architecture-specific because the packaged vendored Python wheels can include native binaries.
- CI builds the RPM on both x86_64 and ARM64 runners and publishes both variants.
- Python API dependencies used by weather are vendored under `/usr/lib/docking/vendor`.
