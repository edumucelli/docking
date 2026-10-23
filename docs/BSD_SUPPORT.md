# FreeBSD and OpenBSD

Docking uses its existing X11 backend on BSD. Use a compositing X11 desktop;
Xfce is the CI reference desktop. Linux Wayland support is described separately
in [WAYLAND_SUPPORT.md](WAYLAND_SUPPORT.md).

## Release packages

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

## Run from source

On FreeBSD, install the Python bindings matching the default Python version:

```sh
pkg install python3 gtk3 libwnck3 libXtst libXScrnSaver
python_suffix=$(python3 -c 'import sys; print(str(sys.version_info.major) + str(sys.version_info.minor))')
pkg install "py${python_suffix}-pygobject" "py${python_suffix}-pycairo" \
  "py${python_suffix}-psutil" "py${python_suffix}-pip"
```

On OpenBSD:

```sh
pkg_add py3-gobject3 py3-cairo py3-psutil py3-pip gtk+3 libwnck3
export PATH="/usr/local/bin:/usr/X11R6/bin:$PATH"
```

From the checkout on either system:

```sh
python3 -m venv --system-site-packages --without-pip .venv
python3 -m pip --python .venv/bin/python install -e .
.venv/bin/docking
```

Run inside the desktop's D-Bus session. For a minimal X session without a session
bus, use `dbus-run-session -- .venv/bin/docking` (install the `dbus` package).
BSD installations use the host's native GTK, Cairo, X11 and psutil libraries.

## Integration coverage

| Feature | FreeBSD | OpenBSD |
| --- | --- | --- |
| Launchers, running applications, minimize/restore, workspaces | Existing X11 backend | Existing X11 backend |
| Edge placement, reservations, zoom, tooltips, menus, autohide | Native X11 | Native X11 |
| System Monitor and Pet CPU readings, memory and disks | Native psutil | Native psutil |
| Process identity | Native process executable and command line | Available command-line evidence; executable lookup is best effort |
| Mounted-volume trash discovery | Native mount table | Native mount table |
| Network status and traffic | Native interface counters, selectable interface | Native interface counters, selectable interface |
| Volume | PulseAudio when available, otherwise `mixer` | PulseAudio when available, otherwise `sndioctl` |
| Battery | Native ACPI through psutil | Native `apm` |
| Temperatures | Native psutil/sysctl when exposed by the kernel | Native `hw.sensors` |
| Session actions | Desktop logout, `acpiconf`, native shutdown | Desktop logout, `zzz`, native shutdown |
| Bluetooth and Cam Shield | Unavailable in the applet picker | Unavailable in the applet picker |

The BSD Network applet reports interfaces, IP addresses and traffic; Wi-Fi
scanning and connection management remain with the host's networking tools.
Hardware readings depend on the machine. VMs commonly have no battery or thermal
sensors, which is represented as unavailable data. Power Profiles needs a
compatible `net.hadess.PowerProfiles` D-Bus service. Microphone applets need
PulseAudio. Session actions follow the host's normal permissions and do not
automatically elevate privileges.

## Visual and behavioral CI

The **CI** workflow calls **BSD desktops** to build and install real FreeBSD
and OpenBSD packages, then run visual checks alongside a Linux control.
**BSD desktops** also supports manual and weekly runs. No BSD machine is needed to review its artifacts. Open a workflow run,
download `bsd-visual-freebsd` or `bsd-visual-openbsd`, extract it, and open
`index.html` locally. The gallery includes full desktop PNGs, per-case settings,
runtime and package-install logs, `results.json`, and JUnit results for
core/platform and BSD applet tests. The VM checks exercise the installed package
from outside the checkout, with the checkout available only for test fixtures.

The harness runs the complete Docking process under private Xvfb, Xfwm and D-Bus
sessions. It checks all four edges, an additional gap, tooltip and menu pixels,
real pointer-driven launch/minimize/restore, zoom, reservations and workarea
recovery, autohide/reveal, and live applet startup. Native smoke tests also read
the guest kernel's actual CPU, memory, mount and network data.

Assertions use independently captured root pixels and window-manager state.
Docking's D-Bus item API provides readiness and input targets. There are no
cross-OS golden-image comparisons: differences in fonts and GTK versions do not
establish a regression by themselves. Behavioral failures remain failures and
retain their available screenshots and logs.

Run the same harness locally on Linux or BSD with native GI/Cairo bindings,
`Xvfb`, `xfwm4`, `dbus-run-session`, and XTest installed:

```sh
python3 -m tools.bsd_visual.run --pytest
# Use another compositing window manager already installed locally:
python3 -m tools.bsd_visual.run --wm 'metacity --sm-disable --composite'
# Debug a single scenario:
python3 -m tools.bsd_visual.run --case placement-bottom
```

The default output is a new directory under `artifacts/bsd-visual/`. An explicit
`--output` directory must be empty so evidence from separate runs cannot mix.
The existing deterministic renderer regression suite remains part of Linux CI.
VM visual checks establish desktop behavior, not physical-device or GPU-driver
compatibility.
