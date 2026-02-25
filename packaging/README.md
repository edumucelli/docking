# Packaging

Build scripts for distributing Docking in various formats.

## DEB (Debian/Ubuntu)

```bash
# Install build dependencies
sudo apt install debhelper dh-python python3-setuptools python3-wheel python3-pip

# Build .deb
./packaging/deb/build.sh

# Install
sudo dpkg -i ../docking_0.1.0-1_all.deb
sudo apt-get -f install  # fix any missing deps

# Verify
docking
```

The .deb depends on system GTK packages (`python3-gi`, `gir1.2-gtk-3.0`, etc.)
and bundles pip-only dependencies (`openmeteo-requests`, `retry-requests`) that
aren't available in Ubuntu repos.

## PyPI

```bash
python -m build
twine upload dist/*
```

Users install with: `pip install docking`

## Flatpak / AppImage

*(Coming soon)*
