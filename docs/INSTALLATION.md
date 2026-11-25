# Installation

[Back to Docking](../README.md)

Install a release package using the instructions below. For a source checkout,
see the [development guide](DEVELOPMENT.md#source-setup). Compositor setup and
feature coverage are in the [Wayland support](#wayland-support).

The [website](https://docking.cc/#install) also provides expandable APT and RPM
installation instructions.

## Install with a package manager

[Read the installer source](https://docking.cc/install.sh.txt) before running
the command. The installer selects APT, DNF, or Zypper for your system.

```sh
(installer=$(curl -fsSL https://docking.cc/install.sh) && sh -c "$installer")
```

Without curl, [download install.sh](https://docking.cc/install.sh) and run
`sh install.sh` from the download folder.

If setup reports an existing configuration, you can keep your managed setup.
To switch to the installer's configuration, back up the reported repository
file and resolve the conflict before retrying.

- For `docking.sources` or `docking-rpm.repo`, move any unrelated repository
  entries to a separate `.sources` or `.repo` file in the same directory. Then
  remove the original Docking file so the installer can recreate it.
- For other repository files, remove only the conflicting Docking entry and
  keep the remaining entries in place.

## Debian and Ubuntu (APT)

On Ubuntu 22.04/24.04/26.04 and Debian 12/13, add Docking's APT repository once
to receive updates through your package manager. Both amd64 and arm64 are
available. Ubuntu derivatives that expose
`UBUNTU_CODENAME`, including Linux Mint 21.x/22.x, use their Ubuntu base.
The APT release checks install Docking on Linux Mint 22.3 and launch it on X11
with Cinnamon, MATE's Marco, and Xfce's Xfwm.

[Run the installer](#install-with-a-package-manager) once on each machine,
including if you already installed a standalone `.deb`. For local installation,
[download the standalone package](#release-downloads).

Open Docking from your application menu.

### Update Docking

Once setup is complete, regular APT upgrades include Docking. To refresh package
lists and update Docking directly, run the following command.

```sh
sudo apt update && sudo apt install docking
```

### Launchpad PPA

The [Launchpad PPA](https://launchpad.net/~docking/+archive/ubuntu/docking)
targets Ubuntu 22.04, 24.04, and 26.04 on amd64 and arm64. After Launchpad
publishes the build for your Ubuntu version and architecture, run the commands
below. If you also use Docking's Cloudsmith repository, replace the last command
with the versioned install below.

```sh
sudo add-apt-repository ppa:docking/docking
sudo apt update
sudo apt install docking
```

The Cloudsmith package can take priority over the PPA package. Run
`apt-cache policy docking` and find the version listed above the
`docking/docking` Launchpad entry. Substitute that exact version for `VERSION`.

```sh
sudo apt install docking=VERSION
```

APT prompts for confirmation if this downgrades the installed package. For
future updates from the PPA, disable Docking's Cloudsmith APT source.

## Fedora and openSUSE (RPM)

On Fedora 44/45 and openSUSE Tumbleweed/Leap 16.0, add Docking's RPM repository
once to receive updates through your package manager. Both x86_64 and aarch64 are
available. Run repository setup once, including if you already installed a
[standalone RPM](#release-downloads).

[Run the installer](#install-with-a-package-manager) to add the repository and
install or upgrade Docking.

Open Docking from your application menu.

### Update Docking with DNF or Zypper

Use the command for your system after completing repository setup.

```sh
sudo dnf --refresh upgrade docking  # Fedora
sudo zypper refresh docking-rpm docking-rpm-noarch && sudo zypper update docking  # openSUSE
```

<a id="freebsd-and-openbsd"></a>

## FreeBSD, OpenBSD, and NetBSD


BSD packages are published as GitHub Release assets, starting with the first
release containing BSD support. Current build targets are FreeBSD 15.1/amd64
(Python 3.12), OpenBSD 7.9/amd64 (Python 3.13), and NetBSD 11.0/amd64
(Python 3.12). Use a package matching the OS release and architecture. The
package includes Docking, translations, icons, and
its Python runtime dependencies; GTK, Cairo, GI and psutil use native packages.

Run the dependency installation and package-manager commands as root. Downloads
and checksum verification can run as an ordinary user.

On FreeBSD 15.1/amd64:

```sh
pkg install python312 py312-pygobject py312-pycairo py312-psutil \
  gtk3 libwnck3 dbus libXScrnSaver polkit xterm
FILE=docking-latest-freebsd-15.1-amd64.pkg
PYTHON=python3
BASE=https://github.com/edumucelli/docking/releases/latest/download
fetch "$BASE/$FILE"
fetch "$BASE/$FILE.json"
```

On OpenBSD 7.9/amd64:

```sh
pkg_add py3-gobject3 py3-cairo py3-psutil gtk+3 libwnck3 dbus polkit
FILE=docking-latest-openbsd-7.9-amd64.tgz
PYTHON=python3
BASE=https://github.com/edumucelli/docking/releases/latest/download
ftp -o "$FILE" "$BASE/$FILE"
ftp -o "$FILE.json" "$BASE/$FILE.json"
```

On NetBSD 11.0/amd64, configure a matching pkgsrc binary repository and a
compositing X11 desktop (the reference desktop is Xfce). Then:

```sh
export PATH="/usr/pkg/bin:/usr/pkg/sbin:/usr/X11R7/bin:$PATH"
pkg_add python312 py312-gobject3 py312-cairo py312-psutil gtk3+ libwnck3 dbus polkit \
  librsvg adwaita-icon-theme
FILE=docking-latest-netbsd-11.0-amd64.tgz
PYTHON=/usr/pkg/bin/python3.12
BASE=https://github.com/edumucelli/docking/releases/latest/download
ftp -o "$FILE" "$BASE/$FILE"
ftp -o "$FILE.json" "$BASE/$FILE.json"
```

Verify the downloaded bytes against the release manifest before installing:

```sh
"$PYTHON" - "$FILE" <<'PYTHON'
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
VERSION=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' "$FILE.json")
mv "$FILE" "docking-${VERSION}.tgz"
pkg_add -D unsigned "./docking-${VERSION}.tgz"

# NetBSD: use the archive's canonical package name.
VERSION=$("$PYTHON" -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' "$FILE.json")
mv "$FILE" "docking-${VERSION}.tgz"
pkg_add "./docking-${VERSION}.tgz"
```

Start `docking` inside the desktop session. For updates, download and verify the
new release, then use `pkg add -f` on FreeBSD or `pkg_add -u -D unsigned` on
OpenBSD, or `pkg_add -u` on NetBSD. NetBSD installs Docking under `/usr/pkg`;
FreeBSD and OpenBSD use `/usr/local`. These are direct release downloads; no third-party package repository
is configured. The release also includes versioned filenames and
`SHA256SUMS-bsd`.

Cam Shield uses BSD kernel file tables to identify camera holders. Its packaged
helper requests administrator authentication for lock/unlock. FreeBSD restores
device permissions and ACLs; OpenBSD saves and restores `kern.video.record`,
which controls recording for all cameras. A previously disabled recording policy
stays disabled after unlocking. Device visibility depends on camera drivers and
the current user's permission to inspect other processes.
Permission-based locking prevents new camera opens; processes already holding
a device remain visible until they close it. OpenBSD's kernel switch blanks
recording even for existing users.

FreeBSD Bluetooth uses the native controller stack. Enable the appropriate
Bluetooth driver/service as described in the
[FreeBSD handbook](https://docs.freebsd.org/en/books/handbook/advanced-networking/#network-bluetooth).
Docking shows controllers and Classic Bluetooth connections, performs discovery,
and opens `bluetooth-config` in a terminal for PIN entry and pairing/profile
configuration. Power and connection changes request administrator authentication.
FreeBSD has no BlueZ trust property; trust and pairing removal are left to native
system configuration. OpenBSD has no native Bluetooth stack, so its Bluetooth
applet remains unavailable.

NetBSD reuses the native BSD process, network-counter, filesystem, and system
monitoring integrations. Battery and temperature readings use `envstat`;
audio uses PulseAudio when installed, otherwise `mixerctl`. Sensor and audio
availability depend on hardware and permissions. Suspend uses NetBSD's ACPI
sleep control and requires the appropriate system privileges and working
hardware support. Bluetooth and Cam Shield applets are unavailable on NetBSD
until their native control integrations are implemented and verified.

## Windows preview

Windows-enabled releases include an **unsigned Windows 11 x64 preview** installer
and portable ZIP on [GitHub Releases](https://github.com/edumucelli/docking/releases).
Both contain Python, GTK and their dependencies. You do not need MSYS2, a source
checkout, a separate Python installation or administrator access. Web-based
applets additionally need Microsoft's
[Evergreen WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/).
The [Windows coverage notes](WINDOWS.md) describe tested behavior and limitations.

Download `docking-latest-windows-x86_64-setup.exe` or
`docking-latest-windows-x86_64.zip`, together with `SHA256SUMS-windows` from the
same release. Versioned filenames are also provided. In PowerShell, compute the
download's checksum and compare it with its matching manifest line:

```powershell
Get-FileHash .\docking-latest-windows-x86_64-setup.exe -Algorithm SHA256
# For the portable package:
Get-FileHash .\docking-latest-windows-x86_64.zip -Algorithm SHA256
```

Checksums verify that the download matches the release; they are not a publisher
signature. Signing is deferred, so Windows may display an unknown-publisher or
SmartScreen prompt. Use the project release source and verify the checksum
before deciding whether to run an unsigned preview.

Run setup as your normal user. It defaults to
`%LOCALAPPDATA%\Programs\Docking`, adds a Start menu shortcut and an Installed Apps
entry, and offers optional launch at login. Windows Startup Apps policy can
override that choice. For the portable package, extract the **entire** ZIP to a
writable directory and open `Docking\Docking.exe`; keep its `runtime` directory
beside the launcher.

Docking's update notice offers **View Release**, which opens the release page.
Download and verify the new package, close Docking and its AI usage-hook processes,
then run the new installer at the registered location. Setup preserves the
existing startup choice unless you change it, and refuses an upgrade while an
owned process is still running. For a portable update, extract the new ZIP into a
fresh directory and use its launcher. Update notices do not install packages.

Remove an installed copy through Windows **Installed Apps**. Uninstall removes
Docking's installed files and owned usage-hook entries while keeping preferences
and unknown files; those files can leave folders behind. Portable copies can be
deleted after their processes close. Neither method removes your saved preferences.

## Release downloads

The latest prebuilt packages are available on
[GitHub Releases](https://github.com/edumucelli/docking/releases) and linked
directly below. Standalone packages are installed from a file. Add the
[APT](#debian-and-ubuntu-apt) or [RPM](#fedora-and-opensuse-rpm) repository
separately to receive future Docking releases through your package manager.

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

- Linux desktop with X11 or Wayland, or FreeBSD/OpenBSD/NetBSD with composited X11.
- BSD native packages: [FreeBSD, OpenBSD, and NetBSD](#freebsd-and-openbsd).
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

## Wayland support

Use [Diagnostics](CONFIGURATION.md#first-use) to inspect the current session.

Docking selects a backend from the current desktop session. You can also force
one with `DOCKING_BACKEND`.

| Backend | Compositor | Coverage |
|---|---|---|
| **GNOME Shell bridge** | GNOME / Mutter 45+ | Dock placement, native windows/actions/previews, workspaces, workspace-aware dodge, Show Desktop, Alt+Tab hiding, Mutter idle time and optional portal color picking |
| **KWin** | KDE Plasma 6 Wayland | Layer-shell placement, native scripting windows/actions/state/geometry, workspaces/filtering, dodge, Show Desktop, optional idle/color picking and authorized ScreenShot2 previews. AT-SPI remains fallback when scripting is unavailable |
| **COSMIC** | COSMIC Wayland | Native layer-shell placement, toplevel state/actions, workspace filtering and geometry-based dodge using verified workspace membership, plus advertised preview and idle protocols |
| **Hyprland** | Hyprland Wayland | Layer-shell placement, IPC windows/focus/close, per-output and special-workspace dodge, native workspaces, optional idle/previews. Minimization is unsupported |
| **Niri** | Niri Wayland | Layer-shell placement, IPC windows/focus/close/previews/workspaces, idle and overlap where native tile positions are known. Current Niri exposes positions for floating windows, not tiled windows |
| **Sway** | Sway Wayland | Layer-shell placement, native IPC windows/focus/close/geometry/workspaces/filtering and dodge, optional idle/previews. Minimization is unsupported |
| **Wayfire** | Wayfire Wayland | Layer-shell placement, screen coordinates across outputs, workspace-cell filtering, IPC windows/actions, dodge, window/color picking and idle notifications. Minimize, Show Desktop and workspace switching depend on the loaded IPC plugins |
| **Native layer-shell** | Protocol-capable Wayland compositors | Independent window/workspace/preview/idle protocol support. Standard toplevel listing alone is read-only, without focus, geometry or window actions |
| **Cinnamon Wayland** | Cinnamon 6.4+ | Dock placement, running-window tracking, focus/cycling, minimize and close through Cinnamon's built-in shell API, including activation across workspaces. Newer Muffin uses layer-shell placement when available. Native services also provide workspaces and filtering, dodge hiding, previews, Show Desktop, idle time, color picking and window selection. Read-only window tracking remains a fallback when only Muffin's snapshot API is available. |
| **Native layer-shell** | GameScope | Dock placement as a GameScope external overlay. Docking automatically uses `GAMESCOPE_WAYLAND_DISPLAY`, including sessions started without `--expose-wayland`. GameScope does not expose general window management |
| **Native layer-shell** | Jay | Dock placement, window actions, workspaces, previews, and idle time after granting Docking the required Jay client capabilities |
| **Native layer-shell** | Miriway | Dock placement, window actions, and workspaces when Docking is launched as a trusted Miriway shell component |
| **Native layer-shell** | Phosh / phoc | Dock placement, running windows/actions and advertised idle protocols. Docking leaves Phosh's private shell protocol unclaimed during normal startup; public image-capture protocols determine preview availability |
| **Treeland** | Deepin Wayland | Standard layer-shell window tracking and previews, plus Treeland Show Desktop and overlap-driven hiding. Left-edge overlap falls back until Treeland fixes its current left-anchor checker |
| **Reduced** | Cage, Weston, and Wayland compositors without a supported integration | Launcher-only mode with no running indicators, previews, or workspace switching. Cage is a single-application kiosk without a suitable layer-shell surface; Weston reserves its shell protocols for its configured shell or IVI controller |

### GNOME Shell Bridge

On GNOME, Docking uses a companion GNOME Shell extension
(`docking-bridge@docking.org`) that provides window management, previews,
workspace switching, and Show Desktop over a private session D-Bus interface.

```bash
# Install and enable the extension (one-time)
tools/linux/gnome_bridge.sh install

# Run the dock with the GNOME Shell bridge backend
DOCKING_BACKEND=gnome-shell docking
```

System packages include the extension. AppImage and Nix users should run
`tools/linux/gnome_bridge.sh install` once, or copy
`docking/platform/backends/gnome/extension/` into the GNOME Shell user
extensions directory.

### KWin / KDE Plasma 6

Docking auto-detects KDE Plasma 6 and uses the KWin backend without extra
configuration. The support table above summarizes the capabilities that KWin's
public interfaces make available. You can also select it explicitly with
`DOCKING_BACKEND=kwin`.

Docking loads, runs and unloads only its own uniquely named KWin script. It does
not start or change user scripts. Native compositor UUIDs identify windows;
AT-SPI identifiers are never used for native actions or capture. Script denial
or unavailability retains accessibility-based tracking.

ScreenShot2 previews require KWin's screenshot effect and authorization from an
installed desktop entry. Missing or denied capture returns no preview, never a
whole-screen or active-window substitute. Source launches may be unauthorized.

### Native layer-shell

On Cinnamon Wayland, Docking prefers native layer-shell when available. Older
Muffin uses an XWayland dock surface so the dock stays out of Cinnamon's panel
application list and Alt+Tab. Window tracking and applet services still use
Cinnamon's native API, including for native Wayland applications. This fallback
requires a working XWayland display; explicit `GDK_BACKEND` and unrelated
`DOCKING_BACKEND` choices are preserved.

Native layer-shell mode needs a compositor with `zwlr_layer_shell_v1`.
See [Source Requirements](#source-requirements) for the
`gtk-layer-shell` GIR and optional live-protocol dependencies needed by source installs.

Check capabilities:

```bash
wayland-info | grep -E 'zwlr_layer_shell_v1|zwlr_foreign_toplevel_manager_v1|ext_workspace_manager_v1'
```

To force a specific backend for testing:

```bash
DOCKING_BACKEND=gnome-shell docking          # GNOME / Mutter 45+
DOCKING_BACKEND=kwin docking                  # KDE Plasma 6 Wayland
DOCKING_BACKEND=sway docking                  # Sway IPC + layer-shell
DOCKING_BACKEND=cosmic docking                # COSMIC protocols + layer-shell
DOCKING_BACKEND=hyprland docking              # Hyprland IPC + layer-shell
DOCKING_BACKEND=niri docking                  # Niri IPC + layer-shell
DOCKING_BACKEND=wayfire docking               # Wayfire IPC + layer-shell
DOCKING_BACKEND=wayland-layer-shell docking   # wlroots compositors
DOCKING_BACKEND=reduced docking               # any Wayland (no WM integration)
DOCKING_BACKEND=x11 docking                   # X11 (full support)
```

### Jay client capabilities

Jay restricts window-management, workspace, capture, and idle protocols to
approved clients. Launch Docking with a connection tag:

```bash
jay run-tagged docking docking
```

Then grant that tag the protocols used by the generic Wayland backend:

```toml
[[clients]]
match.tag = "docking"
capabilities = [
    "layer-shell",
    "foreign-toplevel-manager",
    "foreign-toplevel-list",
    "workspace-manager",
    "screencopy",
    "idle-notifier",
]
```

Jay replaces its default permissions when a client rule matches, so
`layer-shell` must remain in the explicit list. A connection tag is preferred
over matching the process name because it grants these privileges only to the
Docking instance launched through `jay run-tagged`.

### Miriway shell component

Miriway deliberately limits layer-shell, foreign-toplevel management, and
workspace management to trusted shell components. Add Docking to
`~/.config/miriway-shell.config`:

```ini
shell-component=docking
```

Restart Miriway after changing this file. Miriway then launches Docking with
the privileged Wayland socket. Starting Docking as an ordinary application
leaves these protocols hidden and results in reduced launcher-only behavior.
