# Windows support

Windows support is being implemented incrementally on `feature/windows-port`.
The target is the complete dock, with feature parity measured against the
Linux implementation. A startup or unit-test pass does not establish parity.

## First target

Start with **Windows 11 Pro 25H2, x64**, using the normal Microsoft installation
media. Microsoft currently supplies this image through its Windows download
service, and 25H2 remains supported through October 2027. Pro provides RDP for
development; Docking's shared features should also work on Home. Windows 10's
standard support ended in October 2025, so it is a later compatibility target.
Windows 11 26H2 is a follow-up verification target. Version 26H1 targets new
hardware and is not the appropriate general desktop baseline.

Sources: [Windows 11 release information](https://learn.microsoft.com/en-us/windows/release-health/windows11-release-information),
[Windows 10 lifecycle](https://learn.microsoft.com/en-us/lifecycle/announcements/windows-10-end-of-support).

## Development runtime

Use native Windows Python and GTK 3 from MSYS2 **UCRT64**. The MSYS Unix Python
and WSL exercise different operating-system interfaces and do not validate this
port. The existing Cairo renderer, GTK UI, themes, configuration and applet model
remain the product implementation.

In a UCRT64 shell:

```sh
pacman -Syu
pacman -S --needed mingw-w64-ucrt-x86_64-gtk3 \
  mingw-w64-ucrt-x86_64-python mingw-w64-ucrt-x86_64-python-gobject \
  mingw-w64-ucrt-x86_64-python-cairo mingw-w64-ucrt-x86_64-python-pip \
  mingw-w64-ucrt-x86_64-python-psutil mingw-w64-ucrt-x86_64-python-numpy
python -m pip install --break-system-packages 'openmeteo-requests>=1.2,<1.5' requests-cache retry-requests
python run.py
```

References: [GTK on Windows](https://www.gtk.org/docs/installations/windows/),
[MSYS2 environments](https://www.msys2.org/docs/environments/).

MSYS2 Python's C-extension ABI differs from standard Windows CPython. Native
extensions come from pacman. The Windows Open-Meteo dependency uses the
requests-based client releases to avoid building the newer client's qh3
dependency; the forecast API remains the same. See
[MSYS2 Python compatibility](https://www.msys2.org/docs/python/) and
[Open-Meteo 1.4 dependency metadata](https://pypi.org/project/openmeteo-requests/1.4.0/).

## Repeatable Windows desktop lab

`packaging/windows/lab/compose.yaml` pins the Dockur image used by this port.
Use a local official Windows 11 x64 ISO. The lab keeps its disk, screenshots
and jobs outside the source checkout. Its browser, VNC and RDP ports bind only
to localhost. The bootstrap installs Python/GTK and starts a lab agent in the
logged-in Windows user's desktop session.

Set these variables in a private environment file:

```ini
DOCKING_WINDOWS_STORAGE=/absolute/path/to/persistent/storage
DOCKING_WINDOWS_SHARE=/absolute/path/to/shared/artifacts
DOCKING_WINDOWS_SOURCE=/absolute/path/to/windows/worktree
DOCKING_WINDOWS_ISO=/absolute/path/to/official-windows11-x64.iso
DOCKING_WINDOWS_PASSWORD=choose-a-local-lab-password
```

```sh
docker compose --env-file /path/to/lab.env \
  -f packaging/windows/lab/compose.yaml \
  -f packaging/windows/lab/compose.install.yaml up -d
python tools/windows_lab.py --share /path/to/shared/artifacts configure-share
python tools/windows_lab.py --share /path/to/shared/artifacts ready
python tools/windows_lab.py --share /path/to/shared/artifacts --wait 5 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows_smoke.py' \
  --output '\\host.lan\Data\lab\smoke'
```

Jobs receive unique IDs and durable response files under `lab/jobs`. If a host
command times out, inspect the response and heartbeat before submitting another
job. Do not launch a second copy merely because observation timed out.
An agent restart does not replay a claimed job whose outcome is unknown.

Run `configure-share` after each container startup, before submitting jobs.
It disables SMB leases for this dedicated lab and reconnects the lab clients,
so host edits and new job files reach Windows promptly. It leaves Windows
running. The agent retries temporary SMB publication errors while retaining
the completed result.

After the installed Windows desktop has completed setup, stop the container
gracefully and confirm `storage/windows.boot` exists. Subsequent starts use only
`compose.yaml`, without the install overlay. After verifying that boot, the
original ISO can be deleted. Keep the installed disk and firmware files.
The desktop and lab agent were checked after an ISO-free restart. A source
launch immediately after login exceeded the smoke test's 40-second startup
limit; a subsequent import probe took 19.5 seconds over SMB. Packaging and
startup performance remain acceptance work.
Once login settled, the same 40-second smoke check passed again, including
Preferences, persisted resizing, taskbar-safe placement and normal Quit.

The smoke test runs the complete application and records desktop PNGs, native
Win32 window geometry, focus observations, input results and application logs.
An offscreen render is useful for renderer regressions but is not a Windows
desktop acceptance result. Inspect the captured PNGs as well as the assertions.

## First desktop evidence

On October 25, 2026, the complete source application ran on **Windows 11 Pro
25H2, build 26200.8037**, with native UCRT64 Python **3.14.9** and GTK **3.24.52**.
The baseline is one 1280×800 monitor at 96 DPI, with Explorer's taskbar visible.
`smoke-mouse-ui` captured the desktop at startup, during magnification, with
the context menu open, and with Preferences open. Native input opened those
surfaces and selected Quit; the process exited with code 0 without termination.
Hover did not change the foreground window. Ten focused Windows tests passed,
including configuration save/backup and native application metadata contracts.
Gio discovered 184 application records. A subsequent `smoke-workarea` run edited icon size from
48 to 64 through Preferences, verified the persisted JSON value, and checked
the resized native window against Explorer's work area before quitting cleanly.
The larger dock remained fully above the taskbar.

![Docking with 64-pixel icons on Windows 11](../images/windows-first-run.png)

The next desktop milestone adds native `EnumWindows` snapshots, packaged-app
identity through `GetApplicationUserModelId`, normal executable identity,
running indicators, and activate/minimize/close actions. Packaged launches use
the Windows activation manager and pass the user's foreground privilege to it.
Shell icons are extracted on two background workers, then published to GTK.

`windows-native-verified` launched Notepad by clicking its pinned icon, checked
that its window became foreground, clicked the running icon to minimize it,
clicked again to restore and activate it, and selected Close in the item menu.
Independent Win32 observations confirmed every transition. The dock then quit
through its own menu without process termination. Captures also show Notepad's
native icon and running indicator. The baseline remains the same single display.
Fourteen focused tests also passed under native Windows Python. Repeating shell
icon extraction 26 times left the process's GDI handle count unchanged at 40.

A catalog import audit loaded 62 of 65 applet classes. The remaining import
issues are AI Usage's Unix file lock, Ambient's missing GStreamer runtime, and
System Tray's eager XEmbed import. Importing a class does not verify its controls
or native system backend; the catalog acceptance work remains open.

```sh
python tools/windows_lab.py --share /path/to/shared/artifacts run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows_window_smoke.py' \
  --output '\\host.lan\Data\lab\windows-native'
```

![Native Notepad icon and window menu on Windows 11](../images/windows-window-controls.png)

The smoke tool's mouse row coordinates assume that baseline theme and DPI.
Keyboard navigation of the context menu did not pass the initial check.
A native WM_CLOSE request also failed while a menu grab was active. Subsequent
screen-integration checks close the dock normally with WM_CLOSE when no menu is
open and verify restoration of the original desktop work area. Keyboard menu
navigation and shutdown during a menu grab remain follow-up items.

## Desktop reservation and overlap

The Windows surface now uses the native shell AppBar protocol. A transparent,
non-interactive proxy window reserves only the resting shelf; the GTK window
retains its larger animation area. Explorer's taskbar stays at the outer edge.
Shell position/display notifications schedule a coalesced placement refresh,
and shutdown unregisters the AppBar before destroying its proxy.

The reservation phase of `windows-desktop-acceptance` ran the complete dock at
all four edges. At the baseline 48-pixel icon size each edge reserves 53 pixels, while its animation
surface extends 187 pixels. Independent GetMonitorInfo observations verify the
reserved work area, and a maximized blank Notepad follows it. The test records
Notepad's existing native resize margins before comparing its client geometry;
those invisible margins differ from the visible content boundary. Every normal
dock shutdown restores the original `[0, 0, 1280, 752]` work area exactly.
The captured top, bottom, left and right desktops were visually inspected.

![Docking reserving the left desktop edge on Windows 11](../images/windows-screen-reservation.png)

`windows-visibility-native-pointer` passed all six non-reserving modes:
always-on-top, autohide, dodge-active, window-dodge, intelligent and
dodge-maximized. Independent screenshot crops establish hidden/visible states;
all hidden crops matched the covered desktop exactly. In each mode, edge hover
revealed the dock without taking foreground focus from Notepad. Minimizing
Notepad exposed the dock in the overlap modes, while autohide remained hidden.
Closing every test dock left Explorer's original work area intact.

The checks found and fixed startup autohide when the pointer was already
outside the dock. They also established that GTK's input-shape method is a no-op
on Windows. The surface observes the native pointer to reveal a fully transparent
hidden edge, using the renderer's current input rectangle. This fallback does
not confine the pointer or implement pressure barriers; those capabilities
remain disabled. See the [GTK input-shape documentation](https://docs.gtk.org/gdk3/method.Window.input_shape_combine_region.html).

Fifty-two focused tests passed under native Windows Python. The Notepad
launch/minimize/restore/Close desktop check also passed again with reservation
enabled and normal dock shutdown. The baseline remains one display at 96 DPI.
A separate native full-screen GTK fixture was tracked as fullscreen (and not
maximized), with independent Win32 bounds of `[0, 0, 1280, 800]`. Fullscreen
stacking, Explorer restart recovery and broader application/dialog behavior
still need desktop acceptance.

Window overlap is evaluated in the dock monitor's GTK coordinate space. Raw
Win32 geometry is projected relative to the native monitor origin and scale;
owned dialogs participate in overlap without receiving a separate taskbar icon.
Negative-origin and scaled geometry have unit coverage. Actual mixed-DPI and
multiple-monitor acceptance remains pending.

```sh
python tools/windows_lab.py --share /path/to/shared/artifacts --timeout 600 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows_desktop_smoke.py' \
  --output '\\host.lan\Data\lab\windows-desktop'
```

The desktop tool owns a blank Notepad fixture, uses isolated dock settings, and
stops before running if an existing dock or Notepad window is open. Its pixel
comparisons target the documented single-display baseline. It records actual
screenshots, native work areas and independent foreground-window observations.

## Native window previews

Windows previews use `Windows.Graphics.Capture` with an HWND capture item and
a free-threaded frame pool. One worker owns the WinRT apartment, Direct3D11
device, capture sessions, GPU textures and mapped readback memory. GTK receives
copied RGBA pixels through its main loop; hovering never calls another
application's paint handler or waits for capture on the UI thread.

The service keeps at most four native streams, expires unused streams after
1.5 seconds, and bounds the image cache to 32 MiB and 64 entries. Window IDs are
paired with process IDs so reused handles and late results cannot inherit
another process's preview. Resizing recreates the frame pool after releasing
the previous frame, including transitions where content outgrows an old buffer.
A minimized window retains its last captured image. Menu rows and Search panels
refresh asynchronously, as the hover popup already does.

`windows_capture_smoke.py` verifies known colors from an animated native GTK
window while a magenta window covers it on the desktop. Captures retained all
four source colors and an updating frame counter. It also verified resizing
from 642×392 to 806×473 and rejection of a closed source. The actual desktop,
captured source and resized image were inspected.

`windows_preview_smoke.py` runs the complete application with Notepad and Search
pinned. Native input verified that a real hover thumbnail replaced the icon
fallback, kept foreground focus in Notepad, retained the image while minimized,
and restored and activated Notepad on click. Native hit testing also verifies
the hollow Search icon and click-through animation space. The Search palette
opened from its dock applet, found the selected Notepad window, displayed its
280×153 live preview, and retained typing focus. Its captured menu thumbnail
and Search panel were inspected. The renderer applies
one alpha byte within the dock's input rectangle for Win32 layered-window hit
testing; other platforms retain their existing rendering.

![Native Windows hover preview](../images/windows-hover-previews.png)

![Native window preview in Docking Search](../images/windows-search-previews.png)

The preview/UI regression selection passed 126 tests under native Windows
Python, with 11 platform-specific cases skipped. All six visibility modes and
the existing Notepad launch/minimize/restore/Close desktop checks passed again
with preview capture and the input-alpha rendering enabled. The baseline
remains a single 1280×800 display at 96 DPI.

Windows draws its capture indicator around actively captured windows. Sessions
expire when no preview requests them. Indicator-free previews, a first capture
of an already minimized window, HDR/protected content, window outlines and
broader application coverage remain acceptance work. Multiple monitors and DPI
changes still require a real Windows display test.

```sh
python tools/windows_lab.py --share /path/to/shared/artifacts --timeout 150 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows_capture_smoke.py' \
  --output '\\host.lan\Data\lab\capture'
python tools/windows_lab.py --share /path/to/shared/artifacts --timeout 240 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows_preview_smoke.py' \
  --output '\\host.lan\Data\lab\previews'
```

API references: [window capture interop](https://learn.microsoft.com/en-us/windows/win32/api/windows.graphics.capture.interop/nf-windows-graphics-capture-interop-igraphicscaptureiteminterop-createforwindow),
[frame capture and resizing](https://learn.microsoft.com/en-us/windows/apps/develop/media-authoring-processing/screen-capture),
[Microsoft WinRT interface definitions](https://github.com/microsoft/windows-rs/blob/master/crates/libs/windows/src/Windows/Graphics/Capture/mod.rs),
[Direct3D11 SDK interfaces](https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/um/d3d11.h).

## Implementation and acceptance ledger

| Area | Implementation | Required Windows evidence |
| --- | --- | --- |
| Runtime/bootstrap | Windows signals, paths, DLL bootstrap and backend selection | Native source launch and focused Windows tests passed |
| Application metadata | Gio Win32 discovery, packaged activation and background shell icons | Packaged Notepad discovery, pinning, icon and foreground launch verified; broader Win32 inventory pending |
| Basic dock | Shared GTK/Cairo UI with Windows surface adapter | Startup, transparency, hover, mouse menu, Preferences and Quit verified on the baseline desktop; keyboard menu follow-up pending |
| Running applications | Native snapshots, matching and taskbar actions | Indicators, activate, minimize, restore and Close verified with Notepad; grouping/cycling have unit coverage and need multi-window desktop checks |
| Desktop integration | Native AppBar and overlap services | All four edges, taskbar coexistence, maximized-window placement and work-area restoration, six visibility modes and focus-preserving edge reveal verified on the baseline; fullscreen/restart/pressure follow-up pending |
| Displays | Pending | Multiple monitors, negative origins, DPI changes and display removal |
| Previews and overlays | Native Windows graphics capture and asynchronous UI | Occluded/live/resized source pixels and Notepad hover/focus/minimize/activation verified; menu/Search previews and typing focus verified; indicator, outlines and broader display/content cases pending |
| Search and file workflows | Pending | Applications, windows, files, folders, drag/drop and global shortcut |
| System applets | Pending | Native Windows backends for each applicable applet |
| Portable applets | Pending | Exercise the full applet catalog on Windows |
| Packaging and updates | Pending | Install, launch, update and uninstall on a clean Windows VM |
| Existing platforms | Initial regression tests pass | Keep Linux/BSD tests and desktop behavior working |

The first milestone is a visibly working basic dock. Native services and applet
backends follow that proof. Each feature must have its own Windows evidence
before its capability is advertised or its status is marked complete.
