# Windows support

Docking runs the shared Python, GTK 3 and Cairo dock through Windows platform
implementations. This document describes the current module boundaries,
available features, validation requirements and remaining work.

- [Runtime and installation](#runtime-and-installation)
- [Module organization](#module-organization)
- [Current capabilities](#current-capabilities)
- [Validation and CI](#validation-and-ci)
- [Native Windows tray acceptance](#native-windows-tray-acceptance)
- [Desktop lab](#desktop-lab)
- [Remaining work](#remaining-work)

## Runtime and installation

| Environment | Scope |
| --- | --- |
| Windows 11 x64 | Primary desktop and distribution target. |
| Windows Server 2025 x64 | Hosted native backend, GTK, desktop and packaging CI. |
| Windows 11 ARM runner | AMD64 UCRT64 emulation and independent x86 input fixtures; this is not a native ARM64 distribution. |
| Other Windows versions/builds | Additional acceptance is required. Some shell capabilities are build-gated. |

The portable ZIP and per-user installer contain Python, GTK, GStreamer and the
WebView2 loader. They do not require an MSYS2 installation on the user's machine.
WebView2 applets additionally require Microsoft's Evergreen WebView2 Runtime.
The experimental Qt browser is not bundled. Packages are unsigned previews.

See [installation and updates](INSTALLATION.md#windows-preview) and
[Windows packaging](../packaging/windows/README.md) for installation, bundle,
installer and release commands.

### Source development

Use native MSYS2 **UCRT64** Python and GTK. MSYS Python and WSL do not exercise
the Windows backend. Install the packages listed in the
[Windows workflow](../.github/workflows/windows.yml), including UCRT64 GCC,
Python/GObject/Cairo, GTK 3, Pillow, NumPy, psutil, gettext and GStreamer with its
base/good/bad plugins. Build and packaging also need setuptools, wheel, build
and NSIS.

From a UCRT64 shell at the repository root:

```sh
python -m pip install --break-system-packages 'openmeteo-requests>=1.2,<1.5' requests-cache retry-requests
bash tools/i18n.sh --compile
python -m pip install --break-system-packages --no-deps --no-build-isolation -e .
python -m tools.windows.ci --stage preflight
python run.py
```

Native extensions come from pacman because MSYS2 Python's ABI differs from
standard Windows CPython. Source/editable builds compile both AMD64 and x86
input-thread helpers using UCRT64 GCC/binutils. Installed wheels contain those
helpers; generated DLLs do not belong in source control.

For WebView2 applets, install the runtime and run:

```powershell
python -m tools.windows.webview2_setup
```

This verifies the pinned SDK archive and installs its loader and license.
`--package` accepts a downloaded SDK archive. Applets do not download missing
dependencies; an absent loader/runtime produces an unavailable state.

The optional Qt companion uses a separate standard CPython x64 environment:

```powershell
py -3.13 -m venv "$env:LOCALAPPDATA\Docking\WebEngine"
& "$env:LOCALAPPDATA\Docking\WebEngine\Scripts\python.exe" -m pip install -r requirements-windows-browser.txt
$env:DOCKING_WEBENGINE_PYTHON = "$env:LOCALAPPDATA\Docking\WebEngine\Scripts\python.exe"
```

Without `DOCKING_WEBENGINE_PYTHON`, Windows applets select WebView2. See
[remaining browser work](#implementation-gaps) before treating the companion as
a replacement.

## Module organization

Shared contracts, values and orchestration stay at each domain's root.
Concrete OS implementations live below that domain's `implementations/`.
Window-management backends remain a separate axis from the host OS.

```text
docking/
├── core/                              # Configuration, persistence and pure logic
├── platform/
│   ├── composition/                   # Select and assemble concrete providers
│   ├── host/
│   │   ├── base.py                    # Headless host contract and OS facts
│   │   └── implementations/{unix,linux,bsd,windows}/
│   ├── desktop/
│   │   ├── base.py                    # Desktop and artwork contracts
│   │   └── implementations/{unix,windows}/
│   ├── applications/
│   │   ├── backend.py                 # Catalog/association/launch/process contracts
│   │   ├── metadata.py                # Background Windows metadata acquisition
│   │   └── implementations/{shared,unix,linux,bsd,windows}/
│   ├── tray/
│   │   ├── {backend,state,menu,service,notifications}.py
│   │   └── implementations/{unix,windows}/
│   ├── backends/
│   │   ├── {base,selection,window_projection,gtk_surface}.py
│   │   └── {x11,wayland,cinnamon,gnome,kwin,reduced,windows}/
│   └── native/windows/                # Reusable Windows ABI and native primitives
├── applets/
│   ├── {platform,services,worker,popup}.py
│   └── <applet>/
│       ├── {applet,state,render,backend}.py
│       └── implementations/<os-family>/
├── search/
│   ├── services/platform.py           # SearchIntegration and WindowFocus
│   └── services/implementations/{unix,windows}/
└── ui/                                # Shared GTK consumers
```

The applet tree is a convention for applets with native backends; portable
applets retain their existing shared modules. An OS package exists only where
there is a concrete implementation.

| Module | Responsibility |
| --- | --- |
| [`platform/composition/`](../docking/platform/composition/) | `PlatformProvider` and `AppletBackendFactories` construct the selected host, desktop, application, session, tray, Search and applet dependencies. |
| [`platform/host/`](../docking/platform/host/) | Paths, persistence, locking, process invocation and mounts without importing GTK. Windows owns its path rules, named mutexes and flush policy. |
| [`platform/desktop/`](../docking/platform/desktop/) | File/target handling, desktop lifecycle, input/menu integration and native artwork. `DesktopServices` supplies the lazy icon source to `IconLoader`. |
| [`platform/applications/`](../docking/platform/applications/) | One `backend.py` defines the native contracts and launch/process records. The registry owns publication and generations; the launcher owns launch selection and process provenance. |
| [`applications/implementations/windows/`](../docking/platform/applications/implementations/windows/) | Gio/AppsFolder discovery, associations, shortcut registration/import/export, selected-document launch and process evidence. |
| [`platform/backends/windows/`](../docking/platform/backends/windows/) | `WindowsSessionBackend` supplies tracking, surfaces/AppBar, visibility, previews, workspaces, Show Desktop, picking, color capture and idle time. |
| [`platform/native/windows/`](../docking/platform/native/windows/) | Shared COM/WinRT, shell, input, process and graphics primitives. It does not select applets or construct GTK UI. |
| [`platform/tray/`](../docking/platform/tray/) | Immutable items/menus, polling and notifications. Windows owns native shell hosting; Unix owns SNI/D-Bus. XEmbed UI remains in the Unix system-tray implementation. |
| [`applets/<name>/implementations/windows/`](../docking/applets/) | Domain-specific Windows device/API work behind the applet's contract. Shared state and rendering do not perform OS discovery. |
| [`search/services/`](../docking/search/services/) | Windows shortcuts and foreground focus. `SearchIntegration.create_focus()` supplies the focus object to the shared Search window. |
| [`applets/webapp/`](../docking/applets/webapp/) | Shared browser contract and service profiles; Unix owns WebKitGTK, Windows owns WebView2 and the optional companion. WhatsApp and Telegram retain separate profiles. |
| [`tools/windows/`](../tools/windows/README.md) | Runtime checks, bundles/installers, release validation and interactive desktop acceptance. |

### Contract and lifecycle rules

- Select concrete implementations in composition/selectors. Shared UI, state
  and orchestration use domain APIs rather than per-call OS checks.
- Keep host OS, GTK display backend and window-management session distinct.
  Linux and BSD continue to use their existing X11/Wayland/session adapters.
- Shared contracts generally use ABCs. Neutral values contain no native handles,
  GTK objects or provider instances.
- Keep discovery, device I/O and blocking calls off GTK. Native resources belong
  to their acquiring worker, apartment or message thread. WebView2 interfaces
  remain on the GTK STA as required by that API.
- Factories create fresh stateful clients when restart requires a new lifetime.
  Stop cancels queued work, invalidates stale replies and releases subscriptions,
  callbacks, workers, handles and native windows.
- Pass existing service objects where they own the operation: `SurfaceService`
  supplies popup workareas, `DesktopServices` supplies artwork, and
  `SearchIntegration` supplies Search focus. Callbacks remain for events and
  factories that need independent resource ownership.
- Preserve Linux/BSD command precedence, defaults, settings, identities, timers
  and unsupported results when changing shared contracts. Unix SNI actions keep
  their default `(0, 0)` coordinates; Windows uses native icon geometry.
- Keep established public entry points and facades. Do not add import-only
  compatibility modules for former internal OS paths.

## Current capabilities

“Implemented” describes the available backend and controls. Hardware fixtures,
native API tests and real-device acceptance provide different evidence; a
rendering fixture does not establish physical device operation.

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
| System Tray | Native notification host, SDK ABI matrix, icon pixels/geometry, pointer/keyboard/menu actions, focus return and Explorer handoff/recovery. | Uses a shell-hosting protocol without a documented replacement-host API; see [tray acceptance](#native-windows-tray-acceptance). |
| Show Desktop | Serialized native shell toggles and observed desktop/window state. | Immediate actions while Explorer is initializing can fail. |
| Workspaces | Listing/switching, names, membership, current-workspace filtering, off-desktop activation and dock view pinning. | Internal shell interfaces are enabled only on identified builds `26100`/`26200`; unknown builds do not get guessed vtable calls. |
| Capture utilities | Screen color picking; desktop/window/region screenshots, image clipboard, PNG Save As, cancellable overlays and validated Window Killer targets. | Physical mixed-DPI displays, HDR/protected content, secure/remote sessions and broader permissions need acceptance. |

Application enumeration and association acquisition share the metadata worker.
The registry distinguishes pending, ready-empty and unavailable results; failed
refreshes preserve the last accepted snapshot. Startup pins and recent IDs stay
in place until discovery can validate them. Launch handles and selected shortcut
generations remain private to their owning backend.

### Applets and devices

| Area | Windows implementation | Limits |
| --- | --- | --- |
| System Monitor | Windows backend is selected. | CPU, memory, disk, GPU and temperature acquisition are **unimplemented**; sources return unavailable/empty data. |
| Thermals | Windows backend is selected. | Native thermal acquisition is **unimplemented**. |
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

## Validation and CI

Run checks from the repository root. Native desktop tools require an owned
interactive Windows session and a fresh output directory. Their reports belong
under `artifacts/`, not committed images.

### Required CI coverage

| Gate | Required coverage |
| --- | --- |
| Shared Linux/BSD checks | Existing tests, typing/lint, visual/BDD, compositor and package/desktop checks remain required for shared changes. |
| [Windows Server](../.github/workflows/windows.yml) | Native backend/core/GTK tests, installed wheel, uncataloged multiwindow grouping/cycling and restored pins, fullscreen recovery, input API/layouts/shortcuts, tray/application domains, Bluetooth fixtures, standalone ZIP, installer/upgrades/startup/uninstall and startup/session measurements. |
| [Windows 11 input](../.github/workflows/windows-input.yml) | The exact Server-built AMD64 wheel, verified installed bytes, independent AMD64/x86 editors, twelve request controls, eight Telex/VNI compositions, both key routes and plain GTK/Search preedit/Return/Escape behavior. |
| [Candidate evidence](../.github/workflows/ci.yml) | Validate Server and client reports against the same source commit, wheel and package identities before release staging. Missing, failed or dependency-skipped evidence cannot authorize publication. |

The explicit Server `server-api` input profile does not claim composition.
Windows 11 `workstation-composition` requires actual composed text. Successful
TSF/profile readback alone cannot satisfy that requirement.

### Local commands

```sh
python -m tools.windows.ci --stage preflight
python -m tools.windows.ci --stage tests
python -m tools.windows.ci --stage gtk
```

Build/install the candidate wheel as described in
[Windows packaging](../packaging/windows/README.md). Use its actual filename
and full source commit in domain checks:

```sh
python -m tools.windows.ci --stage input --installed-root artifacts/windows/runtime --wheel dist/docking.whl --source-sha <full-source-commit>
python -m tools.windows.ci --stage tray --installed-root artifacts/windows/runtime --wheel dist/docking.whl
python -m tools.windows.ci --stage applications --installed-root artifacts/windows/runtime --wheel dist/docking.whl
```

Replace `<full-source-commit>` before running the command. Use
`--input-coverage server-api` only for explicit Server API coverage.
Use `--owned-amd64-editor` for the emulated AMD64 client fixture.
Domain batches collect independent cases after a failure when cleanup permits;
cancellation stops them immediately.

Installed acceptance verifies every shipped `docking/*` file against the
candidate wheel, permits generated bytecode caches and rejects modified,
missing or unshipped implementation files. API return values, protocol
observations, physical input and reviewed screenshots are separate evidence.
A capture labeled `captured-not-reviewed` is not visual acceptance.

The hosted-only `tools.windows.visual.hosted_desktop` wrapper prepares
recognized first-login/runner windows, records actions and temporarily scopes
the privacy-experience policy. It restores that policy afterward and does not
handle arbitrary user windows. Its cleanup policy must stay confined to
disposable hosted runners.

### Feature probes

Run as `python -m tools.windows.visual.<module> --output PATH`. Use `--help`
for device/fixture arguments and destructive desktop modes.

| Area | Probe modules/options |
| --- | --- |
| Startup, placement, fullscreen | `smoke`, `desktop_smoke`, `fullscreen_smoke` |
| Windows, previews and ghosts | `window_smoke`, `windowidentity_smoke`, `capture_smoke`, `preview_smoke`, `preview_cache_smoke`, `ghost_smoke` |
| Catalog, pins and documents | `applications_smoke` (`--classic`, `--check-visibility`, `--check-artwork`), `classic_smoke`, `runtime_pin_smoke` (`--multiple-windows --uncataloged`, `--explorer-drop --shortcut installed`), `launch_provenance_smoke`, `applications_domain_smoke`, `application_export_smoke` |
| Files, folders and drag/drop | `file_smoke`, `trash_smoke --exercise-internal` |
| Search, focus and shortcuts | `shortcut_smoke`, `focus_smoke`, `search_icon_smoke`, `keyboardlayout_smoke --search-input`, `input_profile_smoke` |
| Tray and Explorer | `tray_provider_smoke`, `tray_smoke`; see [tray acceptance](#native-windows-tray-acceptance) |
| Battery and display | `battery_smoke --exercise-fixtures`, `brightness_probe`, `brightness_smoke` |
| Network and Bluetooth | `network_smoke`, `vpn_smoke`, `bluetooth_smoke`, `pairing_smoke` |
| Audio and media | `ambient_smoke`, `volume_smoke`, `micshield_smoke`, `music_smoke` |
| Privacy, power and session | `camshield_smoke`, `caffeine_smoke`, `powerprofiles_smoke`, `session_smoke` |
| Desktop utilities | `desktop_action_smoke`, `workspace_smoke`, `windowpicker_smoke`, `colorpicker_smoke`, `screenshot_smoke`, `idle_smoke`, `capslock_smoke` |
| Notifications and usage | `notifications_smoke`, `aiusage_smoke` |
| WebView2 | `webapp_smoke`, `webapp_download_smoke`, `webapp_worker_smoke` |
| Qt companion | `webengine_smoke`, `webengine_dock_smoke`, `webengine_live_smoke`, `webengine_download_smoke` |

Hardware and generated fixtures must be labeled correctly. For example, Wi-Fi,
Bluetooth and DDC/WMI snapshots exercise controls without proving a physical
connection or brightness change. Camera fixtures generate frames rather than
opening a physical camera. Session shutdown/restart needs an external observer;
an accepted API request alone does not prove the transition.

See the [desktop tool guide](../tools/windows/visual/README.md) for host-side
`vm_input`, `vm_pointer`, `vm_audio` and capture helpers.

### Packaging acceptance

ZIP/installer validation includes complete payload hashes, Unicode paths,
installed desktop execution, in-use rejection, a distinct older-version
upgrade, startup-choice preservation/disabling, stable hook launchers and
uninstall that retains preferences and unknown files. Release readiness
requires at least three measured startups and a 120-second session with
complete samples and normal shutdowns.

```sh
python -m tools.windows.release --directory path/to/downloaded-artifact --version X.Y.Z --output artifacts/release
```

This validates the accepted wheel, archive, installer, payload inventory and
domain reports before producing versioned/latest packages and checksums.
Detailed build, upgrade and offline WebView2 instructions remain in
[Windows packaging](../packaging/windows/README.md).

## Native Windows tray acceptance

Run in an owned interactive session with the regular dock stopped. Fixtures
create bounded provider processes and generated icons; they do not promote
icons through registry edits or change user notification settings. Explorer
continues to render balloons.

The `--stage tray` batch collects four fresh-process reports: independent
Explorer API comparison, installed-backend API comparison, GTK versions 0/4,
and GTK versions 3/4 with GUID-only updates. Release staging requires all four.

### API and ownership matrix

```powershell
python -m tools.windows.visual.tray_provider_smoke --mode explorer --all --output artifacts/tray-explorer
python -m tools.windows.visual.tray_provider_smoke --mode backend --all --installed-root artifacts/windows/runtime --wheel dist/docking.whl --output artifacts/tray-backend
```

The independent C# provider calls `Shell_NotifyIconA/W` and
`Shell_NotifyIconGetRect` without importing Docking. Its 40 cases cover x64/x86,
ANSI/Unicode, HWND/uID and GUID identity, versions 0/3/4 and valid V1/V2/V3/full
structures. A second PID tests GUID ownership collisions and GUID-only changes.

Acceptance checks exact raw/decoded flags, Shell32 source-size normalization,
callback ownership, title/SHOWTIP changes, copied pixels and cleanup. Legacy
V1/V2/V3 sizes remove SHOWTIP; the full structure preserves it. Successful API
calls do not establish visible tooltip, keyboard or focus behavior.

Backend traces retain bounded owned packet headers, lengths and decoded state,
without unrelated providers or uninitialized tails. A rejected ADD preserves
the original owner; GUID-only MODIFY metadata and live callback identity are
checked separately. Providers must exit normally and shell routing must return
to Explorer.

### GTK and physical input

```powershell
python -m tools.windows.visual.tray_smoke --verify-geometry --verify-interactions --installed-root artifacts/windows/runtime --wheel dist/docking.whl --output artifacts/tray-gtk-v0-v4
python -m tools.windows.visual.tray_smoke --verify-interactions --legacy-version 3 --modern-version 4 --guid-only-updates --installed-root artifacts/windows/runtime --wheel dist/docking.whl --output artifacts/tray-gtk-v3-v4-guid
```

Require actual hover, rich popups, SHOWTIP off/on/off, Enter/Space, Shift+F10,
native context-menu selection, pixels, hidden/delete/re-add and normal shutdown.
Version 0 uses its legacy keyboard callback sequence; versions 3/4 use
`NIN_KEYSELECT`. Geometry uses independent SDK queries and native popup bounds,
including movement, signed origins and invocation anchors.

Menu Escape must restore both native HWND and GTK row focus. First, duplicate
and ineligible locally attributed `NIM_SETFOCUS` requests must not forward to
Explorer; an outside provider click invalidates the return. A never-registered
provider's request still reaches Explorer. Attribution is bounded to five
minutes while awaiting return, with one second of duplicate grace afterward.

`--restart-explorer` is for an owned VM and verifies shell replacement/handoff.
`--notification-point X Y` requires actual balloon SHOW, HIDE, USERCLICK and
TIMEOUT callbacks. Without that point, balloon acceptance is `not-run`; callbacks
are never synthesized.

### Controlled provider inspection

```powershell
python -m tools.windows.visual.tray_provider_smoke --mode explorer --case 64-unicode-guid-v4-full --hold-phase fresh --hold-seconds 120 --output artifacts/tray-hover-fresh
```

Holds are available at `fresh`, `showtip-on`, `showtip-off`, `collision`,
`guid-modify` and `readded`. Inspect fresh hover/SHOWTIP before GUID-only requests
or ownership collisions alter routing. Explorer may put new icons in overflow.

Owned provider journals expose callback sequences and revisioned
`command-NNNNNN.json` controls for rect, showtip, balloon, menu, focus, visibility,
deletion and snapshots. Do not overwrite unacknowledged commands. An owned
`continue` file ends a hold; `stop` requests normal exit; providers also have
bounded deadlines.

Contracts: [Shell_NotifyIcon](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shell_notifyiconw),
[NOTIFYICONDATA](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/ns-shellapi-notifyicondataw),
[Shell_NotifyIconGetRect](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shell_notifyicongetrect)
and [notification-area guidance](https://learn.microsoft.com/en-us/windows/win32/shell/notification-area).

## Desktop lab

The [lab compose files](../packaging/windows/lab/) run an owned Windows VM with
loopback-only browser/VNC/RDP access and durable jobs under the shared artifact
directory. Supply an official Windows 11 x64 ISO and private environment file
with `DOCKING_WINDOWS_STORAGE`, `DOCKING_WINDOWS_SHARE`,
`DOCKING_WINDOWS_SOURCE`, `DOCKING_WINDOWS_ISO` and
`DOCKING_WINDOWS_PASSWORD`.

```sh
docker compose --env-file /path/to/lab.env -f packaging/windows/lab/compose.yaml -f packaging/windows/lab/compose.install.yaml up -d
python -m tools.windows.visual.lab --share /path/to/shared/artifacts configure-share
python -m tools.windows.visual.lab --share /path/to/shared/artifacts ready
```

After installation, stop gracefully and confirm `storage/windows.boot`. Later
starts use `compose.yaml` without the installation overlay. The original ISO
can be removed after verifying an ISO-free boot; the installed disk and firmware
are the persistent VM.

Run `configure-share` after each container start so SMB leases do not hide host
updates. Check the readiness report's UAC/elevation/integrity facts before
acceptance. Standard-user behavior needs a normal medium-integrity desktop.

Jobs have unique IDs, durable responses and a heartbeat. After an observation
timeout, inspect those files before submitting another copy; an unknown outcome
does not authorize a duplicate job. Stop the regular dock and use isolated
settings/fresh outputs for probes. Prepare the GStreamer registry during runtime
setup rather than counting its initial plugin scan as normal startup.

Capture actual desktop input and native observations. Preserve unrelated files,
applications and settings. Destructive modes such as Explorer restart, Recycle
Bin emptying, camera policy changes and session power actions require their
owned fixtures and verified cleanup. Keep generated results/captures in the
shared artifact directory.

## Remaining work

### Implementation gaps

| Area and code | What remains | Completion criteria |
| --- | --- | --- |
| [System Monitor](../docking/applets/systemmonitor/implementations/windows/backend.py) | Implement CPU sampling, memory fraction, disk usage, GPU telemetry and temperature acquisition. Current methods return `None`/empty data. | Native providers publish real values through existing contracts; independent Windows readings verify them, with responsive polling, unavailable states and clean stop/restart. |
| [Thermals](../docking/applets/thermals/implementations/windows/backend.py) | Provide native thermal acquisition; current snapshots are unavailable. | Validate sensors on real hardware and retain explicit unknown/unavailable values where Windows supplies no data. |
| [Browser host](../docking/applets/webapp/implementations/windows/) | WebView2 does not deliver persistent service-worker notifications. The Qt companion lacks H.264/AAC MP4 support, complete-page saving and bundled installer integration. | A supported host must deliver real worker notifications/click activation, decode required media, preserve separate profiles/download cleanup and work from the installed package. Signed-in service validation is also required. |
| [Surface capabilities](../docking/platform/backends/windows/surface.py) | Pressure barriers/native input-shape capabilities are unavailable. | Implement and validate a native capability before advertising it; retain current pointer/visibility fallback until then. |
| [Show Desktop](../docking/platform/backends/windows/desktop.py) | Immediate actions during Explorer initialization can fail. | Exercise first actions after shell replacement and initialization, observe the requested state, and avoid accidental double toggles. |
| [Windows CI](../.github/workflows/windows.yml) | Harden dependency acquisition and hosted desktop preparation against mirror outages and runner windows interrupting input. | Fresh runs produce complete strict domain evidence; setup failures stay explicit and recovery does not weaken assertions or hide focus failures. |
| [Distribution](../packaging/windows/README.md) | Add Authenticode signing for project-produced binaries and installer. Packages currently have checksums but no publisher signature. | Verify signatures and installation/update behavior on a clean standard-user machine. |

### Validation still required

| Area | Work to complete |
| --- | --- |
| Displays and capture | Physical mixed-DPI/multiple monitors, negative origins, hotplug/rearrangement, drag/selection across displays, fullscreen/exclusive rendering, HDR and protected content. Check all dock edges, Search bounds, previews, tray anchors and exact workarea restoration. |
| Power and display hardware | Real batteries/UPS/accessories, AC transitions/resume, DDC/CI monitors, laptop WMI backlights, OEM policy, physical sleep/display inhibition and Modern Standby. Fixtures and VM absence states do not close these items. |
| Networking and Bluetooth | Real Wi-Fi association/authentication/signal/radio control, multiple adapters/routes, hotplug, VPN methods/policies/reconnect, physical classic/LE discovery/pairing/removal, GATT/audio ownership, accessory batteries and file transfer. |
| Audio and privacy | Physical/USB/Bluetooth devices, default-device changes, RDP/exclusive audio, multiple media publishers and device removal during capture. Test real camera/IR/proprietary devices and camera-policy UAC consent/cancel on a standard-user desktop. |
| Shell, input and tray | Broader shortcut/virtual-app inventories, delegated launches, first Search activation and independent input across shell handoffs, additional IMEs/apps, third-party tray providers, balloon policy, repeated shell/ghost recovery races and long-lived sessions. |
| Windows compatibility | Additional Windows builds and session/policy configurations, remote/multi-user operation and supported-edition differences. Validate internal workspace/quiet-hours interfaces per build; unsupported APIs must remain unavailable. Native ARM64 packaging is not provided. |
| Web apps and provider integrations | Signed-in WhatsApp/Telegram messaging/media/downloads, real CDN/network faults, managed policies, sustained browser notification/resource usage and live AI provider CLI sessions. Use private test accounts without committing or logging their data. |
| Lifecycle and distribution | Longer sessions, suspend/resume, fresh installs/upgrades on broader machines, owned-resource/handle stability and cleanup after interrupted operations. Retain the existing installed-wheel, older-version upgrade and release-evidence gates. |

Windows does not expose direct equivalents for every Unix control. No BlueZ
Trusted toggle, universal Bluetooth profile connection, global network switch
or client-side network-share Recycle Bin is promised. Capability and permission
checks must keep those differences explicit.

Close remaining items with named native acceptance cases and retained reports,
not import success, fixture-only checks or a single startup screenshot. Shared
contract changes must continue to pass Linux/BSD regression and desktop checks.
