# Packaging

Build scripts for distributing Docking in various formats.

## DEB (Debian/Ubuntu)

```bash
# Install build dependencies
sudo apt install debhelper dh-python python3-setuptools python3-wheel python3-pip python3-all pybuild-plugin-pyproject

# Build .deb
./packaging/deb/build.sh

# Install
sudo dpkg -i ../docking_0.1.0-1_all.deb
sudo apt-get -f install  # fix any missing deps

# Verify
docking
```

### How it works

- **Runtime deps**: system GTK/GI packages (`python3-gi`, `gir1.2-gtk-3.0`, etc.)
- **Vendored deps**: all pip dependencies go to `/usr/lib/docking/vendor/` to avoid
  file conflicts with Ubuntu's python3-* packages. The entrypoint adds this path to
  `sys.path` at startup.
- **Assets**: theme JSON files, clock SVG layers, and weather city database are bundled
  via `package_data` in `setup.cfg` (shim for Ubuntu 22.04's older setuptools that
  can't read PEP 621 from `pyproject.toml`). Installed to
  `/usr/lib/python3/dist-packages/docking/assets/`.
- **Tests**: skipped during deb build (no pytest in build env); run in CI instead.

## PyPI

```bash
python -m build
twine upload dist/*
```

Users install with: `pip install docking`

## Flatpak / AppImage

*(Coming soon)*
