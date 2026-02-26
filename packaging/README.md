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

The .deb depends on system GTK/GI packages (`python3-gi`, `gir1.2-gtk-3.0`, etc.).
All Python pip dependencies are vendored into `/usr/lib/docking/vendor/` to avoid
file conflicts with Ubuntu's system python3-* packages. The entrypoint adds this
path to `sys.path` at startup.

## PyPI

```bash
python -m build
twine upload dist/*
```

Users install with: `pip install docking`

## Flatpak / AppImage

*(Coming soon)*
