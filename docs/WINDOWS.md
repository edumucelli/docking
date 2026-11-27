# Windows support

## Runtime and installation

| Environment | Scope |
| --- | --- |
| Windows 11 x64 | Primary desktop and distribution target. |
| Windows Server 2025 x64 | Hosted native backend, GTK, desktop and packaging CI. |
| Windows 11 ARM runner | AMD64 UCRT64 emulation and independent x86 input fixtures; this is not a native ARM64 distribution. |
| Other Windows versions/builds | Additional acceptance is required. Some shell capabilities are build-gated. |

The portable ZIP and per-user installer are unsigned previews. Both include
Python, GTK, GStreamer, and the WebView2 loader, so you can run Docking without
MSYS2. WebView2 applets also need Microsoft's Evergreen WebView2 Runtime.
Install the experimental Qt browser separately if you want to try it.

See [installation and updates](INSTALLATION.md#windows-preview) to install
Docking or [Windows packaging](../packaging/windows/README.md) to build the
installer and portable ZIP.

### Source development

Use native MSYS2 **UCRT64** Python and GTK. MSYS Python and WSL do not exercise
the Windows backend. Install the packages listed in the
[Windows workflow](../.github/workflows/windows.yml), including UCRT64 GCC,
Python/GObject/Cairo, GTK 3, Pillow, NumPy, psutil, gettext and GStreamer with its
base/good/bad plugins. Build and packaging also need setuptools, wheel, build
and NSIS.

From a UCRT64 shell at the repository root:

```sh
bash tools/i18n.sh --compile
python -m pip install --break-system-packages --no-deps --no-build-isolation -e .
python -m tools.windows.ci --stage preflight
python run.py
```

Install native extensions with pacman because MSYS2 Python uses a different
ABI from standard Windows CPython. Source and editable builds use UCRT64
GCC and binutils to compile the AMD64 and x86 input-thread helpers. Wheels
include those DLLs. Keep generated DLLs out of source control.

For WebView2 applets, install the runtime and run:

```powershell
python -m tools.windows.webview2_setup
```

The setup command verifies the pinned SDK archive and installs the loader and
license. Pass `--package` to use an archive you already downloaded. Applets
show as unavailable if the loader or runtime is missing.

The optional Qt companion uses a separate standard CPython x64 environment:

```powershell
py -3.13 -m venv "$env:LOCALAPPDATA\Docking\WebEngine"
& "$env:LOCALAPPDATA\Docking\WebEngine\Scripts\python.exe" -m pip install -r tools/windows/requirements-windows-browser.txt
$env:DOCKING_WEBENGINE_PYTHON = "$env:LOCALAPPDATA\Docking\WebEngine\Scripts\python.exe"
```

Windows applets select WebView2 by default. The Qt companion is experimental.

## Current capabilities

"Implemented" describes the backend and controls that are available. The
limits column lists known limitations and cases that still need testing on
real hardware or in a Windows session.

### Dock, applications and shell

| Area | Implemented behavior | Limits |
| --- | --- | --- |
| Dock surface | Four-edge AppBar reservation, taskbar-safe workarea, visibility/overlap modes, fullscreen lowering/recovery and per-monitor-v2 DPI awareness. | Native pointer observation compensates for GTK Win32 input-shape limitations. Pressure barriers are unavailable. |
| Window tracking | Packaged/classic identities, executable fallback, activation/minimize/close, multiwindow grouping/cycling, restored pins and DWM ghost association. | Ghost association uses optional native exports; unsupported associations are rejected. |
| Previews | Worker-owned WinRT graphics capture, asynchronous GTK updates, retained minimized-window images and bounded caches. | First capture of an already minimized window, protected/HDR content and the Windows capture indicator remain limitations. |
| Applications | AppsFolder/Gio catalog, hidden-package filtering, live inventory recovery, association-only handles, selected-document launch and process provenance. | Ambiguous or unavailable selected-application routes fail without opening a different handler. |
| Pins and export | Private executable/shortcut registration, canonical identity, preserved shortcut arguments/options, Applications drag-to-pin and standalone `.lnk` export to Explorer. | Additional virtual/installer-managed shortcut inventories need acceptance. |
| Files and folders | Native drive/UNC/file-URI decoding, shell default opening, previews, monitoring, copy-path and Explorer OLE2 drops. | Network disconnect/reconnect and cross-monitor gestures need wider acceptance. |
| Search | Native global shortcuts, layout/conflict recovery, asynchronous artwork/previews, workarea bounds and guarded shell-handoff focus. | Windows foreground policy still applies; broader shell/session timing needs acceptance. |
| Keyboard layouts | Per-application Win32/TSF selection, x86/AMD64 helpers, US/French/Dvorak/dead keys, AltGr handling and strict Telex/VNI composition checks. | Additional IMEs, elevated/protected apps, remote and multi-user policies need acceptance. |
| System Tray | Native notification host, SDK ABI matrix, icon pixels/geometry, pointer/keyboard/menu actions, focus return and Explorer handoff/recovery. | Uses a shell-hosting protocol without a documented replacement-host API; see [tray acceptance](../tests/visual/backends/windows/README.md#native-windows-tray-acceptance). |
| Show Desktop | Serialized native shell toggles and observed desktop/window state. | Immediate actions while Explorer is initializing can fail. |
| Workspaces | Listing/switching, names, membership, current-workspace filtering, off-desktop activation and dock view pinning. | Internal shell interfaces are enabled only on identified builds `26100`/`26200`; unknown builds do not get guessed vtable calls. |
| Capture utilities | Screen color picking; desktop/window/region screenshots, image clipboard, PNG Save As, cancellable overlays and validated Window Killer targets. | Physical mixed-DPI displays, HDR/protected content, secure/remote sessions and broader permissions need acceptance. |

### Applets and devices

| Area | Windows implementation | Limits |
| --- | --- | --- |
| System Monitor | Windows backend is selected. | CPU, memory, disk, GPU and temperature acquisition are **unimplemented**; sources return unavailable/empty data. |
| Thermals | Windows backend is selected. | Native thermal acquisition is **unimplemented**. |
| KDE Connect | Unavailable in the Windows applet menu. | The existing backend requires a Unix session bus. A Windows integration is **unimplemented**. |
| Battery | Native system power state/rate and device-container accessory properties. | Physical batteries, UPS, AC/resume transitions and accessory readings need hardware acceptance. |
| Brightness | Identity-matched DDC/CI or WMI backlight, readback and unavailable state. | Real laptop/DDC displays, OEM behavior, HDR and display changes need acceptance. |
| Network and VPN | Adapter/routes/counters, saved-profile Wi-Fi, software radio, native Settings and exact RAS VPN connect/disconnect. | No global network switch. Physical Wi-Fi and broader VPN methods/policies need acceptance. |
| Bluetooth | Adapter/discovery APIs, classic/LE pairing, owned GATT sessions, incoming-audio connections, battery metadata and native file-transfer wizard. | Radio, pairing, connection and telemetry controls have API fixtures; physical RF/device acceptance remains open. No BlueZ Trusted or universal profile-connect equivalent is advertised. |
| Volume and Ambient | Core Audio master level/mute and GStreamer WASAPI playback, noise and loops. | Device/role changes, Bluetooth/RDP routing and physical outputs need broader acceptance. |
| Music | Windows media sessions, transport/artwork and validated per-player Core Audio session groups. | Ambiguous player attribution disables volume; multiple/third-party sessions and device changes need acceptance. |
| Mic Shield | Capture endpoint/session enumeration and mute across all current inputs. | Endpoint mute is not a universal block on exclusive capture; physical/hotplug/policy coverage remains open. |
| Cam Shield | Sensor-process activity and supported-edition machine camera policy with owned elevated helper/recovery. | Lock blocks new sessions; existing readers can continue. Home/unrecognized editions do not advertise policy controls. Physical cameras and standard-user UAC consent/cancel need acceptance. |
| Session | Native lock, logout, restart, shutdown and capability-gated suspend. | Windows owns unsaved-work prompts; physical sleep/Modern Standby and managed/multi-user sessions need acceptance. |
| Caffeine and Power Profiles | Worker-owned display/system power requests and configured/effective Windows power-mode controls. | Power policy, hardware sleep and supported plan/API availability constrain controls. |
| Trash | Native count/open/empty, shell change monitoring/reconnection and recycling with undo. | Network shares/mapped network drives have no client-side Recycle Bin and are rejected. |
| Notifications | Windows notification history, revision-checked removal and private-shell Do Not Disturb controls. | Permission revocation produces unknown state; private ABI failures disable controls. Broader policies/providers need acceptance. |
| WhatsApp and Telegram | Separate WebView2 sessions, badges, native page notifications and downloads; optional Qt companion. | WebView2 persistent worker notifications are unsupported. Qt remains experimental and lacks H.264/AAC MP4 support. Signed-in service/media acceptance is incomplete. |
| AI Usage | Headless native hook arguments, UTF-8 storage and named-mutex writer coordination. | Live provider CLI/session coverage remains open. |
| Desk Presence and Caps Lock | Session idle time and native Caps/Num Lock indicators. | Remote/secure sessions, suspend/resume and long-running/hardware behavior need acceptance. |

## Development and validation

- Follow the [platform boundaries](DEVELOPMENT.md#platform-boundaries) for
  shared contracts and native adapters. Reusable Win32, COM and WinRT bindings
  live in [`platform/native/windows/`](../docking/platform/native/windows/).
- Run [Windows visual checks](../tests/visual/backends/windows/README.md) for
  installed-wheel validation, physical input, feature probes and tray acceptance.
- Use the [desktop lab](../tools/windows/README.md#desktop-lab) for interactive VM
  runs and [Windows packaging](../packaging/windows/README.md) for release builds.
