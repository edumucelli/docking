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

## Windows CI

The [Windows desktop workflow](../.github/workflows/windows.yml) runs on pull
requests and master through the main CI workflow, and can also run manually or
on its weekly schedule. It uses native MSYS2 UCRT64 Python and GTK on GitHub's
`windows-2025` x64 runner, with Windows backend/applet contracts, shared core
checks and real GTK preview allocation tests. The release job requires it to pass.

The job builds a wheel, installs it into an isolated directory, then starts that
installed application outside the checkout. The native desktop smoke checks
rendering, menus, Preferences, resize and normal shutdown. Its import-origin
assertion prevents a source checkout from masking missing wheel contents.

The native multiwindow check pins an unlisted compiled program with two
independent windows in one process. It verifies grouping, repeated foreground
cycling and closing one window, then restarts Docking and launches the restored
pin. Its reports and captures are uploaded with the other desktop evidence.

It also builds and extracts a standalone ZIP, then builds an NSIS per-user
installer from that verified artifact. Installer acceptance checks Unicode paths,
all installed-file hashes, Start menu/Installed Apps registration, active-hook
rejection, upgrades, startup-choice preservation/disabling, the installed desktop
and uninstall with preferences and unknown files retained. The stable launcher
selects a versioned runtime; usage hooks retain a stable command across upgrades.
See [Windows packaging](../packaging/windows/README.md) for build and check commands.

JUnit reports, runtime versions, desktop screenshots, logs and packages are
uploaded in the `windows-ucrt64-x64` Actions artifact even on failure. Generated
captures are stored under `artifacts/windows/` rather than committed to `images/`.
Existing project showcase images remain versioned.

The hosted runner uses Windows Server 2025. Windows 11 Pro 25H2 remains the
product's native desktop acceptance target; the dedicated lab covers its shell,
packaged applications, input, device/session and compositor behavior.

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
  mingw-w64-ucrt-x86_64-python-psutil mingw-w64-ucrt-x86_64-python-numpy \
  mingw-w64-ucrt-x86_64-python-pillow mingw-w64-ucrt-x86_64-gstreamer \
  mingw-w64-ucrt-x86_64-gst-plugins-base \
  mingw-w64-ucrt-x86_64-gst-plugins-good mingw-w64-ucrt-x86_64-gst-plugins-bad
python -m pip install --break-system-packages 'openmeteo-requests>=1.2,<1.5' requests-cache retry-requests
python -c "import gi; gi.require_version('Gst', '1.0'); from gi.repository import Gst; Gst.init(None)"
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

## Backend boundaries

Shared UI and state use backend contracts with platform implementations.
For Network, `applets/network/backend.py` defines `NetworkBackend` and its
settings actions; `linux.py`, `bsd.py` and `windows.py` implement that contract.
The applet uses the contract for state, traffic, adapter choices and settings.
Unix desktop command
discovery is shared by Linux and BSD in `desktop_settings.py`; IP Helper bindings
and adapter selection belong to the Windows implementation.
`windows_connections.py` implements the shared `ConnectionManager`, delegating
WLAN calls to `windows_wifi.py` and VPN actions to `windows_vpn.py`/`windows_ras.py`.

Window tracking follows the common `WindowService` contract in
`platform/backends/base.py`. Win32 enumeration and actions are implemented in
`platform/backends/windows/windows.py`. Platform-neutral snapshot projection and
application matching live in `window_projection.py`, used by KWin, Sway and
Windows. `windows/ffi.py` contains only small Win32 ABI
primitives needed by several Windows implementations.

Battery follows `BatteryBackend` for aggregate readings, power, peripherals and
Power Settings. The shared UI keeps one backend instance and passes no sysfs
paths to it. Linux owns its device/path configuration; Linux and BSD use the
UPower implementation for accessories. Windows uses its power APIs and native
Settings action and published Windows device-container battery properties.
Shared battery state and tooltip formatting contain no OS access.

Volume's `VolumeBackend` supplies optional audio controls and settings actions.
Linux, BSD and Windows construct the same contract, so Sound Settings remain
available when no mixer tool is installed. CLI parsing belongs to the ALSA or
PulseAudio implementation; native Core Audio and the Windows Settings action
belong to `volume/windows.py`.

Desk Presence uses the common `IdleService` supplied by the session backend.
X11 and Windows own their respective input-time queries; the applet only reads
idle seconds and updates its shared presence state.

Caps Lock follows `LockKeyBackend` for Caps Lock and Num Lock indicators.
`capslock/linux.py` owns `xset` discovery, execution and parsing;
`capslock/windows.py` owns the Win32 toggle query. Shared state and rendering
contain no operating-system calls, and the applet retains one backend for its
normal poll and Refresh Now action.

Color Picker consumes `ScreenCaptureService`. X11 owns root-window sampling;
Windows owns screen device contexts and pixel reads in `windows/screen_capture.py`.
`windows/geometry.py` shares the monitor mapping used by AppBar placement and
color sampling. Capture-overlay focus and native close handling belong to the
surface backend; applets pass their ordinary popup anchor to the shared helper.

Window Killer delegates selection and termination through `WindowPickService`.
X11, Niri and Wayfire share the POSIX termination primitive in
`backends/unix/processes.py`; process signaling belongs to these implementations.
Windows implements the same interface in `windows/picking.py`, using native hit
testing and validated process handles. The shared applet and capture overlay
contain no Windows process operations.

Desktop uses the common `DesktopActionService`. The Windows implementation owns
Explorer's shell action and window-order observations in `windows/desktop.py`;
the shared applet performs the same service call on every platform.

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
python tools/windows/visual/lab.py --share /path/to/shared/artifacts configure-share
python tools/windows/visual/lab.py --share /path/to/shared/artifacts ready
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 5 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\smoke.py' \
  --output '\\host.lan\Data\lab\smoke'
```

Jobs receive unique IDs and durable response files under `lab/jobs`. If a host
command times out, inspect the response and heartbeat before submitting another
job. Do not launch a second copy merely because observation timed out.
An agent restart does not replay a claimed job whose outcome is unknown.

The readiness report includes `security_context`: UAC policy, effective
administrator membership, token elevation/type and integrity RID. Check these
before desktop acceptance: an elevated session with UAC disabled does not
establish behavior for an ordinary medium-integrity user. The report contains
no account identifiers. After changing UAC policy and rebooting the test VM,
verify the new agent's startup time and token before submitting further jobs.

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

System Tray now selects a native Windows backend and no longer imports
XEmbed/GdkX11 on Windows. Its shared GTK presentation, service and menu models
use the same contracts as the Linux implementation.
Importing a class does not verify its controls or native system backend; the
catalog acceptance work remains open.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\window_smoke.py' \
  --output '\\host.lan\Data\lab\windows-native'
```

The smoke tool's mouse row coordinates assume that baseline theme and DPI.
The initial checks exposed keyboard navigation and native WM_CLOSE failures
while a menu grab was active. The scoped menu integration described below
addresses those failures; the initial mouse-only evidence did not cover them.

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
stacking is covered by the transition/startup check below. Restart races and
broader application/dialog behavior still need desktop acceptance.

Window overlap is evaluated in the dock monitor's GTK coordinate space. Raw
Win32 geometry is projected relative to the native monitor origin and scale;
owned dialogs participate in overlap without receiving a separate taskbar icon.
Negative-origin and scaled geometry have unit coverage. Actual mixed-DPI and
multiple-monitor acceptance remains pending.

Windows startup selects
[per-monitor-v2 DPI awareness](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setprocessdpiawarenesscontext)
before GTK initializes.
GTK's default system awareness previously left the dock over Explorer's taskbar
and reserved too much space after changing Windows display scaling. On the
1280×800 Windows 11 desktop, a real 100% → 125% change now reports 120 DPI and
keeps the 53-pixel dock reservation above the 60-pixel taskbar. Native Search
retains keyboard focus and fits the updated work area. Color Picker also samples
six actual desktop colors correctly at 125%, with clipboard/prefs, Escape,
native menus and stable GDI resources across 300 repeated reads. The wheel and
standalone desktop checks also verify the actual dock window's per-monitor-v2 awareness;
an existing host or manifest DPI setting remains in force.

With screen reservation enabled, the dock lowers its native window when a
fullscreen application opens, then restores its keep-above setting without
taking focus. Clearing the topmost flag alone left it over the fullscreen
window in the normal stacking band. This follows the
[AppBar fullscreen contract](https://learn.microsoft.com/en-us/windows/win32/shell/abn-fullscreenapp).
An initial foreground/monitor check also covers starting Docking while another
application is already fullscreen. A 250 ms native geometry observer runs only
while fullscreen is active and stops on exit or surface shutdown; it handles
the missing closing notification in that startup case. After exit, a separate
short observer confirms the actual Win32 topmost flag and repairs a stale GTK
hint without activating the dock. It stops once native stacking is confirmed;
re-entering fullscreen or removing the reservation/surface cancels it.

```sh
python -m tools.windows.visual.fullscreen_smoke --output artifacts/windows/fullscreen
```

The check owns a separate GTK fixture, enters/exits fullscreen with actual F11
input, and runs both startup orders in fresh processes. Two cycles per startup order
verify foreground ownership, native stacking flags, exact fixture pixels over
the clock's actual screen rectangle, restored dock visibility and normal
fixture/dock shutdown with the original work area. Direct Alt+F4 closure also
covers an application already fullscreen before Docking starts, and checks that
the exit observer stops. The Windows workflow runs all scenarios and uploads
their captures and observations. Native acceptance
currently covers one Windows 11 display at 96 DPI; other display arrangements,
exclusive rendering and broader application/modal behavior remain.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --timeout 600 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\desktop_smoke.py' \
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

The service keeps at most four native streams and expires unused streams after
1.5 seconds. Captured sources and fitted thumbnails share a 32 MiB budget, with
at most 64 sources and 64 fitted images. Sources retain the supported preview
resolution of at most 2048 pixels per axis; larger thumbnail requests do not
enlarge an earlier small thumbnail. Different sizes share one pending capture.
Window IDs are
paired with process IDs and application IDs so reused handles and late results
cannot inherit another application's preview. Resizing recreates the frame pool after releasing
the previous frame, including transitions where content outgrows an old buffer.
A minimized window retains its last captured image. Menu rows and Search panels
refresh asynchronously, as the hover popup already does.

A static or minimized window can stop supplying frames. Its retained source
still serves new thumbnail sizes immediately, with the original capture time.
New native pixels invalidate every fitted size for that source. Memory eviction
prefers discarding fitted copies before the retained source; process,
application-ID and ghost-surface changes discard both. Allocation failures keep
the previous valid source and remain subject to the same memory budget.

`tools/windows/visual/capture_smoke.py` verifies known colors from an animated native GTK
window while a magenta window covers it on the desktop. Captures retained all
four source colors and an updating frame counter. It also verified resizing
from 642×392 to 806×473 and rejection of a closed source. The actual desktop,
captured source and resized image were inspected.

`tools/windows/visual/preview_smoke.py` runs the complete application with Notepad and Search
pinned. Native input verified that a real hover thumbnail replaced the icon
fallback, kept foreground focus in Notepad, retained the image while minimized,
and restored and activated Notepad on click. Native hit testing also verifies
the hollow Search icon and click-through animation space. The Search palette
opened from its dock applet, found the selected Notepad window, displayed its
280×153 live preview, and retained typing focus. Its captured menu thumbnail
and Search panel were inspected. The renderer applies
one alpha byte within the dock's input rectangle for Win32 layered-window hit
testing; other platforms retain their existing rendering.

The preview/UI regression selection passed 126 tests under native Windows
Python, with 11 platform-specific cases skipped. All six visibility modes and
the existing Notepad launch/minimize/restore/Close desktop checks passed again
with preview capture and the input-alpha rendering enabled. The baseline
remains a single 1280×800 display at 96 DPI.

`tools/windows/visual/preview_cache_smoke.py` runs the full application with an owned static
Win32 fixture. After a 320-pixel warm capture, it minimizes the window and waits
until the real graphics stream returns no more buffered frames. The baseline
then failed to supply any image at a new size, with seven empty reads. The
`windows-preview-cache-fixed` run served 190×114, 80×48 and full-resolution
586×353 images from that retained source, rejected capture after normal window
close, and restored the work area exactly. The stronger
`windows-preview-cache-ghost` run also passed the native hang/minimize/thumbnail
activation/physical move/Close/Wait/recovery sequence. Both recovered hover
widgets contained actual pixbufs at their requested 200×150 limits; the previous
Target icon fallback was replaced by its real image. Captures and the desktop
PNG were inspected. The owned ghost fixture uses Calculator's registered ID to
exercise common grouping; it is not a Calculator process.

Windows draws its capture indicator around actively captured windows. Sessions
expire when no preview requests them. Indicator-free previews, a first capture
of an already minimized window, HDR/protected content, window outlines and
broader application coverage remain acceptance work. Multiple monitors and DPI
changes still require a real Windows display test.

### Per-window application identity

Windows snapshots read `PKEY_AppUserModel_ID` from the window property store
before falling back to process identity. Packaged applications can share an
`ApplicationFrameHost` process while retaining distinct application IDs. The
shared matcher receives that application ID; the snapshot retains the native
frame-owner PID for window actions and graphics capture. COM apartments,
property stores and returned values have scoped ownership.

Actions recheck both native process ownership and application identity. Preview
cache entries and streams include the application ID, and the capture worker
revalidates it before and after GPU readback. An old frame cannot publish pixels
after the same HWND and PID have been assigned a different application ID.

`tools/windows/visual/windowidentity_smoke.py` drives the complete dock with native input.
The `windows-windowidentity-native-2` run launched Calculator and Clock from
their pins, observed two distinct application IDs with the same host PID,
activated/minimized/restored Calculator independently, captured distinct native
previews and closed both applications through the shared window service. Clock
displayed its installed update-required view. An owned Win32 fixture changed its
explicit application ID and visible button label without changing its HWND or
PID: the stale action returned `NOT_FOUND`, the old cached image was discarded,
and a new preview contained the updated label. All five PNGs were inspected;
normal shutdown restored the exact work area and preserved Explorer.

Arbitrary executable/shortcut identity, broader hosted applications and
multiple-window cycling remain acceptance work. Ghost-window projection is
covered below.

References: [per-window property stores](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shgetpropertystoreforwindow),
[application identity property](https://learn.microsoft.com/en-us/windows/win32/properties/props-system-appusermodel-id).

### Hung applications and ghost windows

Windows can replace an unresponsive application window with a DWM-owned ghost.
Polling can still see the original HWND, so treating the ghost as another
application produces duplicate entries. The Windows backend associates the
ghost with its original window, retains the original window ID, process and
application identity, and reads visible state, title and geometry from the
ghost. Enumeration deduplicates both representations in either Z order.

Actions resolve the current associated HWND immediately before dispatch. Ghost
minimize, restore and Close use native caption system commands;
`ShowWindowAsync` returned access denied on the DWM-owned window in this lab,
and `WM_CLOSE` did not open the unresponsive-application prompt. An already
minimized window returns success without another minimize request. Capture
streams and cached images include the current native HWND/PID as well as the
logical application identity. The worker validates that association before and
after readback, and publication rejects results from a retired surface.

`tools/windows/visual/ghost_smoke.py` runs the full dock with an owned two-window Win32
fixture. The fixture explicitly sets Calculator's registered application ID to
exercise the real common grouping path; it is not a Calculator process. The
`windows-ghost-native-12` run kept both original running IDs and the pre-minimized
sibling throughout a real hang, captured the DWM ghost, minimized it, restored
it by clicking its dock thumbnail, and tracked a virtual-mouse move from
`(100, 80)` to `(200, 120)`. Close opened the actual Windows prompt. Selecting
Wait hid that prompt and preserved the fixture; recovery retired the ghost,
rejected its old capture target, and resumed original-window capture. Both
windows then closed normally, Explorer was preserved, and the exact work area
was restored. Desktop, prompt and capture PNGs were inspected.

The ghost associations use optional named user32 exports outside the documented
Win32 contract, shared with Window Killer. Missing exports disable the relevant
association rather than guessing from window titles or process names. This
acceptance covers Windows 11 Pro 25H2 build 26200.8037, one 96-DPI display and
normal GTK scale. Other Windows versions, display/session transitions and
repeated ghost-recovery races remain acceptance work. The separate static-source
thumbnail-size fallback exposed by this run was fixed and verified in the
preview-cache acceptance above.

The movement and Wait selection use `tools/windows/visual/vm_pointer.py` on the host. It
negotiates QEMU's absolute virtual tablet over the loopback VNC endpoint; this
provides guest hardware input without changing Windows privileges. Each request
appears sequentially while the guest tool waits for its completion file:

```sh
python tools/windows/visual/lab.py --share /path/to/shared --wait 1 start \
  'C:\msys64\ucrt64\bin\python.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\ghost_smoke.py' \
  --output '\\host.lan\Data\lab\ghosts' --physical-input
python tools/windows/visual/vm_pointer.py \
  --request /path/to/shared/lab/ghosts/pointer-request.json
python tools/windows/visual/vm_pointer.py \
  --request /path/to/shared/lab/ghosts/close-pointer-request.json
```

References: [Windows ghosting behavior](https://learn.microsoft.com/en-us/windows/win32/winmsg/about-messages-and-message-queues),
[QEMU pointer-mode negotiation](https://github.com/qemu/qemu/blob/master/ui/vnc.c).

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --timeout 150 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\capture_smoke.py' \
  --output '\\host.lan\Data\lab\capture'
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --timeout 240 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\preview_smoke.py' \
  --output '\\host.lan\Data\lab\previews'
```

API references: [window capture interop](https://learn.microsoft.com/en-us/windows/win32/api/windows.graphics.capture.interop/nf-windows-graphics-capture-interop-igraphicscaptureiteminterop-createforwindow),
[frame capture and resizing](https://learn.microsoft.com/en-us/windows/apps/develop/media-authoring-processing/screen-capture),
[Microsoft WinRT interface definitions](https://github.com/microsoft/windows-rs/blob/master/crates/libs/windows/src/Windows/Graphics/Capture/mod.rs),
[Direct3D11 SDK interfaces](https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/um/d3d11.h).

## Files, folders and Search

Filesystem targets now recognize native drive paths before URL parsing treats
`C:` as a scheme. File URLs decode into native drive or UNC paths, retaining a
network server rather than opening a same-named local file. The shared decoder
also feeds file previews, image loading and drag/drop. POSIX authority and
filename-byte handling retain their existing behavior.

Search recognizes Windows drive, UNC, home and relative path syntax and routes
it to the Path provider. Copy Path returns a native filename with its drive or
server intact. Text previews normalize Windows line endings for display.

Gio's Win32 file-URL parser drops the authority from a standard UNC URL in the
lab runtime. The file adapter constructs Windows filesystem objects from their
decoded native paths. Metadata, folder browsing/monitoring and recorded icon
files use that boundary. Image thumbnails also convert Gio's Windows extension
content types (such as `.png`) to MIME types before selecting the image loader.
Default-handler opening uses the native Windows shell association; the lab's
Gio URI-launch path opened a blank Notepad instead of the requested document.
The Windows shell boundary normalizes absolute filename separators because
MSYS2 can emit forward UNC paths that ShellExecute rejects. URI and shell
namespace targets retain their original spelling.
Ordinary Windows documents no longer trigger Unix executable-launcher creation.

`tools/windows/visual/file_smoke.py` creates isolated drive and SMB-share fixtures with
spaces, accents, `#`, `+` and `%` in their filenames. It checks canonical targets,
Search results and copy actions, real image thumbnails, directory enumeration
and text previews. Desktop stages open documents through their native default
handler, read back the actual Notepad text, open Explorer, inspect a pinned
folder stack and a Search preview, copy a path through Search, and drag a real
Explorer item onto the dock. The drop must persist in the dock configuration.
The tool records PNGs and closes only its own fixtures. Its desktop input
assumes the documented baseline, with no other dock, editor or Explorer window
open.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --timeout 240 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\file_smoke.py' \
  --output '\\host.lan\Data\lab\files'
```

The `windows-file-geometry-verified` desktop run passed every stage and exited
normally. Independent native window observations and clipboard readback confirm
that both default-opened documents contained their expected text, and Search
copied the exact Unicode drive path. The Explorer drop persisted the canonical
file URI. The folder, Search and drop PNGs were visually inspected. Directory
metadata and previews also passed against the actual `host.lan` SMB share.
The file, folder, icon, menu and Search regression selection passed 286 tests
under native Windows Python, with 18 platform-specific cases skipped. Native
folder checks use the Windows hidden attribute as well as the visible-file
policy.

Native application identity/grouping for arbitrary dropped executables and
shortcuts remains separate acceptance work. Network disconnect/reconnect
behavior and broader folder-monitor scenarios
also remain pending.

References: [Python file-URL conversion](https://docs.python.org/3.14/library/urllib.request.html#urllib.request.url2pathname),
[Gio native file paths](https://docs.gtk.org/gio/type_func.File.new_for_path.html),
[Gio platform content types](https://docs.gtk.org/gio/content-types.html),
[Windows shell file associations](https://docs.python.org/3.14/library/os.html#os.startfile).

## Native Search shortcut

Windows uses `RegisterHotKey` for the configured Search shortcut, starting with
`Ctrl+Alt+Space`. A dedicated thread owns registration, native messages and
release. The thread posts activation and status callbacks to GTK; stopping or
replacing the service discards older generations and cancels their pending
callbacks. Repeated activation notifications are bounded while GTK is busy.
Windows' `MOD_NOREPEAT` flag suppresses held-key repeat.

The existing Preferences capture lifecycle suspends the registration before
capturing a new key, restores it on cancellation, and rebinds after a change.
A competing registration reports the existing **Conflict** status, with no
binding advertised. Disabling Search and normal Quit release the owned hotkey.
The Windows path does not attempt the XDG portal or X11 fallback.

Captured GDK key names map to native virtual keys or characters translated in
the foreground keyboard layout. Character bindings follow live layout changes;
unchanged translations retain their registration. A changed mapping releases
the old key and retires its queued messages before registering the replacement.
An unavailable character or competing owner advertises no binding, and the
same worker retries until the layout or ownership permits registration again.
Stopping cancels these retries. F12 is reserved by Windows for debuggers and
is rejected. The XDG `NUM` modifier has no Win32 registration equivalent.
The keyboard backend retains a bounded history of verified typing windows so
a hidden Search window cannot displace the preceding visible source during a
dock-menu foreground gap. A new foreground application still invalidates an
older menu request.

`python -m tools.windows.visual.keyboardlayout_smoke --search-input --output artifacts/windows/search-layouts`
checks physical scan-code typing in Notepad and Search under US, French and
Dvorak, including French dead-key composition. It switches a semicolon shortcut
between US and French while already bound, verifies unavailable-character and
conflict recovery without changing preferences, and quits during an unavailable
layout. It restores the original loaded layouts, input preferences and work
area, then independently reacquires every tested shortcut. Windows CI runs this
mode and uploads the reports and captures. Other layouts, broader dead keys,
AltGr/IME and per-app input policies, remote sessions and Windows-reserved
combinations still need acceptance.

`tools/windows/visual/shortcut_smoke.py` runs the complete application composition and GTK
main loop with isolated settings. Its driver sends native `SendInput` events
and independently observes HWNDs and foreground focus. GTK state observations
and Preferences actions are marshalled to the application's main loop.
The `windows-shortcuts-visual-verified` run verified activation from Notepad,
Search typing focus, toggle-to-hide and return to Notepad, held-key suppression,
and activation with Caps Lock and Num Lock toggled. It captured and persisted
`Ctrl+Alt+K`, released the previous binding, created a real competing registration
and showed Conflict in Preferences, recovered after that owner released the
key, completed five capture/cancel cycles with one worker, and passed disable/
reenable. After normal application shutdown an independent registration could
acquire both old and new combinations again. The Search and Preferences PNGs
were inspected. Sixty-one focused shortcut/controller checks also passed under
native Windows Python.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --timeout 240 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\shortcut_smoke.py' \
  --output '\\host.lan\Data\lab\shortcuts'
```

References: [RegisterHotKey](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey),
[native hotkey messages](https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-hotkey),
[keyboard-layout translation](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-vkkeyscanexw),
[UnregisterHotKey](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-unregisterhotkey).

## Search artwork

Search resolves application and window icons through canonical application
metadata and the shared icon loader, including its background Windows shell
extraction. Pinned items borrow their current dock artwork, preserving painted
applets, custom icons and image aspect ratios. Providers continue to publish
immutable descriptors without GTK objects.

Native completion, model changes and applet presentation notifications update
the images of current visible rows. They do not rebuild rows, rerun providers,
change selection or disturb entry focus. Destroying Search unsubscribes all
three sources; late notifications cannot touch destroyed widgets. Canonical
icon file URIs use the existing platform path decoder, including Unicode and
escaped literal characters. Windows paths do not use Flatpak's host-root map.

`tools/windows/visual/search_icon_smoke.py` runs the full application with isolated settings.
It briefly holds one real shell extraction until Search displays its fallback,
then releases the extraction and verifies the published native pixels in the
same row. It checks the unchanged query, selection, row generation and typing
focus, launches Notepad with Enter, verifies artwork for its application and
live-window results, and compares the pinned Clock artwork with the dock's
painted icon. The `windows-search-icons-desktop` run passed all stages and
normal shutdown. The Notepad, live-window and Clock PNGs were inspected.
The focused Search/icon selection passed 87 native Windows tests, with two
platform-specific cases skipped.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --timeout 240 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\search_icon_smoke.py' \
  --output '\\host.lan\Data\lab\search-icons'
```

The baseline verifies activation from the focused desktop background and
Notepad. The Start-menu focus failure found during these checks is addressed
by the shell handoff integration below. Some internal system applications expose
flat shell tiles rather than useful artwork; their visibility and artwork belong
to the broader application-inventory acceptance.

## Search activation during shell handoff

Native tracing reproduced the startup failure: GTK had focused Search while
Windows still considered the Start menu foreground, and the shell then returned
foreground ownership to the taskbar. Search now observes that pending handoff
from verified Explorer/Start/Search processes and completes the original
activation on GTK's idle loop. The target must belong to Docking's process and
match the requested Search window. The observer stays active for its original
750 ms limit after the first foreground success: native tracing also found a
late return to Windows Search about 80 ms after Docking acquired focus.
Restoration requires unchanged session input, checked with `GetLastInputInfo`.
New input, an unrelated application taking focus, hiding/destroying Search or
expiry cancels the request. A later shell return cannot override an outside
click during this interval. No foreign input queues are attached.

`tools/windows/visual/focus_smoke.py` runs the complete application and activates Search as
soon as the native shortcut reports Bound. It records foreground HWNDs, native
GUI-thread focus, GTK map/focus events and typing. Earlier Start, taskbar and
desktop runs injected input from Docking's own process and passed, including
outside-click focus, normal shutdown, work-area restoration and independent
hotkey reacquisition. The same instrumented Start-menu case failed before the
handoff integration and passed afterward.

A subsequent regular `run.py` launch exposed a remaining failure when a
separate process sent Start, Escape and the shortcut: Search mapped and had
GTK-thread focus, while Explorer retained native foreground ownership. QEMU's
virtual keyboard reproduced the failure. Same-process input can therefore
overstate foreground acceptance. The smoke tool now uses a separate injector
by default; `--same-process-input` retains the earlier diagnostic comparison.
Regular shell activation remains open, along with broader keyboard layouts,
remote sessions and display configurations. No manual click is counted as a
successful hotkey activation.

With the independent injector, the desktop and settled-taskbar cases pass
foreground ownership, typing, outside-click focus and normal-shutdown checks.
The immediate Start-close case fails. `--delay` controls the interval between
dismissing Start and pressing the shortcut, allowing that transition to be
investigated separately from normal desktop activation.

The trace also records the raw `SetForegroundWindow` return value, call duration,
target identity and foreground/GUI-thread state before and after each native
Search activation attempt. In the ordinary medium-integrity UAC session, the
observer found the correct Search window and Explorer handoff, but the API
returned false. Bounded retries, an explicit foreground grant on the hotkey
receiver and an earlier native activation attempt did not fix this case.
An attached-input-queue diagnostic acquired focus and passed typing and
outside-click checks. A production solution must keep GTK independent of a
foreign input queue: attaching queues can make activation block on that other
thread. See [Microsoft's explanation of attached input queues](https://devblogs.microsoft.com/oldnewthing/20130607-00/?p=4143).

The outside-click check uses native hit testing to confirm that the click lands
outside Search. `--outside-point X Y` selects a desktop point or a visible
foreign window. Search now fits to the pointer monitor's usable desktop with a
16-logical-pixel margin. The shared UI asks the surface backend for the current
work area, including the dock's reservation; Windows reads the live native
mapping because GTK can retain an older work area after AppBar registration.
Reopening on a larger work area restores the preferred size, and an explicit
user resize is retained. Long action lists scroll below their filter, and image,
metadata and text previews scroll together inside the palette.

On the 1280 × 800 lab at GTK scale 2, native Search bounds were
`(32, 32)–(1248, 614)` inside the dock-reserved work area
`(0, 0)–(1280, 646)`. Independent shortcut input, native foreground ownership,
typing, Preview/Actions keyboard controls, outside-click focus and normal
shutdown passed. An owned generated image and twelve-action layout fixture also
passed real native wheel scrolling in both panels; this checks UI layout rather
than provider actions. The native captures were visually inspected.
At GTK scale 1 the palette retained its 680 × 470 preferred size and passed the
same native focus, keyboard-panel, outside-click and shutdown checks.

`--check-workarea` requires a margin inside the native primary work area;
`--check-panels` opens Preview and Actions, checks their bounds and dismisses them
with Escape. `--check-scroll` additionally requires overflowing fixtures and
observes actual adjustment changes after native wheel input. These geometry
checks currently target the lab's single primary display; mixed-DPI and physical
multi-monitor acceptance remains open.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --timeout 180 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\focus_smoke.py' \
  --output '\\host.lan\Data\lab\focus-start' --origin start
```

Use `--origin taskbar` and `--origin desktop` for the other startup cases.

For real QEMU keyboard input, start the guest probe with `--external-keyboard`,
then run its host partner against the same shared output directory:

```sh
python -m tools.windows.visual.lab --share /path/to/shared/artifacts --wait 0 start \
  'C:\msys64\ucrt64\bin\python.exe' -m tools.windows.visual.focus_smoke \
  --output '\\host.lan\Data\lab\focus-qemu' --origin start \
  --external-keyboard --check-workarea --check-panels
python -m tools.windows.visual.vm_input \
  --focus-handshake /path/to/shared/artifacts/lab/focus-qemu
```

The probe and host exchange fresh tokens for origin and activation. The host
acknowledges only after sending the corresponding QEMU key sequence, and cannot
replay an already acknowledged request. Start activation uses a 20 ms Escape
press, a 40 ms interval before the shortcut, plus optional `--delay` (0–10 s).
Results identify this as `external-keyboard` and retain both sequences; failure
captures include the visible desktop. Synthetic input remains available as a
separate diagnostic, and an isolated hardware success does not close the
remaining regular-launch, shell/session and keyboard acceptance work.

On the medium-integrity Windows 11 Pro 25H2 lab, hardware activation at GTK
scales 1 and 2 passed native focus, typing, panel controls, outside-click and
shutdown checks. A controlled native Explorer foreground return after the first
Search success also exercised restoration: input stayed unchanged, the native
foreground call succeeded and the observer expired at its original deadline.
This explicit handoff fixture complements the naturally reproduced race; it
does not replace regular-launch or broader session acceptance.

The uninstrumented regular launch with the original eleven-pin configuration
also passed QEMU Start/Escape/shortcut activation and fifteen native foreground
samples while typing. In a second activation, an independent pointer helper
clicked the desktop shortly after Search acquired focus (the click helper
returned after 651 ms). The session input tick changed, and eight subsequent
samples stayed with Explorer. Broader shell timing, first-acquisition failures
and remote-session acceptance remain open.

References: [foreground activation](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow),
[GUI-thread observations](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getguithreadinfo),
[last input information](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo).

## AI Usage applet and headless hooks

AI Usage now imports and runs on native Windows. Its hook writers use a
path-derived Windows named mutex; POSIX keeps its existing `flock` contract.
This serializes concurrent writers without Windows byte-range locks blocking
ordinary preference readers. Reads and writes explicitly use UTF-8. Four real
concurrent hook subprocesses repeatedly replaced their own sessions and retained
every final session and unrelated Unicode preference.

Windows hook registration uses a headless Python runner with native arguments.
A dock launched by `pythonw.exe` selects the adjacent `python.exe` for hooks
that consume stdin. Claude's documented exec form uses `command` plus `args`;
Codex receives a native notify argv encoded as valid TOML, including backslashes,
spaces and bracket characters. Registration is idempotent and preserves
unrelated Claude hooks when replacing old Docking commands.

`tools/windows/visual/aiusage_smoke.py` runs the full application with AI Usage pinned and
isolated provider settings. The applet registers its real Windows commands;
the generated Claude hook imports a Unicode-path transcript fixture through
stdin while Docking runs, preserving foreground ownership. Its normal poll
method is then invoked without waiting for the 60-second interval. The check verifies 1,500 tokens,
repainted artwork, native wheel selection of Claude, tooltip and context menu,
and normal shutdown. The tooltip and menu PNGs were inspected. The combined
AI Usage, Search, focus and shortcut selection passed 208 native Windows tests
with one POSIX-only filename case skipped, and 209 tests on Linux. Live provider
CLI installations and real sessions remain broader integration acceptance.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --timeout 180 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\aiusage_smoke.py' \
  --output '\\host.lan\Data\lab\aiusage'
```

References: [Windows mutex ownership](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw),
[kernel object namespaces](https://learn.microsoft.com/en-us/windows/win32/termserv/kernel-object-namespaces),
[Claude command hook fields](https://code.claude.com/docs/en/hooks#command-hook-fields).

## Ambient playback and audio capture

The lab now installs GStreamer and its base, good and bad plugin packages,
including the native WASAPI output. `AUDIO=Y` exposes an emulated sound device
and the browser audio stream. Enable Audio under Settings → Advanced in the
web viewer to listen. The verified console-session baseline has one Windows
output device; physical devices, Bluetooth, RDP redirection and device changes
remain broader acceptance.

Ambient uses `Gst.filename_to_uri()` for sound files. On this Windows runtime,
`Path.as_uri()` produced a UNC URI that GStreamer's GIO source opened without
the server name. The GStreamer conversion preserves the share and reaches
PLAYING. A native URI-handler test also checks a UNC filename containing
Unicode, spaces, percent, hash and plus characters. Playback that fails its
initial state change now releases the pipeline and bus watch and stays stopped.

Native validation also found that White Noise selected GStreamer's sine mode.
The shared mapping now selects white noise; both noise modes are checked
against GStreamer's real named enum on Linux and Windows.

The first complete plugin-registry scan measured about 584 seconds on the VM;
a cached `Gst.init()` measured about 65 ms. Bootstrap prepares the full registry
during setup. The isolated desktop smoke copies this installed dependency
metadata into its own cache, while keeping app preferences and runtime files
separate. This verifies playback after setup; it does not measure cold scanning
as application startup.

`tools/windows/visual/ambient_smoke.py` runs the normal dock and uses native clicks, menu
navigation and wheel events. `tools/windows/visual/vm_audio.py` records the actual QEMU audio
relay as 48 kHz stereo PCM and WAV files. The successful run verified all seven
bundled OGG sounds through `wasapi2sink`, white and pink noise, volume changes,
muting, and stop. Muted output remained at the 16-bit quantization floor; after
stop the relay supplied no frames. A separate short OGG fixture under a Unicode
share path completed four real end-of-stream repeats while audio continued.
Normal shutdown restored the work area and released the hotkey. Playing and
menu captures were inspected. Ambient's focused suite passed 45 native Windows
tests and 44 Linux tests, with the Windows-only UNC case skipped on Linux.

Use a fresh output directory for each run. Start the guest job, then service its
audio requests from the host:

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\ambient_smoke.py' \
  --output '\\host.lan\Data\lab\ambient-acceptance'
python tools/windows/visual/vm_audio.py --share /path/to/shared/artifacts \
  --name ambient-acceptance --timeout 300
```

References: [GStreamer filename conversion](https://gstreamer.freedesktop.org/documentation/gstreamer/gsturihandler.html#gst_filename_to_uri),
[noise modes](https://gstreamer.freedesktop.org/documentation/audiotestsrc/index.html),
[native WASAPI output](https://gstreamer.freedesktop.org/documentation/wasapi2/wasapi2sink.html),
[Dockur audio](https://github.com/dockur/windows#how-do-i-enable-audio).

## Native master volume

The Volume applet uses the public Core Audio `IAudioEndpointVolume` interface
on Windows. It controls the default multimedia output device, including master
level and mute, and opens the native Sound page with `ms-settings:sound`.
The existing one-second background poll follows changes from other controls.
Each operation resolves the current default endpoint and releases its COM
interfaces on the calling thread. A missing device leaves polling available
for recovery instead of permanently disabling the backend.

`tools/windows/visual/volume_smoke.py` runs the complete dock with Ambient as a real audio
source. Native wheel input checks five-point changes and both limits; native
clicks toggle mute. With Explorer foreground, Windows' own volume and mute
keys change the endpoint and the applet follows through its ordinary poll.
The host audio capture verifies sound at 50% and 100%, silence at zero and
under both external and applet mute, and sound after unmuting. The endpoint's
scalar uses a driver-dependent audio curve, so the measurement checks a real
increase rather than assuming that 50% means half the amplitude.

The desktop check also enables Show Level through the menu, verifies its saved
preference, and opens the Windows Sound settings page from that menu. It
restores the original endpoint scalar and mute state, then quits the dock
normally and checks work-area restoration and hotkey release. Menu, tooltip,
level and Settings captures accompany the result. The focused suite passed
70 tests on native Windows and 69 on Linux, with the native apartment read
check skipped on Linux. Fault-injection tests cover missing/default-device
changes and cleanup after partial COM acquisition. Physical devices, role
differences, Bluetooth and RDP routing still need desktop acceptance.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\volume_smoke.py' \
  --output '\\host.lan\Data\lab\volume-acceptance'
python tools/windows/visual/vm_audio.py --share /path/to/shared/artifacts \
  --name volume-acceptance --timeout 240
```

References: [default audio endpoint](https://learn.microsoft.com/en-us/windows/win32/api/mmdeviceapi/nf-mmdeviceapi-immdeviceenumerator-getdefaultaudioendpoint),
[master volume scalar](https://learn.microsoft.com/en-us/windows/win32/api/endpointvolume/nf-endpointvolume-iaudioendpointvolume-setmastervolumelevelscalar),
[endpoint mute](https://learn.microsoft.com/en-us/windows/win32/api/endpointvolume/nf-endpointvolume-iaudioendpointvolume-setmute),
[Windows Settings URIs](https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-settings).

## Native Battery and Power Settings

The Battery applet now selects a native Windows backend for its normal system
readings. `GetSystemPowerStatus` supplies the aggregate percentage, charge flag
and time remaining. `CallNtPowerInformation(SystemBatteryState)` supplies the
instantaneous rate, interpreted as a signed 32-bit value in milliwatts and
displayed as a magnitude in watts. The frequent power poll does not spawn a
subprocess. Only DLL bindings are cached; every poll reads current data.

AC power alone does not imply charging. A plugged-in battery can be idle,
and an unknown percentage remains distinct from zero and from no battery.
Unknown readings show a question mark and an unknown tooltip. Unknown time
and rate sentinels are omitted; the discharge-time estimate is not reused as
a time-to-full estimate. Power Settings opens `ms-settings:powersleep` through
the native shell association.

Accessory readings use WinRT `DeviceInformation` to query installed device
containers and their published `System.Devices.BatteryLife` and
`System.Devices.ChargingState` properties. Only connected, external containers
with a known percentage are shown; the computer's own container is excluded.
Container IDs deduplicate interfaces, and readings are sorted by percentage.
Unknown Byte values above 100 are omitted, zero remains a valid reading, and
only charging state 1 adds a charging indication. Available device names and
categories supply the labels. Actual accessory reporting depends on the
properties supplied by Windows and the device's driver.

Peripheral queries run through the shared applet worker, keeping process and
device enumeration work off GTK. The initial and periodic requests coalesce;
stopped or restarted applets and replaced backends reject old replies. The
frequent native power reading keeps its existing timer. The shared zero-percent
tooltip correction is isolated in [PR #479](https://github.com/edumucelli/docking/pull/479).

`tools/windows/visual/battery_smoke.py` runs the full dock and compares the native readings
with Windows Forms' power-status interface and `Win32_Battery` enumeration.
On the lab VM both report AC power with no system battery, and the applet shows
the disabled glyph and “No battery” tooltip. Native keyboard input changes
and persists the overlay preference and opens the actual System > Power page.
Normal shutdown restores the exact work area, releases the Search shortcut,
and removes both Battery timers.
Focused battery, hover, tooltip and interaction tests passed 215 cases on
native Windows and 214 on Linux, with the native API case skipped on Linux.

The optional `--exercise-fixtures` mode then injects clearly labeled PowerAPI
snapshots through the real backend and applet pipeline. Desktop captures check
discharge/remaining time, charging, plugged-in idle, watts, unknown readings and
explicit accessory-property fixtures. A delayed peripheral fixture exercises
native menu input while the worker is pending.
These are rendering and control acceptance, not physical battery evidence.
Physical batteries, multiple batteries, UPS behavior, unplug/replug, resume and
wireless accessory charge readings still need hardware acceptance.

The captures exposed two shared UI regressions. The white charging bolt was
invisible above a low green fill; an outline now keeps it legible. Also, a
native enter event without a subsequent motion event left hover identity and
the tooltip unset. The enter handler now resolves hover immediately. Regression
tests failed before these fixes, including entry at all four dock edges, and
the final Windows desktop check confirms visible tooltips for every state.

The native desktop observer now excludes DWM-cloaked retained application
frames, so a suspended UWP Settings frame is not mistaken for a visible window.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\battery_smoke.py' \
  --output '\\host.lan\Data\lab\battery' --exercise-fixtures
```

References: [system power status](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-system_power_status),
[native battery rate](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-system_battery_state),
[power information query](https://learn.microsoft.com/en-us/windows/win32/api/powerbase/nf-powerbase-callntpowerinformation),
[device-container properties](https://learn.microsoft.com/en-us/windows/uwp/devices-sensors/device-information-properties),
[device battery percentage](https://learn.microsoft.com/en-us/windows/win32/properties/props-system-devices-batterylife),
[device charging state](https://learn.microsoft.com/en-us/windows/win32/properties/props-system-devices-chargingstate),
[Settings URI reference](https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-settings).

## Native Network monitoring, Wi-Fi and settings

The Windows Network backend reads transport-bound adapters and IPv4/IPv6
addresses with `GetAdaptersAddresses`, cumulative 64-bit byte counters with
`GetIfEntry2`, and default routes with `GetIpForwardTable2`. Route selection
adds the adapter metric to the route metric. Automatic selection prefers an
IPv4 default route, then an IPv6 default route, then a connected local link.
The returned route table is copied and released with `FreeMibTable`.

Counter reads use the current interface LUID so index reuse during an adapter
change cannot attach another device's counters to the old sample.
The interface preference stores the persistent adapter identifier, while the
menu and tooltip show its editable name. Explicit selection does not silently
switch to another adapter when the requested one disappears. A change of
identity resets the shared traffic baseline even when the display name stays
the same. IPv6 scope identifiers are retained for link-local addresses.
Automatic selection remains available as the “Auto” menu choice.

`tools/windows/visual/network_smoke.py` runs the complete dock and compares the selected
adapter's GUID, name, operational status and address with `Get-NetAdapter` and
`Get-NetIPAddress`. It brackets the native counters with
`Get-NetAdapterStatistics`, copies an owned 8 MiB payload over the lab SMB share,
and verifies that both byte totals increase by at least the payload size.
The applet's normal two-second poll produces visible speed updates; the test
uses native keyboard input to select an adapter, change the upload overlay,
hide speeds, and restore Auto, checking the persisted JSON preferences.

The same test opens the actual Network & internet and Advanced network settings
pages through backend-owned shell actions. Normal dock shutdown restores the
original work area, releases the Search shortcut and removes the Network timer.
The payload files are removed, and the desktop captures were inspected.

The initial monitoring milestone passed 269 focused cases on Linux, with six native-only cases skipped,
and 137 selected cases on Windows. The window backend layout change also passed
the real Notepad launch/minimize/restore/Close check without forced shutdown.

For the desktop tool, prepare `traffic-source.bin` containing 8 MiB of data in a
fresh output directory writable by the lab guest, then submit:

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\network_smoke.py' \
  --output '\\host.lan\Data\lab\network'
```

The Windows connection manager implements the shared `ConnectionManager`
contract in `windows_connections.py`. `windows_wifi.py` owns WLAN bindings and
network/radio values; `windows_profiles.py` owns WinRT connection metadata.
Routine polls read `GetConnectedSsid` and `GetSignalBars` through WinRT and match
profiles to the selected adapter GUID. Signal bars map to 20-percent steps;
missing signal data stays unknown and has a question-mark glyph. A Wi-Fi link
with no accessible SSID is not labeled as Ethernet.

Discovery runs only when the user opens the Network menu, with scans throttled
to fifteen seconds. Location-sensitive WLAN current-connection and BSSID queries
are not used for routine monitoring. A denied discovery request appears as
“Permission denied” rather than an empty scan. Available networks retain the
exact adapter, raw SSID, case-sensitive profile and BSS type; a connection action
revalidates that identity before calling `WlanConnect`. Networks without a saved
profile open Windows Wi-Fi Settings for native credential setup. The menu also
opens native Wi-Fi/hidden-network and Mobile hotspot settings. Unusable networks
are disabled; software radio state is refreshed after writes and hardware-blocked
radios cannot be toggled. Shared capabilities omit the unsupported global
network switch. Native RAS/VPN control is described below.

The native no-adapter probe covered stopped and running WLAN service states,
then restored the original stopped state. Native enumeration agreed with an
independent `Get-NetAdapter` query. Forty repeated WLAN/WinRT reads kept the
process handle count at 159. Tests cover allocation and handle cleanup, partial
COM/HSTRING acquisition, existing STA and concurrent MTA calls, Unicode names,
raw SSID identity, stale menu entries, access denial and recovery.

Add `--exercise-wifi-fixtures` to the desktop command above to exercise labeled
Wi-Fi API snapshots through the complete application. Real keyboard/mouse input
selects a saved-profile fixture, turns its software radio off/on, verifies a
hardware-blocked toggle, unknown signal and permission-denied recovery, and
launches the actual Windows Settings application. On this no-Wi-Fi VM, the
wireless links redirected to Settings Home; the Wi-Fi and Mobile hotspot pages
remain unverified. The same run verifies real wired metadata and SMB traffic
before injection and restores the real backend before shutdown. The wireless snapshots do **not** prove physical Wi-Fi
association, authentication, signal accuracy, hardware switching or hotplug.

The baseline has one wired adapter on one display at 96 DPI. Physical Wi-Fi and
other adapter changes, multiple-route desktop acceptance, global networking
control and broader network hardware remain required parity work. VPN methods
beyond the verified IKEv2/password configuration also need acceptance.

References: [adapter addresses and interface metrics](https://learn.microsoft.com/en-us/windows/win32/api/iptypes/ns-iptypes-ip_adapter_addresses_lh),
[interface byte counters](https://learn.microsoft.com/en-us/windows/win32/api/netioapi/ns-netioapi-mib_if_row2),
[route metrics](https://learn.microsoft.com/en-us/windows-hardware/drivers/network/mib-ipforward-row2),
[route table ownership](https://learn.microsoft.com/en-us/windows-hardware/drivers/network/getipforwardtable2),
[Wi-Fi location-access changes](https://learn.microsoft.com/en-us/windows/win32/nativewifi/wi-fi-access-location-changes),
[connected SSID](https://learn.microsoft.com/en-us/uwp/api/windows.networking.connectivity.wlanconnectionprofiledetails.getconnectedssid),
[signal bars](https://learn.microsoft.com/en-us/uwp/api/windows.networking.connectivity.connectionprofile.getsignalbars),
[WLAN connection parameters](https://learn.microsoft.com/en-us/windows/win32/api/wlanapi/ns-wlanapi-wlan_connection_parameters),
[WLAN radio state](https://learn.microsoft.com/en-us/windows/win32/api/wlanapi/ns-wlanapi-wlan_phy_radio_state),
[WinRT ABI declarations](https://github.com/microsoft/windows-rs/blob/master/crates/libs/windows/src/Windows/Networking/Connectivity/mod.rs),
[Settings URI reference](https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-settings).

## Native Windows VPN connections

The Windows connection manager lists native VPN entries from both the current
user and all-user phonebooks using `RasEnumEntriesW` and `RasGetEntryPropertiesW`.
Entries are identified by GUID and normalized phonebook path; names can change
without retargeting an old menu item. Non-VPN entries and inaccessible/deleted
profiles are omitted. Connection state comes from `RasEnumConnectionsW` and
`RasGetConnectStatusW`, rather than the existence of a dialer window. The shared
VPN submenu now checks connected entries and disables pending or non-owned
actions; the phonebook path is available as a tooltip when scope matters.

Connecting opens Windows' `rasphone.exe` for the exact phonebook and current
entry name. Its absolute path comes from `GetSystemDirectoryW`, arguments are
quoted without a shell, and Windows owns credential prompts, cancellation and
errors. The dock does not read or save credentials. Disconnect runs off the GTK
thread, revalidates session identity and ownership, calls `RasHangUpW`, then
waits for the connection handle to disappear. Both invalid-handle and not-found
responses are expected during teardown. Duplicate disconnects are suppressed,
and shutdown waits for the bounded native teardown before discarding callbacks.
Existing OS-owned connections and dialer windows remain independent of the dock.

The acceptance endpoint used strongSwan 5.9.8 in a temporary container on the
private VM bridge, with a temporary trusted test CA and EAP-MSCHAPv2 credentials.
The profile used split tunneling and one route to an owned HTTP test address;
normal traffic did not use the test VPN. Native user/system profile dialogs
were opened and cancelled with keyboard input. The real connection was then
established from the dock, checked against PowerShell's VPN GUID/status, and
used to exchange an expected HTTP token through the tunnel. The menu's connected
checkmark and dock disconnect were verified. A second real connection exercised
an external Windows disconnect and the resulting menu state.

`tools/windows/visual/vpn_smoke.py` requires an owned fixture JSON file containing `address`,
`port`, expected HTTP `token`, and the IKEv2 `username`/`password`. A sibling
`profiles.json` lists the two provisioned user/system VPN profiles by GUID and
phonebook. The tool checks their ownership markers, uses actual input for menu
and credential actions, omits credentials from reports, and restores the work
area, timer and Search hotkey through normal shutdown. Profile/certificate and
endpoint provisioning/cleanup are separate lab steps. The test CA, both owned
profiles, endpoint container and generated credentials must be removed after
acceptance. Other authentication methods, server policies, vendor clients,
resume, reconnect and multi-user sessions remain broader acceptance work.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\vpn_smoke.py' \
  --fixture '\\host.lan\Data\lab\vpn-endpoint\fixture.json' \
  --output '\\host.lan\Data\lab\vpn-acceptance'
```

References: [RAS enumeration](https://learn.microsoft.com/en-us/windows/win32/api/ras/nf-ras-rasenumentriesw),
[entry properties](https://learn.microsoft.com/en-us/windows/win32/api/ras/nf-ras-rasgetentrypropertiesw),
[connection status](https://learn.microsoft.com/en-us/windows/win32/api/ras/nf-ras-rasgetconnectstatusw),
[disconnect and teardown](https://learn.microsoft.com/en-us/windows/win32/api/ras/nf-ras-rashangupw),
[SDK ABI definitions](https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/um/Ras.h),
[strongSwan Windows interoperability](https://docs.strongswan.org/docs/latest/interop/windowsClients.html).

## Native Session actions

Session uses a semantic `SessionBackend` contract: list available actions,
request an action, and stop applet-owned work. The shared state contains labels,
and the GTK applet handles icons, menu ordering and disabled actions. Linux and
BSD implement the contract through their existing session commands; Unix lock
fallbacks, D-Bus calls and Flatpak host execution live in `session/unix.py`.
Windows does not construct Unix commands.

`session/windows.py` uses `LockWorkStation` for lock, `ExitWindowsEx` for sign
out/restart/power off, and `SetSuspendState` for suspend. Power actions require
an existing `SeShutdownPrivilege` in the process token. Menu probes only read
the token; requests enable that right temporarily and restore its exact prior
state before closing the token handle. No rights are added or elevation
requested. Power capability reads use the SDK's 76-byte
`SYSTEM_POWER_CAPABILITIES`; Suspend requires an actual S1/S2/S3 or S0 low-power
idle capability, and power off requires S5. Availability is rechecked when an
action executes. Requests run on one owned worker to keep the GTK loop responsive
and suppress duplicates. Shutdown does not force applications closed; Windows
owns unsaved-work prompts and may reject or cancel a transition. An accepted
request is distinct from an observed completed transition.

On the baseline VM, native capabilities and independent `powercfg /a` agree that
no sleep state is available, so Suspend is disabled. Real left-click lock was
verified with WTS session flags changing from unlocked to locked, an independent
QEMU capture of the secure lock desktop, and external credential input to
unlock. The logon identity was unchanged after unlock, and normal dock shutdown
restored the work area and released the global shortcut.

Sign out, restart and power off were selected through the actual dock menu.
After sign out, WTS reported a new session/logon identity with the same boot
time, and Winlogon recorded the sign-out/sign-in events. Restart produced a new
boot time and a User32 planned-restart event from `pythonw.exe`. Power off
cleanly stopped the VM container; after starting that same persistent VM,
Windows reported another new boot and a User32 planned-power-off event from
`pythonw.exe`. Automatic login and the interactive lab agent recovered after
both boots. These transitions end the dock process and were verified externally;
they are distinct from the inspection/lock runs' normal GTK shutdown checks.

`tools/windows/visual/session_smoke.py` uses real mouse/keyboard input and independent
WTS state. Its default mode inspects the menu and quits normally. `--action lock`
waits for an external unlock. `--action logout`, `reboot` or `poweroff` terminates
the guest session; a host observer must prove the transition and recover the
owned VM. The tool's pre-transition record alone is not proof of success.
Physical sleep/resume, Modern Standby, remote/multi-user sessions and domain
policies need broader acceptance.

References: [lock request semantics](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-lockworkstation),
[graceful exit and shutdown rights](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-exitwindowsex),
[suspend and wake events](https://learn.microsoft.com/en-us/windows/win32/api/powrprof/nf-powrprof-setsuspendstate),
[power capabilities](https://learn.microsoft.com/en-us/windows/win32/api/powerbase/nf-powerbase-getpwrcapabilities),
[token privilege changes and restoration](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-adjusttokenprivileges),
[WTS lock state](https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/ns-wtsapi32-wtsinfoex_level1_w).

## Windows Mic Shield

Mic Shield follows one `MicShieldBackend` contract for state, explicit mute,
toggle, settings and shutdown. Shared state, tooltip/icon rendering, GTK menus
and lifecycle contain no audio-server commands or native API calls. Linux owns
PulseAudio/PipeWire source enumeration, monitor-source filtering, corked capture
streams, locale-stable `pactl` parsing and commands in `micshield/linux.py`.
Windows selects `micshield/windows.py` with Core Audio in `windows_audio.py`.

Windows enumerates every active **capture** endpoint rather than selecting only
the default microphone. The aggregate is muted only when all readable inputs
are muted. A known unmuted input disproves aggregate mute; otherwise unreadable
mute state stays unknown. Active session enumeration supplies capture process
IDs and names. Metadata failures retain known active capture; unreadable session
state stays unknown instead of becoming idle. A multi-process session is not
attributed to one PID. Interfaces, endpoint/session strings and process handles
are scoped to their acquisition thread. Capture queries use an MTA worker.

Native mute/unmute applies to all current input endpoints, continues protecting
other inputs after one fails and requires actual readback for success. An
unknown-state toggle chooses mute. Periodic reads are guarded, user transitions
are serialized and readback determines presentation. Construction performs no
provider query on GTK. Old polls cannot undo a completed action, and shutdown or
restart invalidates queued work, replies and the activity pulse. The applet's
Microphone Settings action opens **Privacy & security > Microphone**.

Native acceptance uses two emulated HDA inputs fed a generated 440 Hz tone
through an isolated ALSA file source. It opens neither a host microphone nor
physical audio hardware. Two separate Windows WASAPI recorder processes report
real PCM statistics; an independent C#/.NET Core Audio oracle reads and changes
endpoint mute and identifies the default input. Real mouse/menu input muted
both streams (RMS about 5,800 to below 1), restored both on unmute, and detected
an external change that left only the **non-default input unmuted**. Each
recorder's actual PID appeared in the menu. Stopping capture returned to idle
and stopped the red pulse. Native privacy Settings, accessibility metadata,
inspected screenshots, original mute restoration and normal close passed.
Unknown metadata uses an explicitly injected API fixture.

The optional fixture is separate from the normal lab. Generate its input, then
recreate only the owned lab with the microphone overlay:

```sh
python tools/windows/visual/microphone_fixture.py --share /path/to/shared/artifacts
docker compose --env-file /path/to/lab.env -p lab \
  -f packaging/windows/lab/compose.yaml \
  -f packaging/windows/lab/compose.microphone.yaml up -d --force-recreate --pull never
python tools/windows/visual/lab.py --share /path/to/shared/artifacts configure-share
```

Record the two newly created capture endpoint IDs and original mute state. Run
`tools/windows/visual/micshield_smoke.py --output ... --device ID1 --device ID2` in the
interactive Windows lab. It restores mute state and closes its owned recorders.
After acceptance, recreate the lab with **only** `compose.yaml`, reconnect its
share, verify the capture endpoints are gone, and remove the generated
`lab/microphone-fixture/tone.raw` and `alsa.conf`. The verifier's
`--expect-absent` mode checks the regular lab's real no-input UI and Settings.
Keep screenshots and result journals. The regular demo's pins and recent
history remain separate from all acceptance configurations.

This controls capture endpoint mute. Hardware-supported controls also affect
exclusive streams; software controls can be bypassed by exclusive access.
Physical/USB/Bluetooth/RDP inputs, hotplug/default changes during capture,
exclusive-mode behavior, protected or multi-process sessions and broader
permission/policy cases remain acceptance work.

References: [capture endpoint enumeration](https://learn.microsoft.com/en-us/windows/win32/api/mmdeviceapi/nf-mmdeviceapi-immdeviceenumerator-enumaudioendpoints),
[endpoint mute and shared/exclusive behavior](https://learn.microsoft.com/en-us/windows/win32/api/endpointvolume/nn-endpointvolume-iaudioendpointvolume),
[active session state](https://learn.microsoft.com/en-us/windows/win32/api/audiopolicy/nf-audiopolicy-iaudiosessioncontrol-getstate),
[process attribution](https://learn.microsoft.com/en-us/windows/win32/api/audiopolicy/nf-audiopolicy-iaudiosessioncontrol2-getprocessid),
[synthetic ALSA file input](https://www.alsa-project.org/alsa-doc/alsa-lib/pcm_plugins.html).

## Windows Cam Shield

Cam Shield uses one `CamshieldBackend` for state, lock/unlock capabilities,
explicit actions, settings and shutdown. Shared GTK, rendering and state have
no `/dev`, procfs, ACL, `pkexec` or Windows API operations. Linux and BSD own
their camera discovery and privileged policies. Their shared Unix client
launches the existing permission helper only after a user action.

Windows selects `camshield/windows.py`. It aggregates Media Foundation capture
source metadata and sensor activity reports, grouping multiple devices held
by one PID. A reported process still counts as a holder when it is not currently
streaming, consistent with an open Linux video file descriptor. Process names
use the Windows process backend, retaining the PID when its executable cannot
be read. An uninitialized or failed monitor, or a missing device in a nonempty
report, produces **Camera activity unknown**. A successfully delivered empty
aggregate is the native idle notification, including after the last client
stops. Enumeration failure produces **Camera state unavailable**.
Reports for cameras no longer in the current inventory cannot keep them active.
The backend never activates a capture source or requests video frames.

Native callbacks live in an owned sensor process. That process owns its MTA,
Media Foundation lifetime, callback thunks, report interfaces and allocated
strings. The dock serializes small requests, validates replies and applies a
four-second request deadline. Failure reaps the owned process before replacing
it. Shutdown runs a bounded process reaper without blocking GTK. Construction
does no native work; guarded background queries, action serialization and
lifecycle generations reject stale reads and queued operations after stop or
restart. The activity pulse stops when activity ends or the applet stops.

Windows controls use the documented machine `AllowCamera` policy on Pro,
Enterprise, Education and IoT Enterprise. `windows_control.py` separates policy
state and explicit action dispatch from activity reporting. Policy and recovery
registry operations live in Windows implementations. The shared applet only
asks its backend for capabilities and actions.

Lock blocks **new sessions** across the machine. Already-open readers can keep
receiving frames; their activity and PIDs remain visible alongside **Camera
locked**. No camera is opened by the applet to test enforcement. The current
inventory covers available Media Foundation video sources, not every disabled
PnP device or proprietary camera stack. PnP disabling is deliberately not used:
the lab's generated software camera kept delivering real frames to a new reader
with a cached symbolic link even after its node reported disabled.

An explicit action launches the fixed `windows_helper.py lock|unlock` command,
using `ShellExecuteExW` with `runas` when the dock is not elevated. Cancellation,
helper failure, timeouts and failed readback do not report successful actions.
An elevated dock still uses a separate bounded helper. Background polling never
requests elevation. The helper serializes changes with a named mutex and writes
and flushes the original policy value before restricting access. Recovery data
lives in a fixed HKLM key with a protected administrator/SYSTEM writable DACL
and read-only access for users; every read verifies the key's owner and DACL.
The helper accepts no device ID, registry path or recovery-file arguments.

Unlock restores the original DWORD `1` or removes only the value when it was
previously absent. A restriction already set by another tool is never adopted.
If an administrator has replaced or removed Docking's restriction, recovery
preserves their current value and clears the stale record. Recovery survives
dock/backend restart and remains available with no cameras present. The camera
device enablement and per-app privacy permissions are independent of this
policy. Home and unrecognized editions keep monitoring and Settings without
advertising this policy control.

On the Windows 11 lab, two independent monitor start/stop cycles and repeated
queries returned an empty camera inventory, agreeing with a separate PnP camera
enumeration. The native desktop verifier checks this real absence state, both
unavailable controls, the Camera Settings menu handoff, accessibility metadata,
inspected screenshots, sensor-process exit and normal dock shutdown/work-area
restoration. Unknown and provider-error rendering also use explicitly injected
API fixtures.

Native capture acceptance uses a generated-frame Windows virtual camera. Two
independent Media Foundation reader processes receive real 320x240 NV12 frames
with advancing timestamps and changing pixel checksums. Shared mode permits
both clients to stream simultaneously; both actual PIDs appear in Cam Shield.
The activity pulse advances while they capture. Closing one reader keeps the
other active; closing the last reader yields the native empty report, returns
the menu to idle and stops the pulse. Reader and sensor-process exit, privacy
Settings, normal dock close and work-area restoration are verified. The test
opens no physical camera and uses no injected activity data.

Native policy acceptance uses the real GTK menu and elevated helper: a fresh
reader fails with `0x80070005` and zero frames while an existing reader keeps
capturing. A new backend recovers the saved policy and offers Unlock; after
Unlock, a fresh reader receives generated frames again. The exact policy
baseline and empty recovery journal are checked in final cleanup, alongside
all reader/sensor exits and normal dock shutdown.

**Physical/IR/proprietary devices and device hotplug/removal during capture
remain open.** Native `ShellExecuteExW`/`runas` dispatch and recovery with no
cameras present also passed. Those runs used UAC disabled (`EnableLUA=0`), so actual
consent and cancellation on a standard-user desktop remain unverified;
cancellation and process-handle cleanup have focused API tests.

The following active state is an **API rendering fixture**, not native capture:

The following captures show **actual native capture clients** using only
generated frames:

Run `tools/windows/visual/camshield_smoke.py --output ... --expect-absent` in the
interactive owned Windows lab after preparing its GStreamer registry. Keep
the regular dock closed during acceptance and restore its normal configuration
afterward. The verifier uses an isolated configuration and leaves pin/recent
history intact.

For capture acceptance, use an elevated Windows 11 24H2-or-newer lab without
existing cameras, MSYS2 UCRT64 GCC (`mingw-w64-ucrt-x86_64-gcc`), Python/GTK and
Pillow. Run `tools/windows/visual/camera_fixture.py --output C:\DockingLab\camera-test
--journal PATH\session.json --seconds 300` with a fresh local output directory
and journal directory. It compiles `tools/windows/visual/camera_source.cpp` with warnings as
errors and records ownership before registering its unique COM class. The
camera is current-user/session scoped and generates frames entirely in memory.
Before declaring readiness, a short native read/stop cycle verifies the frames
and releases Windows' initial registration activity, establishing an idle
baseline for the desktop verifier.
Use the exact symbolic link from the ready journal with
`tools/windows/visual/camshield_smoke.py --output ... --device SYMBOLIC_LINK`.
Add `--verify-policy` to exercise actual menu Lock/Unlock, denied fresh capture,
continued existing capture, backend restart/recovery and restored capture. This
option requires the sole generated camera, a supported edition and an initially
unrestricted policy with no recovery pending. Its finalizer restores the saved
policy through the same helper even after a test failure. The verifier owns
and normally stops its capture processes. Write `stop` in
the fixture journal's directory to remove the camera and its owned COM class.
After an owner crash, `tools/windows/visual/camera_fixture.py --recover PATH\session.json`
checks process identity, DLL hash and registry ownership before cleanup. Both
normal removal and recovery after terminating only the owned fixture process
have passed in the lab. Keep the result journals and screenshots; fixture COM
registration recovery is separate from applet camera policy recovery.

References: [sensor activity monitoring](https://learn.microsoft.com/en-us/windows/win32/api/mfidl/nf-mfidl-mfcreatesensoractivitymonitor),
[activity report callbacks](https://learn.microsoft.com/en-us/windows/win32/api/mfidl/nf-mfidl-imfsensoractivitiesreportcallback-onactivitiesreport),
[sensor process reports](https://learn.microsoft.com/en-us/windows/win32/api/mfidl/nn-mfidl-imfsensorprocessactivity),
[camera source enumeration](https://learn.microsoft.com/en-us/windows/win32/api/mfidl/nf-mfidl-mfenumdevicesources),
[camera privacy guidelines](https://learn.microsoft.com/en-us/windows/apps/develop/camera/camera-privacy-guidelines),
[session-scoped virtual cameras](https://learn.microsoft.com/en-us/windows/win32/api/mfvirtualcamera/nf-mfvirtualcamera-mfcreatevirtualcamera),
[custom media source requirements](https://learn.microsoft.com/en-us/windows-hardware/drivers/stream/frame-server-custom-media-source),
[shared camera sources](https://learn.microsoft.com/en-us/windows/win32/medfound/mf-devsource-attribute-frameserver-share-mode),
[machine camera policy and supported editions](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-camera).

## Windows System Tray

The shared `platform/tray` package owns immutable item/menu models, the backend
contract, one background poll service and launcher notification projection.
`LinuxTrayBackend` adapts StatusNotifier/AppIndicator metadata and D-Bus menus;
menu clients are prepared during the background poll. Unix XEmbed windows and
their takeover/release UI live in `systemtray/unix_presentation.py`, loaded only
by the Unix presentation factory. The shared applet performs no D-Bus calls,
loads no X11 library and knows no Windows handles. Compatibility imports remain
under `platform/status_notifier` for existing consumers.

Windows uses `WindowsTrayBackend` and an owned message thread with a hidden
`Shell_TrayWnd`/`TrayNotifyWnd` pair. The host receives real `Shell_NotifyIcon`
registrations while forwarding notification and AppBar messages to Explorer.
Explorer keeps its visible notification area and topmost style. A session-local
mutex prevents two Docking hosts competing for routing. The host must run at
Explorer's integrity level; it does not weaken the window message filter to
accept lower-integrity senders.

The Windows transport bounds and decodes the shell's normalized Unicode wire
format, rather than interpreting it as a native pointer-sized
`NOTIFYICONDATA`. GUID identities, legacy window/UID identities, flagged
modifications, notification versions and app-requested hidden state are kept
distinct. Existing clients are seeded through `TaskbarCreated`; a duplicate
registration accepted by Docking succeeds even when Explorer already has it.
Unknown messages continue to Explorer.

GUID-only modifications can omit the callback window and numeric icon ID.
The Windows implementation resolves the existing registration, validates its
live owner, and preserves the callback window, numeric ID and version while
applying only flagged metadata. Native acceptance first reproduced an update
that Windows accepted while Docking retained the old title. The corrected
backend follows icon/title and hidden-state changes, retains version-4
double-click delivery to the original client, and removes the icon by GUID.
Use `--guid-only-updates` with the tray smoke tool to repeat this check.

Borrowed HICONs are copied synchronously into RGBA before the registering call
returns. Two GDI composites recover transparency for alpha and masked icons;
the DCs and bitmaps are released after each copy. Only pixels reach GTK. Native
tooltip titles and multiline status are projected separately. Polling rejects
destroyed/reused owner windows; actions validate the current owner again.

Open sends the provider's primary activation. Double-click the row's icon for
providers that require that gesture. Menu sends the native context request;
the application owns and renders its menu. Callback version 4 packs the icon ID
and signed screen coordinates as required by the notification API. Explicit
actions grant foreground permission to the selected process. Windows still
enforces message and foreground restrictions. GTK tooltips stay hidden while
the tray popup is open.

The shared tray contract accepts immutable presentation rectangles and an
optional invocation anchor. The applet measures its GTK images on the main
thread; `SurfaceService.screen_rectangle` converts client-relative bounds to
the platform desktop coordinate space. Windows reuses its monitor mapping to
produce physical coordinates, including scale and origin translation. X11
uses GTK desktop coordinates for SNI's position hints. Compositors with no
known global placement can leave the projection unavailable.

The Windows host bounds its icon-position wire decoder and resolves GUID or
window/UID identity through the backend's live owner checks. Native traces
establish that the two requests return origin and size. The implementation
packs signed coordinates and dimensions separately. Visible rows republish
geometry after movement and content changes. An explicit invocation retains
its initiating icon bounds while the GTK popup hides, so an asynchronous native
provider can query them before opening its own menu. A new presentation,
service replacement, applet stop, icon removal or backend close clears the
corresponding cached placement.

The hidden receiver also follows Explorer's actual taskbar rectangle. This
preserves Explorer's fallback placement for unpublished icons; a transparent
receiver at the top of the display had shifted those fallback queries there.
No GTK object or Windows handle crosses the shared tray geometry contract.

After routing to a replacement Explorer window/process, the host reseeds
clients once. A native run exposed a missed restart broadcast; the explicit
reseed recovers it. AppBar registration tracks the real Explorer identity too:
a repeated `TaskbarCreated` from another host keeps its existing registration
and reflows its rectangle. An actual replacement requires registration again.
This avoids duplicate-registration failures and preserves explicit removal on
shutdown. The tray and AppBar implementations share the native shell lookup.

Native acceptance compares an independent client's `Shell_NotifyIconGetRect`
results with GTK image allocations anchored to independent native popup HWND
bounds. Both legacy and GUID icons match exactly. The rectangles follow a
60-pixel horizontal and 50-pixel vertical popup move, and return to Explorer's
placement when the popup is hidden without an invocation. The native fixture
queries the SDK for its menu position; both actual context menus open at the
reported icon's left/bottom edge and accept keyboard selection. Separate SDK
checks verify distinct icon sizes and negative origins; mixed-DPI hardware
acceptance remains open.

The shared service rejects stale polls/timers after restart and creates a new
owned backend for a new lifetime. Applet actions and fetched menu layouts are
also guarded across stop, restart and service replacement. Normal close
destroys the receiver windows and releases the mutex. Because registrations
were forwarded, clients continue using Explorer without a synthetic shutdown
announcement.

Native desktop acceptance on Windows 11 Pro 25H2 exercises two independent
notification identities in a generated-pixel fixture: legacy callbacks and
GUID/version-4 callbacks, both registered before Docking starts. Actual mouse
input opens the GTK popup, double-clicks both icons, activates both native
windows and opens both native context menus; Down/Enter selects a native menu
command. Color/alpha pixels are checked in the backend and GTK row. The fixture
changes and destroys its source HICON, updates its title, hides/shows an icon,
deletes another and exits normally; the popup follows each change. No SNI,
XEmbed or GdkX11 module is loaded.

A separate real-provider check starts Task Manager, minimizes it and restores
and focuses it by double-clicking its icon in Docking's popup. Normal Task
Manager close removes its tray entry. A handoff probe keeps both fixture icons
alive while closing the backend, then verifies successful updates through
Explorer with no re-registration, work-area change or foreground change.
Full dock close restores the original work area and stops the owned host thread.
Across 60 native icon updates, the receiving process kept the same GDI/USER
handle counts; close returned its USER count to the pre-host baseline.

AppBar queries and position changes carry shared output memory as well as the
`WM_COPYDATA` input packet. `WindowsAppBarBridge` maps the caller's reply,
allocates a copy accessible to Explorer, forwards the request and copies the
result back. Each mapping and owned allocation is released on success or
failure. This keeps shell reservation behind the existing Windows surface
implementation while the notification host receives shell traffic.

The restart check exposed a missing reservation with the initial transparent
forwarding. With shared replies relayed, native acceptance verifies all four
edges against maximized Notepad bounds and exact work-area restoration. Two
actual Explorer process crashes/restarts in one dock lifetime recover both
native tray clients and the same reserved shelf. After each restart, a separate
shell operation recycles an owned document and restores it through its exact
shell item; the Trash applet receives both notifications. Existing Recycle Bin
contents are preserved. Tray menus, double-clicks, Task Manager restoration and
handoff to Explorer also pass after those restarts.

Independent client processes verify `ABM_GETTASKBARPOS` against Explorer's
actual window bounds before, during and after hosting. Across 600 forwarded
queries and three additional host lifetimes, the host handle count stays at
189 while running and returns to its warmed baseline of 185 after each close.
The first lifetime has a one-time increase from the cold process count;
subsequent lifetimes return to the same warmed value.

Run `tools/windows/visual/tray_smoke.py --output PATH --task-manager` in the owned lab
with the regular dock closed and a prepared GStreamer registry. Task Manager
must not already be running. The tool uses an isolated dock configuration and
starts `tools/windows/visual/tray_fixture.py`; each fixture has a bounded lifetime and
deletes its own icons on normal exit. Reports, independent callback journals
and screenshots remain in the output directory. The regular demo and its
pin/recent history should be restored afterward.

Add `--verify-geometry` to include independent SDK queries, popup movement and
native provider menu placement. Fixture commands use immutable revision files
so Windows SMB readers cannot block replacement of a shared command file.

Add `--restart-explorer` only in an owned test VM to include the two shell
crashes, reservation recovery, Recycle Bin round trips and live-icon handoff.
The tool refuses open Explorer folder windows and targets the verified shell
executable/PID. It recovers Explorer on failure after closing its own dock.

The shell hosting protocol is Windows implementation detail, with native
acceptance on the lab build rather than a documented replacement-host API.
Broader providers, WOW64 clients, custom hover interfaces/balloons, elevated
client policies, restart races, multiple displays/DPI and long-running sessions
remain acceptance work. Broader provider placement and real mixed-DPI/display
changes still need verification. This is an intermediate Windows milestone.

References: [notification area](https://learn.microsoft.com/en-us/windows/win32/shell/notification-area),
[notification data and callback versions](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/ns-shellapi-notifyicondataw),
[foreground permission](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-allowsetforegroundwindow),
[icon rectangle queries](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shell_notifyicongetrect),
[AppBar requests](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shappbarmessage),
[shared shell memory](https://learn.microsoft.com/en-us/windows/win32/api/shlwapi/nf-shlwapi-shallocshared),
[shell-host interoperability precedent](https://github.com/cairoshell/ManagedShell/blob/master/src/ManagedShell.WindowsTray/TrayService.cs).

## Windows Bluetooth

Bluetooth uses the shared `BluetoothBackend` contract. Windows supplies native
radio, endpoint and desktop implementations; Linux/BSD keep their existing
implementations. Device capabilities are published with each snapshot so GTK
can build menus without device I/O. Changes are delivered as a complete domain:
map the required APIs and ownership rules, implement the connected behaviors,
then exercise the integrated backend, menus, cancellation and Windows CI.
Use this same domain-wide sequence for the remaining Windows parity work.

| Behavior | Windows APIs and ownership |
| --- | --- |
| Adapter inventory and power | `BluetoothAdapter`, `Radio.State`, `Radio.RequestAccessAsync` and `Radio.SetStateAsync`; exact adapter ID/address validation and state readback |
| Classic inventory and discovery | `BluetoothFindFirstRadio` / `BluetoothFindFirstDevice`; cached reads normally, bounded inquiries using the selected radio's open handle during owned discovery |
| LE inventory and live changes | `DeviceInformation` AssociationEndpoint `DeviceWatcher`; explicit classic/LE protocol filters, Added/Updated/Removed events and bounded retained argument queues |
| LE advertisements and RSSI | Owned `BluetoothLEAdvertisementWatcher` during discovery; active scan responses, public/random address identity, expiration and read-only beacon rows |
| Classic pairing and removal | `BluetoothAuthenticateDeviceEx` with the selected radio handle; `BluetoothRemoveDevice` followed by authentication readback |
| LE pairing and removal | `DeviceInformationCustomPairing.PairAsync`, explicit ConfirmOnly/DisplayPin/ProvidePin/ConfirmPinMatch handling, `UnpairAsync` and fresh paired-state readback |
| LE connection ownership | `BluetoothLEDevice.FromBluetoothAddressAsync` with address type, full returned endpoint identity validation, `BluetoothDeviceId` and `GattSession.MaintainConnection`; success requires an active session, release closes only Docking's session |
| Incoming classic audio | `AudioPlaybackConnection` selector, `TryCreateFromId`, `StartAsync` and `OpenAsync`; matching device-container identity and opened-state readback |
| Accessory battery | Connected non-local `DeviceContainer` battery property, joined by container GUID; standard GATT Battery Service/Level reads on an already owned LE connection |
| Native desktop actions | `ms-settings:bluetooth` and the absolute system `fsquirt.exe -send` file-transfer wizard |
| Shutdown and cancellation | One owned MTA endpoint worker, cancellable async operations, scoped handles/interfaces/events, and a pairing helper assigned to a kill-on-close Windows process job before receiving input |

Radio states distinguish off, unknown and policy-disabled. Only an explicit
radio change requests access. Radio queries use a hidden, bounded PowerShell
process; endpoint interfaces stay in one MTA worker. Immutable records cross
into the applet. GTK shutdown sets cancellation flags without waiting for native
queries, discovery or prompts. Late results cannot repaint a removed applet.

Classic discovery is scoped to the selected radio. WinRT LE discovery and GATT
operations use the actual Windows default LE adapter: both its device ID and
address must match the selected radio. A different default, removal, power-off
or backend shutdown invalidates owned discovery and connections. LE-only radios
do not issue classic inquiries. Other applications' scans remain under their
control. Periodic endpoint rescan recovers missed updates; advertisement queues
and collections are bounded, and observations expire after 45 seconds.

Classic and LE endpoints retain separate logical identities even when their
addresses match. LE public/random address types must match before advertisements
supply endpoint telemetry. An advertisement alone cannot become a pairable
endpoint. Optional name, appearance, RSSI and battery data remain unknown when
unavailable. A known **0%** battery remains visible. Container metadata is joined
by GUID, never by display name. GATT battery reads have one shared 1.5-second
budget per poll, refresh at most one already owned accessory, and never open a
new device or establish a connection from an inventory read.

Unpaired endpoints expose **Pair** when supported. Pairing runs in an owned
helper with a native modal owner. Classic Windows authentication owns its
ceremony; custom LE pairing supports native PIN entry, PIN display and matching.
PINs stay inside the helper's native UI and are never logged, stored or sent
over IPC. User rejection, timeout and applet shutdown cancel the operation,
dismiss only its owned windows and reap the helper. Native success must be
followed by matching paired-state readback. Removal clears Docking's own
connection first. Classic bond removal is system-wide for that device address;
the adapter identity protects selection but does not make that Windows API
adapter-specific.

Windows has no independent BlueZ **Trusted** property or universal profile
connect/disconnect API. Controls reflect the operations Windows actually
provides. Paired LE endpoints expose **Keep Connected** and **Release Connection**
for Docking's GATT session. Supported incoming audio endpoints expose **Connect
Audio** and **Disconnect Audio** for remote-source audio played on this PC.
That API does not reconnect headset output or unrelated HID/profile links.
Releasing Docking's ownership can leave a device connected through Windows or
another application; the UI uses actual state. Native **Devices...** remains
available for Windows-managed profiles, including when no adapter is installed.
An owned session remains releasable if its link drops while Windows is trying
to reconnect; ownership is separate from the device's current connected state.

**Send Files to Device...** opens the installed system Bluetooth File Transfer
wizard in send mode. Windows owns recipient selection, authentication, progress
and cancellation. Actual menu selection, painted send-page controls and normal
cancellation passed in the adapter-free Windows 11 VM. Physical transfer remains
hardware acceptance.

The owned Windows 11 VM has no Bluetooth radio. Native WinRT/Win32 inventory and
an independent PnP oracle agree on its absence. Native activation, endpoint
watchers, actual PnP Added callback delivery, typed event registration and COM
reference cleanup are exercised without altering hardware. The full menu probe
also opens the actual Devices settings page and checks normal shutdown and
work-area restoration. Classic/LE/audio connections, pairing, metadata including
0% battery, radio power/denial/policy/removal and discovery are **explicit injected
API fixtures**. They exercise real GTK input and shared menus without RF writes.

`tools/windows/visual/pairing_smoke.py` exercises production helper IPC,
process ownership and native dialogs in twelve cases: accept/reject,
GTK cancellation, timeout, PIN accept/reject/stop/timeout, displayed PIN
accept/reject and matching PIN accept/reject. These are explicitly synthetic
ceremonies. Hosted Windows CI runs the same menu fixtures and pairing probes,
with captures uploaded as artifacts; its fixture-only menu mode does not depend
on Windows 11 Settings being installed on the server runner.

```sh
python -m tools.windows.visual.bluetooth_smoke --output artifacts/windows/bluetooth
python -m tools.windows.visual.pairing_smoke --output artifacts/windows/pairing
```

Physical radio/policy/permissions, multiple-adapter hotplug/default changes,
classic inquiries, LE advertisements, real pairing/removal, GATT/audio links,
accessory batteries and file transfer still require hardware acceptance. Native
factories and fixtures do not establish successful physical device operation.

References: [adapter enumeration](https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.bluetoothadapter.getdeviceselector),
[radio changes](https://learn.microsoft.com/en-us/uwp/api/windows.devices.radios.radio.setstateasync),
[device enumeration](https://learn.microsoft.com/en-us/windows/apps/develop/devices-sensors/enumerate-devices),
[LE advertisements](https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.advertisement.bluetoothleadvertisementwatcher),
[classic inventory](https://learn.microsoft.com/en-us/windows/win32/api/bluetoothapis/nf-bluetoothapis-bluetoothfindfirstdevice),
[classic removal](https://learn.microsoft.com/en-us/windows/win32/api/bluetoothapis/nf-bluetoothapis-bluetoothremovedevice),
[classic authentication](https://learn.microsoft.com/en-us/windows/win32/api/bluetoothapis/nf-bluetoothapis-bluetoothauthenticatedeviceex),
[custom pairing](https://learn.microsoft.com/en-us/windows/apps/develop/devices-sensors/pair-devices),
[GATT connection ownership](https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.genericattributeprofile.gattsession.maintainconnection),
[GATT battery reads](https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.genericattributeprofile.gattcharacteristic.readvalueasync),
[incoming audio playback](https://learn.microsoft.com/en-us/uwp/api/windows.media.audio.audioplaybackconnection),
[Windows file transfer](https://learn.microsoft.com/en-us/windows-hardware/drivers/bluetooth/bluetooth-user-interface).

## Windows keyboard layouts

Keyboard Layout uses the shared `LayoutBackend` contract. Input-source state,
labels, GTK presentation, polling and queued selection are shared. Linux daemon
and command integrations (GNOME, IBus, Fcitx5, MATE and XKB), Flatpak launching,
settings and layout viewers live in `keyboardlayout/linux.py`. Windows selects
`keyboardlayout/windows.py` independently and never falls back to Linux tools.
Discovery and switching run through the background worker; repeated requests
coalesce, stale polls cannot undo newer choices, and removal invalidates pending
work and replies.

Windows enumerates loaded input locales with `GetKeyboardLayoutList` and reads
the typing application's input thread with `GetKeyboardLayout`. Session-only
locale identities keep US and US Dvorak distinct even though both display EN.
Windows supplies the localized language, layout and variant descriptions. A
worker resolves physical layout and variant IDs through the keyboard-layout
registry without activating layouts, preserving foreground dead-key composition.
Descriptions are cached while their layouts remain loaded; the GTK interaction
snapshot uses the available cache.
No locale handles or window identities are saved in applet preferences.

Selection remains bound to the original application HWND/PID/thread identity.
The backend resolves the current focused control, including WinUI editors on a
separate thread. It retains a verified editor across the dock's temporary
foreground gap only while the same desktop is receiving input. Input-desktop
handles are always closed. A user selection restores and settles that typing
window before posting `WM_INPUTLANGCHANGEREQUEST` to its current input window.
A different foreground application, a reused/closed window, an unloaded locale
or an inactive/secure desktop prevents the old request. Readback determines the
selected source; a rejected change does not become a permanent checked choice.

The native menu service also accounts for asynchronous foreground restoration:
`SetForegroundWindow` can return success before foreground ownership changes.
Its bounded GTK retries preserve the original HWND/PID, stop when another
window takes focus, and are cancelled during shutdown. A new menu opened during
a pending restore captures a new foreground owner instead of restoring an old
application afterward.

Show Current Layout opens Windows' On-Screen Keyboard; Keyboard Settings opens
**Time & language > Typing**. Native desktop acceptance uses real menu keys,
mouse clicks and wheel input, an independent C#/Windows Forms input-locale oracle
and Notepad documents saved from physical scan-code input. The US Q key position
produces `q` with US, `a` with French and `'` with US Dvorak. The verifier loads
only its owned runtime layouts, preserves the configured preload/substitute and
default preferences, closes its saved test tabs/windows and unloads its handles.
External changes are observed through the normal two-second poll.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\keyboardlayout_smoke.py' \
  --output '\\host.lan\Data\lab\keyboardlayout-acceptance'
```

Windows also enumerates enabled TSF keyboard profiles, including distinct input
processors that share an HKL. Their session identities include the language,
service CLSID and profile GUID. The dedicated enabled-state query remains
authoritative when enumeration flags lag a language-profile change. Disabled
plain-keyboard profiles loaded only as
IME compatibility handles are excluded from the menu. Native TSF activation can
return to a configured keyboard profile; legacy Win32 layout requests remain
available. Profile descriptions come from TSF BSTRs; the target thread's
`CurrentInputMethodLanguageTag` supplies language metadata independently of a
transient LANGID. Metadata reads never activate an input source.

A small freestanding helper handles one registered request through a
thread-scoped `WH_GETMESSAGE` hook. It uses the application's existing COM
apartment for TSF, verifies window/thread/process and input-desktop ownership,
rechecks enabled profiles, and calls documented interfaces on that input thread.
Classic loaded layouts use `ActivateKeyboardLayout` on the same target thread
without requiring COM. Both paths retire cancelled queued requests before a
paused application can apply them after resuming.
Activation flags are zero: Docking does not enable profiles or change preferred
languages, registry defaults, process-wide or desktop-wide activation policy.
Windows retains control of its per-application input policies. The hook ignores
other messages and reads no keystrokes or composition text; IME metadata is
limited to open/conversion state and composition length. Cancellation retires
queued requests and owns hook, mapping and atom cleanup. A 32-bit target uses a
32-bit helper hosted by the system's `rundll32`, in an owned process job.

Windows builds compile x64 and x86 helpers with MinGW-w64 GCC and binutils. Both
DLLs depend only on Windows system libraries and ship in a platform-tagged
wheel, portable ZIP and installer. Source/editable builds require the compiler;
installed distributions do not. Helpers are copied to a content-addressed local
cache before a foreign application loads them. Linux/BSD wheels remain pure
Python. No generated DLL is checked in.

CI distinguishes `server-api` from `workstation-composition` input coverage.
Windows 11 x64 is the product target. Mandatory Windows 11 client CI tests the
exact Server-built AMD64 candidate wheel, retains all twelve request controls,
eight real Telex/VNI compositions and both plain GTK/Search preedit/Return/Escape
checks. Server retains API/profile activation, classic/dead-key/layout/shortcut,
cancellation and cleanup coverage without claiming composition. The observed
hosted Server image emits raw input in independent native editors and plain GTK
despite correct TSF/profile/mode readback; its failure is retained as evidence,
not reclassified as successful composition. No universal Server IME limitation
is inferred. Release staging requires the client reports, wheel digest and exact
source commit; see [Windows packaging](../packaging/windows/README.md).

`input_profile_smoke` temporarily enables two owned built-in Vietnamese test
profiles and restores their original state. On Windows 11 25H2, Telex physical
`A,A` and VNI physical `A,6` both produce `â` in saved 64-bit Notepad and 32-bit
Windows Forms text, with independent profile readback and unchanged caller
input. The acceptance check also returns to US, cancels a posted activation
while the editor is paused, resumes it without a late change, and restores
preload/substitute/default/wheel preferences. It can exercise an installed wheel:

```sh
python -m tools.windows.visual.input_profile_smoke \
  --installed-root artifacts/windows/runtime --output artifacts/windows/input-profiles
```

The independent reference matrix compares scan-code input and layout-mapped
virtual keys for both profiles in both editor architectures. It records exact
saved text for all eight variants. A composition mismatch is collected only
after the owned target, profile, IME restoration, caller state and return to US
are verified; other failures stop the fixture. Every variant must produce `â`.

Both tools independently read keyboard state in a fresh, isolated Python
process through Win32 and read-only registry access. This query needs neither
PowerShell nor runtime C# compilation. It compares loaded layout codes,
foreground/default layouts, wheel routing and keyboard preferences; failed
queries remain fatal rather than standing in for an unchanged baseline.

Both input tools accept `--owned-amd64-editor` to use a bounded AMD64 Forms
editor with explicit process-architecture checks. Windows 11 client CI uses
this mode with UCRT64 through x64 emulation on `windows-11-arm`, retaining the
independent x86 editor and the same strict composition/preedit assertions.
Compiler diagnostics are retained in bounded, redacted build reports.

The hosted client job wraps both checks with
`python -m tools.windows.visual.hosted_desktop --installed-root
artifacts/windows/runtime --wheel dist/*.whl --source-sha <full-source-commit>
--output artifacts/windows`. The wrapper is restricted
to disposable GitHub-hosted Windows 11 ARM runners. It scopes the documented
`DisablePrivacyExperience` policy to the batch, handles recognized first-login
windows and the runner's WSL-update terminal, and records desktop captures and
actions in `desktop-preparation.json`. It restores the policy afterward and
collects both input results in `input-domain.json`, retaining every strict
composition and restoration requirement. Unknown windows remain outside its
cleanup rules.
See the [runner privacy-screen issue](https://github.com/actions/runner-images/issues/14069),
[WSL terminal issue](https://github.com/actions/runner-images/issues/14264) and
[Microsoft privacy policy](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-privacy#disableprivacyexperience).

On an ARM host, a non-WOW64 process can still use the AMD64 ABI. The input hook
selector resolves that ambiguity with `GetProcessInformation` and
`ProcessMachineTypeInfo` before choosing an x86 or AMD64 helper.

CI runs both installed-wheel Search and native editor checks in fresh
processes through `python -m tools.windows.ci --stage input --installed-root
artifacts/windows/runtime --wheel dist/*.whl --source-sha <full-source-commit>`.
Use `--input-coverage server-api` for the explicit hosted Server API profile.
It collects both results in
`artifacts/windows/input-domain.json` before failing the batch, so a Search
failure still leaves the independent editor result available. Cancellation
stops the batch immediately.

Search gives an entry's IME first chance at palette navigation, commit and
cancellation keys. Ctrl+Alt/AltGr text does not invoke Ctrl+J/P navigation.
Shortcut capture rejects text-input modifiers, including physical right Alt
when Win32 GDK reports the same mask as Ctrl+Alt. The native global-hotkey worker
observes only right-Alt transitions, temporarily releases a conflicting Ctrl+Alt
registration, keeps pumping release events, and restores the registration.

The earlier language-bar marshaling experiment is excluded: Microsoft's
`ITfLangBarMgr.GetInputProcessorProfiles` and `GetThreadMarshalInterface`
interfaces are marked as APIs that should not be used. TSF activation requires
an already initialized input apartment; other targets retain Win32 layout
access. Elevated/protected or noncooperating applications, secure/remote
desktops, additional IMEs and multi-user policies still need broader acceptance.
Native API success is accompanied by actual composed-text checks where engines
exist.

References: [input locale enumeration](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getkeyboardlayoutlist),
[per-thread active input locale](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getkeyboardlayout),
[layout descriptions](https://learn.microsoft.com/en-us/windows/win32/intl/using-registry-string-redirection),
[layout request semantics](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-inputlangchangerequest),
[target-thread layout activation](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-activatekeyboardlayout),
[child notifications and TSF/transient ID limitations](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-inputlangchange),
[input-desktop ownership](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-openinputdesktop),
[TSF profile activation](https://learn.microsoft.com/en-us/windows/win32/api/msctf/nf-msctf-itfinputprocessorprofilemgr-activateprofile),
[thread-scoped hooks](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowshookexw),
[GTK entry IME filtering](https://docs.gtk.org/gtk3/method.Entry.im_context_filter_keypress.html).

## Windows Brightness

Brightness follows one `BrightnessBackend` interface for reading state, applying
changes to a named display and stopping work. Shared state and GTK code contain
no OS discovery, subprocesses or sysfs paths. Existing xrandr, brightnessctl and
backlight access live in `brightness/linux.py`; `brightness/windows.py` implements
the same interface for Windows.

The Windows backend identifies the primary monitor by its device interface,
uses the high-level DDC/CI brightness API when the monitor advertises support,
and otherwise matches the WMI backlight instance to that same display. WMI uses
the built-in Windows PowerShell CIM provider in a hidden process with a bounded
timeout. It never chooses the first unrelated laptop panel. Ambiguous cloned
displays and monitors without a usable provider are unavailable. Each DDC request
acquires fresh physical-monitor handles and releases them on every exit path.
Windows hardware control does not overwrite the user's gamma calibration.

The shared applet discovers devices in its background worker, observes external
changes and hotplug through polling, and renders unavailable state without a
fabricated percentage. Discrete hardware levels let wheel input advance on
panels with coarse brightness steps. Writes read back the actual device value,
including denied or rounded requests. Requests remain bound to their original
display, coalesce rapid input, reject stale polls and stop after applet removal.

The independent read-only `tools/windows/visual/brightness_probe.py` finds one QEMU
monitor on the baseline VM. DDC capabilities/brightness return not supported
(Win32 error 50), and `WmiMonitorBrightness` has no supported instances. The live
dock correctly displays **Brightness: Unavailable**. Physical laptop panels,
DDC/CI monitors, OEM behavior, HDR and multi-display changes still require real
hardware acceptance; this VM cannot prove physical brightness changes.

`tools/windows/visual/brightness_smoke.py` first verifies that real unavailable state,
then injects explicitly labeled DDC and WMI provider fixtures behind the Windows
backend. Real desktop wheel/click/menu input verifies scrolling, reset, denied
write readback, five-second external updates, display replacement/removal and
Show Level persistence. The fixture scenarios never write native monitor
settings. Normal dock shutdown removes its timer and restores the work area.
Native tests also execute the real WMI script under Windows PowerShell with
explicit CIM fixtures to verify instance matching, method arguments, discrete
levels and error handling. Read-only provider cycles check handle ownership.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\brightness_probe.py' \
  --output '\\host.lan\Data\lab\brightness-capabilities.json'
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\brightness_smoke.py' \
  --output '\\host.lan\Data\lab\brightness-acceptance'
```

References: [monitor device identity](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-enumdisplaydevicesw),
[monitor capabilities](https://learn.microsoft.com/en-us/windows/win32/api/highlevelmonitorconfigurationapi/nf-highlevelmonitorconfigurationapi-getmonitorcapabilities),
[DDC brightness readback](https://learn.microsoft.com/en-us/windows/win32/api/highlevelmonitorconfigurationapi/nf-highlevelmonitorconfigurationapi-getmonitorbrightness),
[WMI panel levels](https://learn.microsoft.com/en-us/windows/win32/wmicoreprov/wmimonitorbrightness),
[panel brightness method](https://learn.microsoft.com/en-us/windows/win32/wmicoreprov/wmisetbrightness-method-in-class-wmimonitorbrightnessmethods),
[gamma API limitations](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-setdevicegammaramp).

## Native Caffeine inhibition

Caffeine uses a shared `Inhibitor` acquire/release contract. The Unix
ScreenSaver D-Bus cookie and systemd child implementation lives in `caffeine/linux.py`;
`caffeine/windows.py` owns Windows display/system power-request counts and their
handle. Construction does no native IO. Acquisition is idempotent, partial
failure rolls back owned counts, and release closes the request object even
when clearing an individual count fails.

The shared `InhibitionSession` reconciles the latest toggle on a worker. GTK
confirms active state only after backend success, shows pending/unavailable
status, and starts its duration countdown after acquisition. Duration changes
do not acquire additional request counts. Stop invalidates old replies and
releases the lock on the worker, including when acquisition is already in
flight or its GTK result has not been delivered. Cleanup does not require
another GTK iteration, and the worker finishes before normal process exit.

`tools/windows/visual/caffeine_smoke.py` runs the complete dock. Actual native icon
clicks, menu duration selection/persistence, release, rapid explicit requests
and active-close cleanup passed at normal and doubled GTK scale. Independent
`powercfg /requests` observations verified both native request types and their
removal; actual handle creation/set/clear/close calls ran off GTK and balanced.
A labeled timing fixture reduces remaining time to three seconds to exercise
the real GTK countdown and native release without a 30-minute test. A separate
controlled barrier after a successful display request proved responsive GTK
shutdown and cleanup after the GTK loop exits. The failure mode deliberately
passes an invalid request kind to Windows, verifies unavailable/inactive UI and
no remaining lease, then retries with normal valid requests.

These requests prevent automatic display-off and idle sleep subject to Windows
power policy. They do not change screensaver, lock or persistent power settings,
or override explicit user sleep. Modern Standby on battery can terminate
system/execution requests after its documented timeout. The VM has no sleep
support, so physical suspend/display behavior, battery/Modern Standby, remote
sessions and other Windows versions remain acceptance limits.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\python.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\caffeine_smoke.py' \
  --output '\\host.lan\Data\lab\caffeine'
# Use fresh outputs for --stop-during-acquire or --fail-first-set.
```

References: [request object ownership](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-powercreaterequest),
[request types and policy limits](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-powersetrequest),
[balanced release](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-powerclearrequest),
[reason structure](https://learn.microsoft.com/en-us/windows/win32/api/minwinbase/ns-minwinbase-reason_context).

## Native Windows Power Profiles

Power Profiles has one `PowerProfilesControlBackend` contract for state, profile
selection and cleanup. The shared state/presentation module contains no D-Bus or
command execution. The existing PPD, tuned and TLP detection/parsing lives in
`powerprofiles/linux.py`; Windows is selected independently in `backend.py`.
Battery and Power Profiles share the native `SYSTEM_POWER_STATUS` ABI in
`platform/backends/windows/power.py`.

The Windows implementation uses the documented Windows 11
`PowerGetUserConfiguredACPowerMode`/`DCPowerMode` and corresponding setters.
The shared Power Saver/Balanced/Performance choices map to Windows' Best power
efficiency/Balanced/Best performance user preferences. It resolves the active
supply at execution time and writes only that supply's preference. Unknown
supply status or an unsupported mode is unavailable, with no fabricated
Balanced value. Readback verifies a successful write. Bounded snapshot reads
retry source/plan changes, and the user's base power plan is preserved.

The backend reads the active plan's personality, including Balanced-derived
custom plans, through `PowerGetActiveScheme` and `PowerReadACValue`. Every
allocated active-plan GUID is copied and freed with `LocalFree`. Other plan
personalities disable mode selection and explain that a Balanced plan is
needed. The dock never replaces a user's plan to make a mode request succeed.

A version-two effective-power-mode subscription observes system overrides.
The menu continues to check the user's configured preference while a limitation
line reports an overridden effective mode, Energy Saver, Game Mode or Mixed
Reality. No callback touches GTK. Backend shutdown waits for native writes,
unregisters the callback without holding its data lock, and stops future work.
The shared applet also rejects stale poll/set replies and queued writes after
removal. Normal dock Quit removes the applet timer and releases the dock's
global shortcut.

The desktop verifier uses native mouse/keyboard input and an independent C#
P/Invoke oracle through PowerShell. It exercises all three AC modes and effective
readback, radio checkmarks, left-click cycling, external changes reaching the
polling UI, a temporary Balanced-derived plan, a non-Balanced plan and recovery.
Temporary plans and both AC/DC preferences are restored by the verifier.
An actual Energy Saver switch is located/read through UI Automation and toggled
with real mouse input. On this VM, Energy Saver Standard changes effective mode
to the efficiency state while the older `SYSTEM_POWER_STATUS.SystemStatusFlag`
battery-saver flag stays zero. That older flag is not used as proof of Energy
Saver being off. The user's selected preference remains distinct from the
effective-mode limitation.

The actual Settings **System > Power** page was inspected. This VM does not
expose Settings' Power mode selector, so no selector acceptance is claimed.
Configured/effective state is verified through the independent native APIs.
Physical battery/AC transitions, OEM power management, policy restrictions and
additional Windows versions remain broader acceptance work.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\powerprofiles_smoke.py' \
  --output '\\host.lan\Data\lab\powerprofiles-acceptance'
```

References: [configured AC mode](https://learn.microsoft.com/en-us/windows/win32/api/powrprof/nf-powrprof-powergetuserconfiguredacpowermode),
[configured DC mode](https://learn.microsoft.com/en-us/windows/win32/api/powrprof/nf-powrprof-powergetuserconfigureddcpowermode),
[setting a mode and user/system intent](https://learn.microsoft.com/en-us/windows/win32/api/powrprof/nf-powrprof-powersetuserconfiguredacpowermode),
[effective-mode notifications](https://learn.microsoft.com/en-us/windows/win32/api/powersetting/nf-powersetting-powerregisterforeffectivepowermodenotifications),
[callback cleanup](https://learn.microsoft.com/en-us/windows/win32/api/powersetting/nf-powersetting-powerunregisterfromeffectivepowermodenotifications),
[power-plan selection](https://support.microsoft.com/en-us/windows/change-the-power-mode-for-your-windows-pc-c2aff038-22c9-f46d-5ca0-78696fdf2de8),
[power status and legacy battery saver](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-system_power_status).

## Native Recycle Bin

Trash selects a Windows shell backend. `SHQueryRecycleBinW` reads the current
user's bin across drives, and Open Trash launches `shell:RecycleBinFolder`.
Confirmed Empty uses `SHEmptyRecycleBinW`, then checks the remaining count.
The shared confirmation keeps Cancel as its default. Query failures retain
the last observed count; failed empty requests retain the applet's error badge.
The native structure follows the SDK's different 32-bit and 64-bit alignment.

A hidden window subscribes to the virtual Recycle Bin folder with
`SHChangeNotifyRegister`. Shared notification packets are locked and unlocked
before a coalesced GTK idle refresh. The observer also schedules reconnection
on `TaskbarCreated`. Two actual Explorer restarts in one dock lifetime verify
new recycle/restore notifications after each restart, with the original count
and owned document restored. Broader restart races remain acceptance work.
Stop removes queued work, deregisters the subscription, destroys
the window and frees its folder identifier. Gio monitoring on other platforms
continues through the existing file and volume monitors.

Dropped files use `IFileOperation` with recycling and undo enabled. Windows'
warning is retained if an item cannot be recycled. Network shares and mapped
network drives are rejected because they have no client-side Recycle Bin.
Drop URI decoding preserves native drives, UNC authority and literal filename
characters before the backend chooses whether it can recycle the item.

`tools/windows/visual/trash_smoke.py` runs the complete dock in a lab with an initially empty
bin. It verifies external document recycling, native notifications, Open Trash
and restoration with the original contents. It drags a Unicode-named folder
from Explorer onto Trash using real mouse input, verifies the shell's recycled
folder and child contents, checks default Cancel, then confirms Empty. The
entire shell inventory must belong to the fixture before an empty request.
Normal Quit checks native observer cleanup, work-area restoration and hotkey
release. The focused suite passed 62 native Windows tests and 215 Linux tests;
three Windows-only path/ABI cases are skipped on Linux. Shared drag routing
also uses real geometry tests at all four edges.

The initial captured protocol was **`win32-dropfiles`**, GTK 3's legacy Windows
path that delivers the file on release. The OLE2 milestone below now enables
drag-motion feedback as well. The driver follows current icon geometry while
running applications can reflow the dock. Its PowerShell queries create no
console windows, and captures wait for native opening animations.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\trash_smoke.py' \
  --output '\\host.lan\Data\lab\trash-acceptance'
```

References: [Recycle Bin query](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shqueryrecyclebinw),
[empty request](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shemptyrecyclebinw),
[shell change notifications](https://learn.microsoft.com/en-us/windows/win32/api/shlobj_core/nf-shlobj_core-shchangenotifyregister),
[file operation flags](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-ifileoperation-setoperationflags),
[GTK 3.24.52 Win32 drag implementation](https://gitlab.gnome.org/GNOME/gtk/-/blob/3.24.52/gdk/win32/gdkdnd-win32.c).

## Native drag feedback and internal gestures

Windows runtime preparation now enables GTK's OLE2 implementation before GDK
opens its display, using `GDK_WIN32_USE_EXPERIMENTAL_OLE2_DND`. This is the flag
provided by the verified GTK 3.24.52 runtime. The initial environment-only lab
probe and the subsequent normal runtime both reported **`ole2`** and delivered
drag-motion events. The desktop capture shows Trash's green target feedback
and the insertion space while an Explorer folder is held over the dock.

The same full application check also exercises internal gestures. Immediately
after the external applet drop, Calendar is dragged off and unpinned; Clock is
then moved before Trash, persisted, and dragged off as well. The check waits
for each actual GTK drag-end callback before starting another gesture, and
inspects the settled desktop images. Normal Quit restores the work area and
releases the hotkey. A separate native nested-menu/focus/close regression passed
with OLE2 enabled, as did 38 native drag/menu/hotkey tests and 81 focused Linux
drag/pointer tests.

This sequence exposed a shared state bug: an external applet drop can leave a
successful-drop flag without a source drag-end callback to clear it. Every new
internal drag now starts with a fresh flag, so a prior drop cannot suppress
drag-off removal. A regression test reproduces that complete event history.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\trash_smoke.py' \
  --output '\\host.lan\Data\lab\ole2-gestures' --exercise-internal
```

The acceptance baseline remains one display at 96 DPI. Other drop sources and
cross-monitor drags still need acceptance. See the
[GTK Win32 drag implementation](https://gitlab.gnome.org/GNOME/gtk/-/blob/3.24.52/gdk/win32/gdkdnd-win32.c).

## Applications menu pinning

Windows application listings have no desktop-entry file URI. Their menu rows
now offer the canonical application ID through a private GTK target restricted
to Docking's own process. On drop, the dock resolves the ID against its current
registry and pins the visible application at the insertion position. It does
not interpret the ID as a path or launch command. Existing desktop-file URI
transfers on Linux are preserved, and locked rows remain nondraggable.

`tools/windows/visual/applications_smoke.py` runs the complete application with native mouse
and keyboard input. It types Notepad into the Applications filter, drags the
actual result row onto an exposed part of the dock, and records the OLE2 target
and exact packaged application ID at the receiver. It checks persisted pinning,
verifies that the gesture did not launch the app, then clicks the new pin and
independently checks Notepad's native window and foreground ownership. After
closing its newly opened Notepad window, it reopens the menu and sends the dock
a native close request. Normal shutdown restores the original work area and
releases the Search hotkey. The final desktop PNGs were visually inspected.
Focused drag, menu and popup checks passed 137 tests on Windows, with five
POSIX-only cases skipped; all 142 passed on Linux. URI fixtures use native
file paths on each platform, while Snap, Flatpak and AppImage permission cases
remain explicitly scoped to their POSIX semantics.

The drag/reopen sequence exposed a close request lost during the applet menu's
GTK grab. Applications now prepares its menu through the dock surface's
existing Windows menu session, so native close dismisses the owned menu before
closing the dock. Applet shutdown also pops down its own menu.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\applications_smoke.py' \
  --output '\\host.lan\Data\lab\applications'
```

Exporting native application shortcuts to Explorer and cross-monitor gestures
remain follow-up work.

## Packaged application visibility

Gio can expose packaged background components as visible applications even when
their manifest declares `AppListEntry="none"`. Windows Clock 11.2607.6.0 exposed
both `Microsoft.WindowsAlarms_8wekyb3d8bbwe!App` and its `!ClockWidgets` component
with the same title and icon. The native Start list and AppsFolder listed only
the main application. Docking now respects the manifest declaration rather than
merging applications by title or guessing from their IDs. See Microsoft's
[VisualElements manifest contract](https://learn.microsoft.com/uwp/schemas/appxpackage/uapmanifestschema/element-uap-visualelements).

The Windows backend resolves the current user's registered head packages through
[FindPackagesByPackageFamily](https://learn.microsoft.com/windows/win32/api/appmodel/nf-appmodel-findpackagesbypackagefamily)
and reads their manifests outside the GTK thread. Only explicit hidden entries
override Gio visibility; missing, unreadable or malformed metadata leaves Gio's
decision intact. The shared registry retains hidden applications' canonical
metadata and launch handles, publishes the changed listing, and refreshes open
Search results. Registry notifications invalidate pending metadata; obsolete
requests and stopped workers cannot publish into a later lifecycle.

`tools/windows/visual/applications_smoke.py --check-visibility` uses the installed Clock
package and the complete dock. An explicit fixture delays the real metadata
lookup until native input has exposed both raw Gio entries in the Applications
menu and Search. Releasing it must remove the widget from open Search without
changing its query, selection or entry focus. The reopened Applications menu
must show one Clock row, whose native mouse activation must launch the main
application with its canonical ID. The probe closes its owned windows normally
and verifies work-area restoration and hotkey release. Run it with a fresh output
directory using the same lab command as the pinning check, adding
`--check-visibility`.

On the standard-user Windows 11 25H2 lab, this check passed with registry
generation 1 changing to 2 while the open query, pinned Clock selection and
entry focus remained unchanged. The menu and Search PNGs were inspected, as
was the actual launched Clock application. Both canonical launch handles
remained available, and normal shutdown restored the exact work area and
released the shortcut. The portable application/metadata selection passed
276 tests. Broader Win32 shortcut inventory still needs acceptance.

## Classic Windows application catalog

The Windows backend enumerates the current user's AppsFolder through
`SHGetKnownFolderItem`, `IShellItem` and `IEnumShellItems`. It copies canonical
application IDs, localized names and available link targets into the shared
immutable application model. Each read owns its COM apartment, interfaces and
returned strings; enumeration and property reads run outside GTK. Hidden shell
items and explicit hidden package components remain excluded. See Microsoft's
[shell enumeration handler](https://learn.microsoft.com/windows/win32/api/shobjidl_core/nf-shobjidl_core-ishellitem-bindtohandler)
and [AppUserModelID property](https://learn.microsoft.com/windows/win32/properties/props-system-appusermodel-id).

The complete native catalog supplies application-menu and Search listings while
Gio's original records and handles remain available for associations and existing
pins. Association-only clients remain usable through their actual Gio handles;
their absence from the Start menu does not make their file associations unusable.
Discovery uses canonical IDs, without merging equal names or guessing identities
from command strings. Failed catalog reads retain the last complete native
snapshot, with Gio as the initial fallback. Native shell-change subscriptions
watch the user and common Start-menu folders and AppsFolder, feeding the existing
metadata worker and debounce. The complete 30-second refresh remains a recovery
path; stop and lifecycle invalidation reject late replies. Unchanged immutable
launcher exports and their drag formats are reused only within one worker
lifetime, with file identity and metadata checks before reuse.

Application catalog and launch contracts live in
`platform/applications/catalog_backend.py` and `launch_backend.py`. The shared
registry owns generations and debounce, the metadata worker owns cancellation
and publication, and the shared launcher owns selection and process provenance.
Unix adapters retain desktop-entry/Gio/Flatpak handling; the concrete Windows
adapters, discovery, export cache, registration and process evidence live under
`platform/applications/windows/`. Win32 and COM primitives remain under
`platform/backends/windows/`.

Native catalog entries record their shell application-folder target. Activation
passes that target to Windows rather than reconstructing the shortcut command,
arguments or working directory. Packaged entries retain activation-manager
foreground delegation. Background icon extraction now also accepts classic shell
targets, including entries whose artwork is not an executable's default icon.

The standard-user lab initially exposed 36 visible Gio records versus 79 entries
in both `Get-StartApps` and AppsFolder. With native discovery, Docking's visible
catalog matches all 79 native IDs and names.
`tools/windows/visual/applications_smoke.py --classic` exercises Character Map in Search
with actual shell artwork, native
menu filtering, OLE2 drag-to-pin, persisted canonical identity, mouse activation,
window grouping and normal cleanup. The default mode still checks packaged
Notepad, and `--check-visibility` checks asynchronous hidden-component filtering.

Character Map passed this complete native UI sequence under the standard-user
Windows 11 25H2 session. Its Search row and pinned dock icon used shell artwork;
the newly opened window grouped under the exact canonical pin ID. The PNGs were
visually inspected. The hidden Clock widget regression also passed with the new
catalog, preserving the open query, selection and entry focus while metadata
arrived. Installer, multi-user and broader file-association behavior still need
native acceptance.

The shared registry publishes whether its initial optional catalog is pending.
Configured pins keep their places while discovery runs; publication fills the
existing items without replacing their running state, custom icons or applets.
Saved recent IDs are also retained until the catalog can validate them. The
application owns the model's registry subscription and releases it at shutdown.

`tools/windows/visual/classic_smoke.py` uses independent compiled GUI programs
with the same executable filename in different directories and real Start-menu
shortcuts. Native checks cover saved-pin startup, distinct grouping, activation,
Unicode shortcut names and folders, empty/quoted/Unicode arguments, updated
working directories, and a separate runtime identity for an unlisted program.
The shell records have no desktop `Exec` command, so matching uses their canonical
executable paths. ELF and PE signatures distinguish direct executable conflicts
while preserving Unix wrapper and symlink behavior. Captures and reports remain
under `artifacts/`.

Executable registration now has a shared backend contract. The Unix backend
keeps its existing desktop entries; Windows persists direct PE executables as
private Unicode `.lnk` files under Docking's configuration directory. Runtime
matching and registration use the same identity, including when MSYS2 changes
path separators or Windows returns different path casing. The registry publishes
the generated records and monitors their directory. The model and drop handler
request registration through the registry, and failed writes or catalog lookups
leave the item unpinned. Installed native shortcut pins retain their shell IDs.

`python -m tools.windows.visual.runtime_pin_smoke --output artifacts/windows/runtime-pin`
checks the actual Keep in Dock menu on an unlisted compiled program, preserves
the running item, restarts Docking in a fresh process, and launches the restored
pin with real pointer input. Independent program facts verify the executable,
empty arguments and executable working directory; native window tracking proves
one matching dock item. The check creates its pin in active UCRT64 and restores
it with ordinary desktop path semantics, then restores Explorer's work area and
removes its owned fixtures. Installer-owned portable bundles and broader file
associations still require acceptance.

An unpackaged program whose window identity is its executable basename can
appear without any installed shortcut or catalog alias. The Windows backend opts
into this executable-based fallback after normal matching fails; a validated
native image and the owning PID supply the same canonical identity used for
persistent pins. Other backends keep their existing matching policy, and
explicit hosted-application IDs keep their catalog matching.

Add `--uncataloged` to create no fixture shortcuts and use a unique executable
name. The check verifies that no catalog alias provides its runtime identity;
Windows CI combines it with the multiple-window scenario.

Add `--multiple-windows` to create two independent native top-level windows in
one process. Before and after restarting Docking, the check verifies one pinned
item with two windows, four alternating foreground changes through actual pin
clicks, and closing one window while the remaining window stays grouped and
activates from the pin. This passed for the owned unlisted executable in a
Unicode directory on Windows 11 with UAC enabled and an ordinary user. Broader
application identities, process layouts and display/session behavior still need
acceptance.

Add `--explorer-drop` to exercise an actual Explorer OLE2 file drag instead of
the application menu. The owned fixture lives under a path containing French
and Japanese characters. The check obtains Explorer's file-icon geometry through
UI Automation, sends native pointer input, verifies the decoded URI and app pin,
and retains the source executable. A fresh Docking process then launches the pin
and groups its window under the saved application ID. Both the menu and Explorer
paths passed with Unicode executable directories, including restart from an
active UCRT64 shell into ordinary desktop path semantics. Only the owned Explorer
window closes, and Explorer's original shell and work area remain intact.

Native shortcut activation now uses `ShellExecuteExW` with an optional owned
process handle. The Windows backend polls that exact handle without waiting and
releases it after exit or when its launch record is discarded. The shared
process-lifetime contract also accepts Unix `Popen` instances. When Windows
returns a process, the application launcher records which canonical launcher
started it; the matcher uses that evidence before executable-name ties. DDE or
delegated activations may return no new process and keep their native behavior.

`python -m tools.windows.visual.launch_provenance_smoke --output artifacts/windows/launch-provenance`
uses controlled registry records and independently written native shortcuts to
launch two profiles of one compiled executable under a Unicode path. The native
programs report distinct PIDs, exact empty/quoted/Unicode arguments and different
working directories. The real launcher, process-identity service and matcher
retain each profile's identity, then prune completed process records. The actual
dock's Explorer-drop/restart/launch check also passed with this shell launcher.
Explorer application-shortcut drops now preserve a binary copy of the selected
`.lnk`, including its arguments, working directory, artwork and other shell
options. The Windows registration backend stores an atomic manifest and an
immutable shortcut generation under Docking's configuration directory. Restoring
the pin does not require the original shortcut. A failed update preserves the
previous registration. File and folder shortcuts retain ordinary file behavior.

The Windows shell's `IApplicationResolver2.GetAppIDForShortcut` supplies canonical
installed and implicit application IDs. Its first-method ABI is documented by
the [System Informer implementation](https://github.com/winsiderss/systeminformer/blob/master/phlib/include/appresolverp.h).
This is an internal shell interface: unavailable implementations use a stable
Docking ID for executable shortcuts. Virtual shell shortcuts require a resolved
native identity. The registry preserves existing association handles and catalog
metadata while using the selected copy's launch options. Native icon extraction
also distinguishes shortcut generations, so replacing a selected shortcut can
refresh its artwork without changing the pin ID.

Add `--shortcut installed` or `--shortcut unlisted` to the `runtime_pin_smoke`
Explorer command to exercise shortcut import. The installed case copies a real
Start-menu shortcut, checking the shell ID against AppsFolder before the drop.
Both modes use a working directory distinct from the executable's directory and
remove the selected original before restarting Docking, then verify exact empty,
quoted and Unicode arguments, the selected working directory and native window grouping
from the launched program's independent facts.

Both shortcut modes passed on Windows 11 25H2 with real Explorer OLE2 drops,
fresh-process restoration without MSYS2, pointer activation, normal cleanup and
the original Explorer shell/work area preserved. Drop and grouped-launch PNGs
were inspected. Native shell parsing explicitly normalizes MSYS2 separators;
the resolver regression also exercises Unicode shortcut paths. Other Windows
versions, installer-owned and virtual shortcut inventories, elevation and
multi-user behavior still require acceptance.

The [ShellExecuteEx process-handle contract](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/ns-shellapi-shellexecuteinfow)
defines when the shell returns a handle and requires the caller to close it.

Selected documents use a separate native launch route. Generated executable
pins and selected shortcut pins pass literal arguments through `ShellExecuteExW`
without replacing the shortcut's stored arguments, working directory or options.
For installed classic applications, the metadata worker matches the exact native
application ID to Start-menu shortcuts and snapshots the original binary. It
rejects differing shortcuts that share an ID. Normal application activation
continues to use AppsFolder; direct AppsFolder activation and namespace-link
activation were independently observed to ignore additional file arguments.
Packaged applications receive an owned Shell item array through the activation
manager. An unavailable selected-document route never opens another application
as a fallback.

The whole-domain acceptance command runs three document-drop routes, default and
explicit associations plus an absent-association lookup, selected-shortcut export,
live catalog changes and Explorer recovery:

```sh
python -m tools.windows.visual.applications_domain_smoke \
  --output artifacts/windows/applications-explorer \
  --installed-root path/to/installed --wheel path/to/docking.whl --restart-explorer
```

Use an owned interactive test desktop: `--restart-explorer` replaces its exact
validated shell process. The command uses isolated pins and temporary application
shortcuts, creates only new association keys, records all nine check outcomes and
embeds child evidence in `result.json`. Independent cases continue after a failed
check only when owned cleanup is verified. The report binds its runtime to the
provided wheel and records shell identity and work-area restoration. Native SDK
proofs verified literal selected-file forwarding and original shortcut options;
the complete installed-wheel domain sequence remains a separate acceptance run.

The native reader uses [IShellLinkW::GetPath](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-ishelllinkw-getpath)
with stored-path flags and [IPersistFile::Load](https://learn.microsoft.com/en-us/windows/win32/api/objidl/nf-objidl-ipersistfile-load)
without resolving moved targets or launching them during catalog discovery.

## Application launcher export

The Windows metadata worker prepares standalone `.lnk` files for Applications
menu rows. AppsFolder entries keep their namespace target and application ID;
selected private shortcuts keep their original binary contents, including
arguments, working directory and shell options. Friendly Unicode filenames live
in a persistent cache so Explorer can finish copying after the drag returns.
Replacing a selected shortcut invalidates its previous export generation.

The shared menu delegates prepared file drags through `SurfaceService`.
Unix backends retain the normal GTK desktop-file URI source. The Windows surface
uses a native Shell data object with worker-prepared Shell IDList, `CF_HDROP` and
copy-effect values. It reads no launcher files in the GTK drag callback. The
handoff waits for both GTK's source end and the original Win32 OLE loop's finish;
the new Shell loop dispatches GTK events through an owned message-only timer
window. Shutdown removes pending handoffs and releases native capture and timer
resources. GTK's exported native-dialog bridge leaves Win32 input with OLE while
the timer dispatches queued GTK work, so Escape and button release are processed
by the native drag loop.
Within Docking, an exact export-file URI resolves to its current application
record, preserving canonical pinning instead of routing the launcher file to an
applet or opening it with another launcher.

```sh
python -m tools.windows.visual.application_export_smoke --output artifacts/windows/application-export
python -m tools.windows.visual.application_export_smoke --classic --output artifacts/windows/classic-export
python -m tools.windows.visual.application_export_smoke --cancel-export --output artifacts/windows/export-cancel
python -m tools.windows.visual.application_export_smoke --close-during-export --output artifacts/windows/export-close
```

These checks require installed Notepad or Character Map on the interactive lab.
They drag a real menu row into an owned Explorer folder, verify an exact file
copy without pinning or activating the app, close Docking normally and remove
its isolated cache. A separate parent process then double-clicks the copied
shortcut in Explorer and checks its native application identity, foreground
window and loaded controls before cleanup. Generated captures stay in artifacts.
Windows CI also exercises saved PIDL target readback and native `CF_HDROP`
round trips without depending on Store applications. The lifecycle checks hold
the mouse button until the native drag is active, then cancel with Escape or
close Docking and verify no file was copied.

References: [namespace shortcut targets](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-ishelllinkw-setidlist),
[Shell file formats](https://learn.microsoft.com/en-us/windows/win32/shell/clipboard),
[native drag operation](https://learn.microsoft.com/en-us/windows/win32/api/shlobj_core/nf-shlobj_core-shdodragdrop),
[GTK 3 Win32 modal integration](https://github.com/GNOME/gtk/blob/gtk-3-24/gdk/win32/gdkevents-win32.c).

## Shared application artwork

Applications menu rows, their drag images, Search results and unavailable window
previews now use the same application artwork resolver. It preserves current
painted or custom dock icons, canonical native extraction and retained Gio icons
for association-only listings. Native extraction stays on the existing worker;
completion updates the existing menu image without rebuilding rows or changing
the query, selection or focus. Menu and preview subscriptions end when their
surface closes. A completed window capture replaces fallback artwork and cannot
be overwritten by a later icon update.

`tools/windows/visual/applications_smoke.py --check-artwork` holds real native icon
extraction until the filtered menu is visible, then checks the existing row's
pixels, OLE2 drag-to-pin, activation and preview fallback. The preview capture is
explicitly unavailable for the probe's owned application. Both packaged Notepad
and `--classic` Character Map passed under the standard-user Windows 11 25H2
session, including normal cleanup. The captured menu, drag and preview images
were visually inspected; fallback previews retain transparent edges and display
one label, without the ordinary dock tooltip overlapping the preview.

`--trace-surfaces` records GTK ownership and native visible windows around the
drop. This identified the earlier floating generic icon as preview fallback:
the GDK drag window was already hidden. The fix replaces that fallback artwork
and suppresses the duplicate tooltip while retaining the existing transparent
preview design.

## Native Window Killer

`WindowsWindowPickService` supplies the shared Window Killer applet. The existing
capture overlay handles input and Escape; the Windows surface service owns its
focus and native-close behavior. After dismissal, the picker maps GTK logical
coordinates through the monitor geometry used by AppBar and Color Picker, then
uses native hit testing to resolve child controls to their application window.

Each selection records the live HWND, owning PID and process creation time.
Only the latest selection can be used, and a termination attempt consumes it.
Before termination, the backend rechecks the window/process association and
creation time using a newly opened process handle, then retains that exact
kernel handle through `TerminateProcess`. HWND or PID reuse cannot redirect the
operation to a different process. Start/stop invalidates previous selections.
Handles are released on successful operations, rejected targets and errors.

For `ApplicationFrameWindow`, a single visible foreign-process
`Windows.UI.Core.CoreWindow` identifies the hosted application. Missing or
ambiguous content is rejected. The host process, the dock itself, the current
Explorer shell, critical processes and known shell UI processes are excluded.
Termination applies to the selected application's process and all its windows.
Normal queries request limited process information and synchronization access;
termination adds `PROCESS_TERMINATE`. Windows applies the caller's effective
permissions. The backend does not elevate or change privileges.

Windows can cover a hung application with a DWM-owned `Ghost` window. The picker
uses the optional named `HungWindowFromGhostWindow` export to obtain the original
application HWND, and revalidates this association before terminating it.
This export is outside the documented Win32 contract; its presence and behavior
were verified on the Windows 11 baseline. If unavailable, ghost selection is
rejected. DWM is excluded from termination. Broader Windows versions and ghost
recovery races still need acceptance.

`tools/windows/visual/windowpicker_smoke.py` runs the complete dock and real input against
separate Win32 fixture processes. It checks repeated Escape and foreground
restoration, ignored desktop/dock clicks, overlapping windows and native child
controls, no click-through, running-entry removal, denied termination with a
responsive surviving fixture, cross-process hosted content, stale-window
rejection, a genuinely hung process with a DWM ghost, and real packaged
Calculator activation and termination. Repeated queries check process handle
counts. Native close while capture is active must release the overlay and stop
the service; surviving fixtures close normally and the exact work area returns.

The administrator lab's `psutil` enables `SeDebugPrivilege`, which can bypass a
process DACL. The denial case therefore uses a duplicate same-user impersonation
token with privileges disabled only around the actual backend call, requires
Windows error 5, then restores the original thread token. This is a real access
check against an owned fixture's deny rule; it does not change machine policy.
Actual standard-user/elevated UAC behavior remains separate acceptance work.
Both normal and `GDK_SCALE=2` runs passed on the same 96-DPI display; physical
mixed-DPI displays and secure/remote desktops remain pending.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\python.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\windowpicker_smoke.py' \
  --output '\\host.lan\Data\lab\window-picker' --calculator
```

References: [native hit testing](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-windowfrompoint),
[window ancestry](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getancestor),
[process creation times](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes),
[process access and debug privilege](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess),
[asynchronous process termination](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess).

## Native Show Desktop

Windows uses the public `IShellDispatch4.ToggleDesktop` operation. It preserves
Windows' behavior for minimized, non-minimizable and topmost windows. The backend
observes the shell desktop's actual Z order for explicit show/hide requests,
including changes made outside Docking with Win+D. Hidden native surfaces also
participate in this ordering, so the dock's AppBar proxy keeps empty desktops
observable in modes without screen reservation. State comes from the target
Windows shell's current ordering.

Explorer applies the toggle asynchronously. One lazy worker serializes requests
and waits for the observed transition before dispatching the next action.
Repeated explicit requests stay idempotent, and GTK keeps processing input and
window updates. Each COM interface belongs to that worker's calling apartment
and is released per invocation. The queue is bounded; stop cancels queued work
and interrupts transition waits. Following an Explorer restart, the worker waits
up to ten seconds for the desktop to become ready. `IShellWindows.FindWindowSW`
must confirm desktop registration before dispatch; a visible shell window alone
does not establish readiness. A pending registration never dispatches a toggle.
An accepted request returns OK, with its outcome available through the backend's
completion state.

Windows window tracking keeps covered windows in its running-app projection.
A non-minimizable window behind the raised desktop reports no overlap, allowing
Window Dodge to reveal the dock after Show Desktop. Windows that were already
minimized keep their prior state. The shell's own activation and topmost rules
still apply.

`tools/windows/visual/desktop_action_smoke.py` runs the complete application with Desktop,
Clock and System Tray, so shell commands also traverse the native notification
host. A separate fixture owns normal, fixed, pre-minimized and fixed-topmost
windows. Actual applet clicks, explicit and rapid requests, external Win+D and
service stop/restart must produce the expected native states. The test compares
window hit testing, native minimized bits, tracked visibility and retained window
IDs. Run both `none` and `window-dodge` modes; the latter verifies that covered
fixed windows cease hiding the dock. Captures wait for the dock's animation
endpoint and stable fixture pixels, since native minimized bits precede DWM's
minimize animation. The optional Explorer restart checks fresh shell identity
and post-restart actions. Shutdown restores the desktop, closes
the owned fixtures, stops the worker and restores the exact desktop work area.

On the baseline Windows build, Explorer creates its windows before its Show
Desktop commands work: early automation requests and even native Win+D can be
ignored. Registration checks alone do not establish command readiness. The
restart test first requires independent native Win+D to minimize and restore
the owned normal fixture, recording ignored startup shortcuts, then verifies
Docking's explicit requests and actual applet clicks. Immediate requests during
Explorer initialization can still fail; the backend reports an unobserved
transition and does not blindly dispatch a second toggle. Reliable application
actions during this startup interval remain a behavior limit.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\python.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\desktop_action_smoke.py' \
  --output '\\host.lan\Data\lab\desktop-actions' \
  --hide-mode window-dodge --restart-explorer
```

References: [public shell toggle](https://learn.microsoft.com/en-us/windows/win32/shell/ishelldispatch4-toggledesktop),
[Show Desktop and Minimize All semantics](https://devblogs.microsoft.com/oldnewthing/20040527-00/?p=39153),
[shell desktop identity](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getshellwindow),
[window Z order](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindow),
[desktop registration](https://learn.microsoft.com/en-us/windows/win32/api/exdisp/nf-exdisp-ishellwindows-findwindowsw),
[SDK interface layout](https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/um/ShlDisp.h).

## Native virtual desktops

`windows/workspaces.py` implements the common `WorkspaceService` used by the
existing Workspaces applet. Its worker owns COM enumeration, names, active
desktop observations and serialized switches; GTK receives immutable snapshots
and change notifications. A stopped or restarted service rejects old replies,
queued switches and deferred window activation. Failed shell reads expose an
unavailable service and recover on subsequent polls.

Window membership uses the public `IVirtualDesktopManager`. Windows tracking
retains shell-cloaked applications on other desktops, excludes them from overlap
policy, and filters established other-desktop windows when the current-workspace
preference is enabled. Unknown membership does not hide a running application.
Activating an off-desktop window switches to its desktop first, verifies the
transition, then validates and activates the native window. Close and Minimize
do not switch desktops. Ghost windows retain their original application's
workspace membership.

Windows ignores GTK 3's sticky hint. `WindowsSurfaceService` therefore delegates
the shared workspace-scope setting to the worker, which pins or unpins only the
dock's owned shell view. It never pins a shared Python application ID. GDK's
exported handle function resolves the exact surface, and native ownership is
checked before pinning. A newly mapped view is retried without discarding
workspace observations.

Desktop listing, switching and view pinning require internal shell interfaces.
The implementation checks Windows build 26100/26200 and the identified 24H2
interface IDs before calling their vtables. Other builds advertise these
capabilities as unavailable. The public API alone does not provide enumeration
or switching; a missing shell interface does not produce a fabricated desktop.
Native acceptance currently covers Windows 11 25H2 build 26200.8037. Windows
11 24H2, other patch levels, Explorer restart during a switch, remote sessions,
and multiple displays still need desktop acceptance.

`tools/windows/visual/workspace_smoke.py` runs the complete dock with four explicitly
labeled fixture windows on two desktops. The fixtures assign a registered
application ID to exercise real common matching and grouping; they are not
Calculator processes. Native applet clicks, wheel and menu selection, external
Win+Ctrl+Left, current-workspace filtering, off-desktop foreground activation,
external rename/create/removal and stale-ID rejection passed at GTK scale 1/2.
An additional run verified native unpinning hides the dock on the other desktop,
restoring its original desktop reveals it, and repinning makes it visible across
desktops again.
Both runs closed the fixtures normally, removed only their own new desktops,
restored the original desktop list and active desktop, preserved Explorer, and
restored the exact work area. Unit tests cover ABI ownership, unavailable builds,
worker isolation, recovery, cancellation, filtering and deferred actions.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\python.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\workspace_smoke.py' \
  --output '\\host.lan\Data\lab\workspaces'
```

References: [public membership contract](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nn-shobjidl_core-ivirtualdesktopmanager),
[PowerToys membership and pinned-window handling](https://github.com/microsoft/PowerToys/blob/main/src/modules/launcher/Wox.Plugin/Common/VirtualDesktop/VirtualDesktopHelper.cs),
[24H2 shell interface definitions](https://github.com/MScholtes/VirtualDesktop/blob/master/VirtualDesktop11-24H2.cs).

## Native screen color picking

The Windows screen service maps GTK logical root coordinates into physical
monitor coordinates, then reads one pixel with `GetDC`/`GetPixel`. Each request
releases its device context on the calling thread, including failed reads.
`CLR_INVALID` returns an unknown reading; black and white remain valid colors.
`DwmFlush` lets removal of the caller's translucent capture overlay reach
composition before sampling. Construction and start/stop do not acquire screen
resources, and stopped or earlier-lifetime results are rejected.

The shared capture helper assigns its dock parent and asks the surface backend
to prepare focus before the input grab. The Windows surface reuses its existing
focus session, restores the previous app after selection or Escape, and dismisses
an active capture before native dock close. Temporary capture HWNDs cannot
replace the remembered source application during repeated picks. Other apps
that acquire foreground retain it through the existing ownership checks.

`tools/windows/visual/colorpicker_smoke.py` runs the complete dock beside a separate process
painting six known color patches. Independent desktop pixels establish the
fixture before real input opens the shared overlay and selects each patch.
Every color, including black and white, must match the tooltip, Windows
clipboard readback and saved preferences. The test checks source focus after
every pick and cancellation, no clicks leaking into the fixture, the native
Copy and Show Hex menu actions, and 300 repeated reads with stable GDI handles.
`--shutdown-overlay` closes the dock while a capture is active and requires
normal exit, fixture shutdown and exact work-area restoration.

The baseline is one 1280×800 Windows display at 96 DPI. A second run forces
`GDK_SCALE=2` and exercises actual doubled GTK surfaces and input coordinates;
it does not establish mixed-DPI or multiple-monitor acceptance. Those displays,
HDR, protected content, remote sessions and secure desktops remain follow-up
work. Negative-origin and scaled monitor arithmetic has unit coverage.
The `windows-colorpicker-baseline-verified` and
`windows-colorpicker-scale2-verified` runs passed and exited normally; their
overlay and menu PNGs were inspected.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\python.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\colorpicker_smoke.py' \
  --output '\\host.lan\Data\lab\colorpicker' --shutdown-overlay
```

References: [screen device contexts](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getdc),
[pixel values and invalid reads](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-getpixel),
[same-thread context release](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-releasedc),
[caller composition flush](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/nf-dwmapi-dwmflush).

## Native Notifications

Notifications uses one `NotificationsBackend` contract and shared GTK history,
badge, wheel navigation and menus. `notifications/linux.py` owns dunst/GNOME
commands and the bounded D-Bus monitor. `state.py` contains only values and
presentation. Initial discovery, polling, controls and cleanup run on one
`SerialWorker` thread. Old state replies cannot replace newer controls, while
events drained by an earlier read still reach the same applet lifetime. Stop
cancels pending work and prevents late replies from reviving timers or history.

The Windows implementation reads `UserNotificationListener` with scoped WinRT
objects and cancellable asynchronous enumeration. It checks permission before
and after enumeration: revoked access is an unknown pending count, never an
empty inbox. New, replaced and reused notification IDs update shared history;
unchanged notifications do not reappear after clearing local history. History
keeps at most 40 entries. Windows Clear Notifications is offered only when all
pending entries fit, validates the exact displayed revision, and removes those
IDs individually so a newly arriving notification is not included in a global
clear. Clearing local history does not dismiss Windows notifications.

Do Not Disturb uses the shell's **private** `IQuietHoursSettings` interface and
the effective quiet-hours WNF profile, validated on Windows 11 25H2. This is
separate from the documented presentation/first-login notification-state query.
Unknown profiles or ABI failures disable the control while notification history
can remain available. Changes wait for both the selected and effective profiles;
the shell can accept a setter before publishing its new selection. A remembered
Alarms Only selection is retained when disabling and reenabling. Automatic rules
can keep the effective state paused even after a manual off request; the applet
reports that outcome rather than claiming the effective state changed.

The native dock check uses actual pointer/menu/wheel input, generated toasts
under an owned AppUserModelId, and the real Windows Settings Do Not Disturb
toggle as an independent readback. It exercises history, the badge, local clear,
subsequent events, and normal worker cleanup at GTK scale 1 and 2. The doubled
scale run also verifies Unicode text, native removal of one displayed ID while
retaining other notifications, and normal close during a held native read.
The fixture removes only its own
notification group and registration and restores the original quiet-hours
selection. It does not clear preexisting Windows notifications.

```powershell
python -m tools.windows.visual.notifications_smoke --output C:\\lab\\notifications
```

Revoked-access and shell-failure behavior has policy/lifecycle test coverage;
native permission prompts, managed policy, automatic-rule transitions, other
toast providers, Windows versions and user sessions still need acceptance.

References: [notification listener and access revocation](https://learn.microsoft.com/en-us/windows/apps/develop/notifications/app-notifications/notification-listener),
[notification-state query semantics](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/ne-shellapi-query_user_notification_state),
[quiet-hours interface definition](https://github.com/bitdisaster/windows-focus-assist/blob/master/lib/quiethours.idl),
[effective-profile reader](https://github.com/bitdisaster/windows-focus-assist/blob/master/lib/focus-assist.cc).

## Shared WhatsApp and Telegram browser host

Both applets use the same `BrowserBackend` contract and retain their separate
service profiles. The WebKitGTK implementation lives in
`docking/applets/webapp/linux.py`; the Windows implementation lives in
`docking/applets/webapp/windows.py`. The service modules select only their URL,
session name and public page landmarks. Windows does not import the WebKitGTK
host or its D-Bus notification adapter.

The native host uses Microsoft Edge WebView2 and the public SDK COM interfaces.
Install the WebView2 Runtime, then install the pinned x64 loader from the
repository root with the Windows Python interpreter:

```powershell
python -m tools.windows.webview2_setup
```

The installer verifies the official SDK archive checksum and extracts only
`WebView2Loader.dll` and its license into the per-user runtime directory.
`--package` accepts an already downloaded SDK archive. The running applets do
not download dependencies. `DOCKING_WEBVIEW2_LOADER` can select another absolute
loader path. A missing loader or runtime exposes an unavailable state.

WebView2 interfaces stay on the GTK UI thread's STA as required by Microsoft.
Environment/controller creation, script execution and profile clearing use
native completion callbacks without nested message loops. Directory creation
runs in a background worker. Native delegate references retain Python callbacks
until their final COM release, including replies arriving after a browser stops.

Each service stores its browser profile below its own
`docking_data_dir()/<service>/webview2` directory. Hiding a window retains the
controller and session; stopping releases the native host. Disconnect first
navigates away from the service, clears the WebView2 profile asynchronously,
then reloads. The page bridge accepts bounded status, badge and activation
events only from the service's HTTPS origin. Camera and microphone requests
require an explicit dialog while the browser is visible. External navigation
requires a user gesture and uses the shared target opener.

The owned desktop exercise uses generated responses at the service origin,
without an account, conversations, contacts or a live QR code:

```powershell
python -m tools.windows.visual.webapp_smoke --service whatsapp --output C:\Temp\docking-whatsapp-check
python -m tools.windows.visual.webapp_smoke --service telegram --output C:\Temp\docking-telegram-check
```

Native exercises on Windows 11 Pro 25H2 verified hidden startup and badge delivery,
actual pointer and Unicode input, Escape/hide/reopen, controller restart with
cookie/local-storage/IndexedDB persistence, generated login/ready transitions,
malformed page traffic, confirmed disconnect and normal shutdown. The realized
decorated window fits the native work area at GTK scale 1 and 2. These generated
pages establish host behavior; signed-in service behavior and physical media
devices remain separate acceptance work.

`--notifications` additionally exercises native Windows toasts and Action Center
history. Both engines implement the shared `NotificationBackend` contract;
Windows publishes through worker-owned WinRT interfaces and routes immutable
activation tokens back to the browser's STA. Notification text stays within the
native notification adapters. The page bridge carries badge/status data only.

Each browser profile has a stable Windows application identity so native
notification preferences survive restarts. An owned profile mutex prevents a
second publisher from replacing its active COM route. Closing the browser
removes its owned notifications and revokes the live activation server while
retaining the user's notification settings. Disconnect failures also complete
their callbacks; callbacks that can open a dialog run after leaving the native
WebView2 event stack and are discarded after the browser stops.

The lab's WebView2 155 runtime loses the active tag's close routing if any
superseded same-tag notification object is released. The adapter retains those
objects in a flat group until the active tag closes, disconnects or stops. This
keeps native and DOM close behavior correct without changing the page's public
Notification API. A long-lived tag retains its replacement objects until that
group ends; sustained memory use remains acceptance work.

Generated native pages verified Windows history delivery and actual card clicks
dispatching the page's notification click handler and reopening the matching
browser at GTK scale 1 and 2. Native lifecycle runs also verified tag replacement,
programmatic close, active-notification shutdown and worker ownership. Repeated
replacement/close cycles verify that each DOM notification closes once and that
every native close handler is released after its group ends.
Some shell cards in this VM are absent from UI Automation. In that case,
`--notification-point X Y` supplies a card position inspected visually; the tool
captures that card and still requires the actual DOM click and browser activation.
`--popup-activation` is an alias for the native notification exercise.

Persistent service-worker notifications remain unsupported by the current host.
Microsoft documents `NotificationReceived` as a
[non-persistent notification callback](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/overview-features-apis#web-notification-handling).
An actual generated service worker registered, activated and called
`registration.showNotification()` successfully in the lab's WebView2 155 runtime,
but no native callback or matching Windows toast arrived. Page `new Notification()`
acceptance does not establish this worker path or signed-in service behavior.

The worker fixture serves actual JavaScript through the SDK's resource-source
filter, including service-worker requests. The diagnostic records worker replies,
native callbacks and Windows history separately. It requires native delivery by
default. `--observe` records unavailable delivery without a command failure;
`result.json` still reports `native_delivery: false` and `ok: false`.

```powershell
python -m tools.windows.visual.webapp_worker_smoke --output C:\\lab\\worker-observation --observe
```

### Experimental browser companion

The Windows Qt companion implements the shared `BrowserBackend` in a separate
CPython process. Set `DOCKING_WEBENGINE_PYTHON` to that interpreter before starting
Docking to select it for WhatsApp and Telegram. The normal Windows host uses
WebView2 when this variable is absent. This remains an experimental engine path;
installer integration, signed-in services, media, real-service downloads,
sustained resource use, broader input methods and physical display acceptance
remain pending.

Install the companion dependencies in a separate CPython x64 environment:

```powershell
py -3.13 -m venv "$env:LOCALAPPDATA\Docking\WebEngine"
& "$env:LOCALAPPDATA\Docking\WebEngine\Scripts\python.exe" -m pip install -r requirements-windows-browser.txt
$env:DOCKING_WEBENGINE_PYTHON = "$env:LOCALAPPDATA\Docking\WebEngine\Scripts\python.exe"
```

The pinned Qt 6.12 metapackage supplies the matching WebEngine/PDF wheels.
The companion omits Qt's extra user-agent product token while preserving the
actual Chromium version and platform. The real WhatsApp page rejects that extra
token with an update screen; the normalized identity reaches its login page.
Browser commands and validated badge/status events use bounded UTF-8 packets on
private pipes. A Windows job owns the companion and Chromium descendants before
configuration permits browser startup. Normal close releases the profile and
toasts; a shutdown deadline releases the job for an unresponsive companion.
Notification text and Qt objects stay in that process. WinRT publication has one
owned worker; real engine notifications route OS activation back to page or
service-worker handlers.

The public Badging API bridge runs in the main JavaScript world. Its narrow
status channel reaches Qt through an isolated world; subframes get no injected
bridge. Profile origin checks, navigation and device consent remain active.
Disconnect holds the profile lock while destroying the old browser, removing
its stored data off the UI thread and starting a fresh companion. The parent
holds profile ownership across that replacement, and old native processes have
exited before stored files are removed.

`tools.windows.visual.webengine_smoke` serves generated HTTPS pages at the actual service
origins using a profile-local test certificate. It creates no OS certificate trust
and requires no account. Native runs on Windows 11 Pro 25H2 with Qt 6.12 verified
both service profiles, real Unicode input, worker tag replacement and card clicks,
page notification close, cookie/local-storage/IndexedDB/worker removal on
disconnect, and normal or held-UI process-tree shutdown while GTK stayed responsive.
`--notification-point X Y` supplies a visually inspected
Action Center card position. `--hang-at-end` explicitly holds the Qt UI thread to
exercise process-tree cleanup while the GTK loop continues running.

```powershell
python -m tools.windows.visual.webengine_smoke --python "$env:DOCKING_WEBENGINE_PYTHON" --output C:\\lab\\qt-browser --notification-point 1100 155
```

`tools.windows.visual.webengine_dock_smoke` runs the actual dock with both service
applets active. It drives native dock clicks, Open/Hide and Reload menus,
generated login/ready transitions, worker notification activation and the real
Disconnect confirmation. It checks that the matching applet receives and clears
notification attention, that profiles isolate cookies/local storage/IndexedDB,
and that disconnect preserves the other service's data and badge. Normal dock
shutdown must reap both companion trees and restore the Windows shell state.
The generated fixture uses no signed-in accounts or private service data.

```powershell
python -m tools.windows.visual.webengine_dock_smoke --python "$env:DOCKING_WEBENGINE_PYTHON" --service whatsapp --output C:\\lab\\qt-dock --notification-point 1100 155
```

`tools.windows.visual.webengine_live_smoke` uses the production entry point and fresh
profiles against the actual service. It verifies anonymous login while hidden,
native window focus, Escape/show retention, reload and owned process-tree exit.
WhatsApp and Telegram passed on Windows 11 Pro 25H2 with Qt 6.12. The tool records
coarse lifecycle states, takes no screenshots and removes the real service profile
after the owned browser has exited. It does not sign in or establish signed-in
messaging/media acceptance.

```powershell
python -m tools.windows.visual.webengine_live_smoke --python "$env:DOCKING_WEBENGINE_PYTHON" --service whatsapp --output C:\\lab\\qt-live
```

`--diagnostic` selects a separate observer that records only coarse public
landmark/compatibility flags. It exports no page text, pairing codes, cookies or
account data. `--show-before-login` isolates mapping effects during diagnosis;
`--chromium-user-agent` is an explicit diagnostic identity experiment. Normal
acceptance uses none of those flags. The current Qt wheel advertises WebM support
but no H.264/AAC MP4 support; a complete media-capable engine remains required
before this experimental path can become the default.

The generated companion checks also passed after enabling UAC and rebooting the
lab into an ordinary medium-integrity, non-admin session: WhatsApp at GTK scale 1
and Telegram at GTK scale 2 both verified real worker cards, tag replacement,
native card clicks, Unicode input, session clearing and normal process cleanup.
The original dock was restored and its Search, tray ownership and placement
checked in that session.

A separate full Edge 156 control decoded an actual generated H.264/AAC clip,
but registered-worker notifications never reached Windows history, despite
native Allow input and a granted permission state. The actual browser token was
medium integrity and non-admin. Removing the lab's job object or changing the
worker's `silent` option did not restore delivery. A debugger-free fresh
default-profile control decoded the clip and activated the worker but did not
obtain notification permission, so it does not establish default-profile
notification behavior. The original browser profile was restored unchanged.
These controls do not yet identify a media-capable replacement for the default
browser backend.

The Qt companion uses the native Windows Save dialog and retains progress
controls for each accepted download. Pause/Resume control the actual engine
request; Cancel or closing its progress window cancels the transfer. Docking
shutdown and Disconnect reject pending destination dialogs and cancel active
requests before destroying the browser profile.

`tools.windows.visual.webengine_download_smoke` drives real download links, destination
dialogs and transfer buttons. It checks overwrite refusal, HTTP and blob
attachments with Unicode names and byte-exact contents, pause/resume, partial-file
removal, disconnect during a paused transfer, and normal process-tree shutdown
with either a paused transfer or a pending native Save dialog. It removes only
its own generated Downloads subdirectory. Saving complete pages is not supported
by this companion yet. Signed-in exports, CDN/network faults, managed policies,
concurrent-profile downloads and physical display behavior remain acceptance work.

These generated download cases passed on Windows 11 Pro 25H2 with Qt 6.12 for
WhatsApp at GTK scale 1 and Telegram at GTK scale 2. Both shutdown cases exited
normally in under a second and left none of their native browser processes.

```powershell
python -m tools.windows.visual.webengine_download_smoke --python "$env:DOCKING_WEBENGINE_PYTHON" --service whatsapp --output C:\\lab\\qt-downloads
```

### Web-app downloads

The native host preserves WebView2's default download manager, destination
selection and progress dialog. Generated HTTP attachments and browser `blob:`
links have native acceptance coverage, including Unicode filenames and contents,
existing-file preservation, duplicate names, public SDK pause/resume and closing
the browser while a download is paused. Native process observation verifies that
the stopped browser leaves no WebView2 processes for its owned profile. These
checks passed for WhatsApp at GTK scale 1 and Telegram at GTK scale 2.

`tools/windows/visual/webapp_download_smoke.py` drives the actual download link with
native pointer input, observes SDK download states on the browser's owning STA,
checks the saved bytes in the real Downloads directory, and captures the native
progress UI. It uses random owned filenames and removes only those files.

```sh
python -m tools.windows.visual.webapp_download_smoke --service whatsapp --output C:\\lab\\webapp-downloads
```

Signed-in service exports, real CDN/network failures, managed download policies,
alternate default handlers and broader display/session behavior remain acceptance
work. The generated pages use no account, messages or contacts.

## Native Music controls

Music has one `MusicBackend` contract for player discovery, metadata, transport
controls, volume and cleanup. `music/linux.py` owns the existing MPRIS,
playerctl and Rhythmbox implementations; `state.py` contains platform-neutral
session values and presentation. The shared GTK applet does not interpret
D-Bus names or run player commands. Registry icon candidates come from the
backend without IO on GTK.

Discovery, artwork and all player controls run through the shared `SerialWorker`.
It creates one IO thread on first use and keeps related calls in order. The
constructor displays loading state without waiting for discovery. Old poll
replies cannot undo actions or revive a stopped/restarted applet. Volume bursts
coalesce before execution; explicit transport clicks retain their order. Stop
signals cancellation and lets native cleanup finish on the IO thread without
another GTK callback. The thread exits before normal process termination.

Windows uses the public `GlobalSystemMediaTransportControlsSessionManager` API.
It prefers a playing session and uses the system's current session for ties.
The backend retains COM session identities, assigns opaque snapshot tokens and
revalidates both session membership and source application before acting. A
closed or replaced source cannot fall back to another player. Supported
Play/Pause/Next/Previous commands come from the session's current capabilities.
Connecting metadata can be temporarily unavailable; that does not erase known
transport capabilities. Thumbnail streams are bounded to 4 MiB, copied into
owned bytes, closed on the worker and decoded by the shared artwork resolver.
Changed artwork invalidates the cache even when track text stays the same.
Native drive paths, Unicode filenames and file URIs use the shared path parser.

Per-player volume uses Core Audio output-session groups. Attribution checks the
live creator application, executable identity or a validated explicit window
application ID. Cross-process groups use the documented creator identity.
Ambiguous or unavailable attribution disables volume controls. The displayed
level is the group's highest stream level; an explicit change updates that same
group. An opaque digest of endpoint/session-instance IDs rejects a changed
group before writing. Partial failures roll back owned changes where the level
still matches the attempted write. Endpoint master volume, mute and the source
player's own volume property are preserved.

`tools/windows/visual/music_smoke.py` runs the full dock with actual icon clicks,
keyboard menu selection and wheel input. Its separate `music_fixture.py` owns a
real Windows MediaPlayer, two generated PCM tracks, late-published PNG covers
and SMTC transport callbacks. The fixture reports MediaPlayer's actual playback
state and selected source. Native normal and doubled-scale runs verified
pause/resume, next/previous, changed metadata/cover pixels, both volume menu
items, wheel changes with Core Audio readback, exact fixture-level restoration
and normal shutdown. `--shutdown-read` holds a completed native read before its
reply to verify responsive close and worker-owned cleanup. These checks also
verify unchanged master/source volume and Explorer/work-area restoration.

```sh
python -m tools.windows.visual.lab --share /path/to/shared --wait 1 start \
  'C:\msys64\ucrt64\bin\python.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\music_smoke.py' \
  --output '\\host.lan\Data\lab\music'
# Repeat with --env GDK_SCALE=2 and --shutdown-read using a fresh output.
```

Third-party/browser publishers, multiple simultaneous sessions, vanished creator
processes, device/default changes, hardware output effects, remote sessions and
other Windows versions still need broader acceptance. Unattributed/shared
output groups do not gain a master-volume shortcut. The existing default-player
launch path remains part of the shared application-registry workflow.

References: [media-session manager](https://learn.microsoft.com/en-us/uwp/api/windows.media.control.globalsystemmediatransportcontrolssessionmanager),
[session capabilities](https://learn.microsoft.com/en-us/uwp/api/windows.media.control.globalsystemmediatransportcontrolssessionplaybackcontrols),
[Core Audio creator-process semantics](https://learn.microsoft.com/en-us/windows/win32/api/audiopolicy/nf-audiopolicy-iaudiosessioncontrol2-getprocessid),
[session-instance identity](https://learn.microsoft.com/en-us/windows/win32/api/audiopolicy/nf-audiopolicy-iaudiosessioncontrol2-getsessionidentifier).

## Native screenshots

Screenshot uses one `ScreenshotBackend` contract for discovery, full/window/region
capture, region coordinates and cleanup. The existing Unix CLI and portal
implementation is isolated in `screenshot/linux.py`; `state.py` contains only
modes, coordinates and owned pixel results. The shared applet owns asynchronous
requests, cancellable delays, region selection and captured-image presentation.

Windows captures the virtual desktop or a physical pixel rectangle through
Pillow's Windows screen capture. Window mode reuses the WinRT graphics capture
implementation at source resolution. The shared surface contract provides the
window active before a dock menu took focus; Windows validates its HWND/PID and
normalizes DWM ghosts before capture. Menu restoration retains a bounded history
of live identities so a destroyed preview cannot hide the preceding source.
A stale source is rejected rather than capturing a recycled HWND.

GTK Win32 rounds scaled mouse coordinates to logical integers. The Windows
backend observes button coordinates on the owned region overlay before GTK
translation, then maps them to fractional logical coordinates for the shared
selection UI. It leaves native input dispatch intact and removes the observer
before destroying the overlay. Physical capture bounds therefore retain odd
pixels at doubled GTK scale. Desktop reads validate geometry and a 128 MiB pixel
budget; window frame pools and GPU devices close on their owning worker. Stop
invalidates old results, cancels pending delays and reads, and lets worker
cleanup finish after GTK exits.

Captured pixels go to the image clipboard and a monitor-sized preview. Copy
repeats that operation. Save As uses GTK's native file chooser (Windows
`IFileDialog`) and writes a PNG on a worker using a temporary file followed by
replacement of the exact confirmed path. The chooser supplies a `.png` default
name; the applet does not silently change the chosen overwrite target. No
screenshot file is created until Save As is accepted.

`tools/windows/visual/screenshot_smoke.py` uses a separate six-color fixture, actual
dock clicks and menu input, independent Windows Forms clipboard reads and
independently decoded PNG pixels. It checks source-resolution window capture,
exact region bounds across a color boundary, clipboard persistence, Escape
cancellation, the three-second delay and normal-close cleanup. Use a fresh
output directory for each run. `--shutdown-region` closes with the input overlay
active; `--shutdown-window` holds a real captured frame until normal dock close
and checks worker-owned stream/device cleanup. `GDK_SCALE=2` exercises doubled
GTK scale; this does not establish physical mixed-DPI monitor acceptance.
Normal and doubled-scale runs passed, including an odd-device-pixel region,
Unicode UNC export, active-overlay close and close during a native window read.
The same 136 focused tests passed on Linux and native Windows.

Multiple physical monitors, negative origins and monitor changes during
selection still need native desktop acceptance. HDR, protected content,
uncached minimized windows, secure/remote sessions and other Windows versions
remain acceptance limits, alongside the capture indicator behavior documented
for window previews.

References: [Pillow desktop capture](https://pillow.readthedocs.io/en/stable/reference/ImageGrab.html),
[GTK native file chooser](https://docs.gtk.org/gtk3/class.FileChooserNative.html),
[GTK Win32 button-coordinate translation](https://github.com/GNOME/gtk/blob/gtk-3-24/gdk/win32/gdkevents-win32.c).

## Native session idle time

`WindowsIdleService` reads `GetLastInputInfo` and `GetTickCount64` through the
Windows backend. The native structure uses fixed-width fields on both Python
architectures. Tick arithmetic handles the 32-bit input timestamp wrapping;
failed queries and impossible pre-boot durations return an unknown reading.
Stopping the service rejects further reads and results from an earlier lifetime.
The implementation installs no input hooks and creates no polling worker.

The API reports input in the calling Windows session. Its timestamp can be
non-monotonic, including for injected input. A 32-bit timestamp cannot establish
an uninterrupted idle history longer than its roughly 49.7-day cycle. Remote
sessions, suspend/resume and an actual long-running desktop remain acceptance
work; rollover arithmetic has unit coverage.

`tools/windows/visual/idle_smoke.py` runs the complete application with Desk Presence and
Clock, the supported 30-second threshold and the normal 10-second poll. A
separate input process establishes activity, then the desktop remains untouched
until the applet reports away. A separately compiled C# reader checks the native
idle values. The test verifies both counters, the presence pulse stopping and
resuming, and reactivation after new input. Native menu navigation selects the
one-minute threshold and the saved configuration retains it. Normal shutdown
restores Explorer's work area exactly; stopped and restarted service reads are
also checked. No X11 transport is imported. The `windows-idle-native-menu-verified`
run passed every stage and exited with code 0; its active, away and menu PNGs
were inspected.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\python.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\idle_smoke.py' \
  --output '\\host.lan\Data\lab\idle'
```

References: [session-specific input time and timestamp caveats](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo),
[native uptime](https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/nf-sysinfoapi-gettickcount64).

## Native keyboard lock indicators

The Windows Caps Lock backend binds `GetKeyState` lazily and reads on the GTK
message-loop thread. Only the low toggle bit supplies an indicator; the high
pressed-key bit does not. Zero means a valid off state. Native binding failures
return the shared unavailable state. The existing one-second applet poll updates
the icon and tooltip; no keyboard hook or separate polling worker is installed.

`tools/windows/visual/capslock_smoke.py` runs the complete application beside an owned
Notepad document. A separate process sends native lock-key input while Notepad
keeps foreground focus. All four Caps Lock/Num Lock combinations must reach the
background applet through its normal poll and agree with an independent Windows
Forms reader. Actual scan-code typing and the saved Notepad contents verify
lowercase and uppercase behavior. The native menu shows both indicators and
invokes Refresh Now. Cleanup restores the original lock states, closes the
owned document and dock normally, and checks the exact desktop work area.
The `windows-capslock-native-first` run passed every stage and exited with code 0;
its indicator and menu PNGs were inspected.

Physical keyboard LEDs, remote sessions and secure-desktop transitions remain
acceptance work.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\python.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\capslock_smoke.py' \
  --output '\\host.lan\Data\lab\capslock'
```

Reference: [toggle bits and message-queue semantics](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getkeystate).

## Keyboard menus and close handling

GDK Win32 records keyboard grabs without transferring native focus. The Windows
surface temporarily makes the dock focusable and presents it before a root menu
opens. When the menu closes, it restores the original focus policy. An
out-of-context `EVENT_SYSTEM_FOREGROUND` observer retains the preceding window:
a non-activating right click can clear `GetForegroundWindow` before GTK delivers
the button event. Deferred restoration checks both HWND and process identity,
and only runs while the dock still owns foreground focus. Search, Preferences
and an outside-click target keep their newly acquired focus.

GTK suppresses a parent's delete event while another toplevel has a menu grab.
A `SetWindowSubclass` callback on the dock queues dismissal of its owned menus
before requesting the normal GTK close. It forwards other native messages.
The surface removes both native hooks and pending callbacks during shutdown;
the GDK event dispatcher remains unchanged. Search presentation also cancels
an existing GTK menu grab before showing its typing surface.

`tools/windows/visual/menu_smoke.py` drives the complete application with native keyboard and
mouse input. Run its two shutdown modes in separate fresh jobs. The baseline
checks navigate three menu levels, return to a parent with Left, dismiss with
Escape and restore Notepad focus, open Search by its hotkey while a submenu is
active, type into Search, invoke Search with a mouse menu action, invoke
Preferences with Enter, repeat open/dismiss cycles, and check outside-click and
hover focus. Keyboard Quit and native WM_CLOSE with an active menu must both
exit normally, restore the exact desktop work area and release the Search
hotkey. GTK observations run on the main loop, and native HWND observations
independently verify focus and destruction. The lab still uses one display,
96 DPI and the US keyboard layout.

Both `windows-menus-keyboard-verified` and `windows-menus-native-verified` passed
these stages and exited with code 0. The nested-menu, Search and close-menu
PNGs were inspected. The focused menu, runtime and Search selection also passed
102 tests under native Windows Python, with one POSIX filename case skipped.

```sh
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --timeout 180 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\menu_smoke.py' \
  --output '\\host.lan\Data\lab\menus-keyboard' --shutdown keyboard
python tools/windows/visual/lab.py --share /path/to/shared/artifacts --timeout 180 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows\visual\menu_smoke.py' \
  --output '\\host.lan\Data\lab\menus-native-close' --shutdown native
```

References: [window subclass callbacks](https://learn.microsoft.com/en-us/windows/win32/api/commctrl/nf-commctrl-setwindowsubclass),
[removing a subclass](https://learn.microsoft.com/en-us/windows/win32/api/commctrl/nf-commctrl-removewindowsubclass),
[foreground event observation](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwineventhook).

## Implementation and acceptance ledger

| Area | Implementation | Required Windows evidence |
| --- | --- | --- |
| Runtime/bootstrap | Windows signals, paths, DLL bootstrap and backend selection | Native source launch and focused Windows tests passed |
| Application metadata | Gio Win32 discovery, packaged activation and background shell icons | Packaged Notepad discovery, pinning, icon and foreground launch verified; broader Win32 inventory pending |
| Basic dock | Shared GTK/Cairo UI with Windows surface adapter and scoped native menu focus | Startup, transparency, hover, mouse/keyboard/nested menus, dismissal, Preferences and both keyboard Quit and native close during a menu grab verified on the baseline desktop |
| Session idle | Native Windows implementation of the common IdleService | Desk Presence real idle/input transitions, independent native time readings, counters, pulse, threshold menu and persistence verified; remote sessions, suspend/resume and long uptime pending |
| Caffeine | Shared Inhibitor contract with Unix cookie/child and Windows power-request implementations; worker-owned requests and cleanup | Native clicks, menu duration/persistence, countdown, independent display/system request counts and normal/blocked-acquisition shutdown verified; failed requests expose unavailable state; physical sleep/display effects, policy/Modern Standby, sessions and other versions pending |
| Window Killer | Native implementation of WindowPickService with process-instance validation | Actual input, child/overlapping/hosted windows, real Calculator, denied access, stale targets, DWM ghost, handle ownership and active-capture shutdown verified at GTK scale1/2; optional ghost export across Windows versions, UAC and broader display/session acceptance pending |
| Show Desktop | Native Explorer implementation of DesktopActionService with serialized actions and live shell ordering | Applet/explicit/rapid/external toggle, minimized/fixed/topmost windows, retained running entries, Window Dodge, worker lifecycle and shell recovery have desktop acceptance tools; broader displays, Windows versions, focus and policy behavior remain pending |
| Virtual desktops | Windows implementation of WorkspaceService and surface scope, with build-gated shell ABI and public window membership | Native applet click/menu/scroll, external keyboard switch, names/list changes, current-workspace filtering and cross-desktop foreground activation verified at GTK scale 1/2; worker lifecycle, membership ownership and unknown builds tested; other versions, Explorer transition races, sessions and displays pending |
| Screen color picking | Native Windows implementation of the common ScreenCaptureService | Six actual desktop colors, clipboard/preferences, Copy/Show Hex, repeated pick/cancel focus, active-capture close and GDI lifetime verified at normal and doubled GTK scale; mixed-DPI/displays, HDR, protected/remote/secure content pending |
| Music | Shared MusicBackend and serial worker; Unix MPRIS/CLI and native Windows media-session/Core Audio implementations | Native clicks, menu transport, late artwork/metadata changes, per-player volume readback and normal/held-read close verified at GTK scale 1/2; third-party/browser/multiple sessions, output attribution/device changes and broader versions/sessions pending |
| Notifications | Shared NotificationsBackend, history and serial worker; Unix dunst/GNOME/D-Bus and native Windows listener/quiet-hours implementations | Native click/menu DND confirmed by Windows Settings at GTK scale 1/2; generated toasts, Unicode history, badge, wheel, local clear, targeted removal and normal/held-read shutdown verified; permission/managed policy, automatic rules, other providers and broader versions/sessions pending |
| WhatsApp and Telegram | Shared BrowserBackend and NotificationBackend with separate service profiles, WebKitGTK/D-Bus and native WebView2/Windows toast implementations | Generated native pages verify startup/badges, actual Unicode input, window sizing at GTK scale 1/2, persistence, disconnect and normal close; native Windows history, real card-click/DOM activation, tag replacement, programmatic close, repeated group cleanup and active-toast shutdown verified for both profiles; native HTTP/blob downloads, Unicode, collision handling, pause/resume and paused-download process cleanup verified; persistent workers unsupported by WebView2; experimental Qt companion verifies native worker/tag/click/close, Unicode input, process-replacement disconnect and normal/forced process-tree cleanup; sustained memory use, signed-in services, CDN/network/policy and physical media remain pending |
| Screenshots | Shared ScreenshotBackend with Unix tool/portal and native Windows pixel implementations; shared selection, clipboard, preview and PNG export | Full-screen/window/region pixels, native Save As, clipboard persistence, Escape, delay and normal-close cancellation verified at normal and doubled GTK scale, with exact odd-device-pixel bounds and worker-owned capture cleanup during normal close; physical displays, HDR/protected content and broader sessions remain |
| Keyboard lock indicators | Shared LockKeyBackend with X11 and Windows implementations | Normal polling while Notepad has focus, all four toggle combinations, independent Windows Forms state, typed letter case, native menu and exact restoration verified; physical LEDs, remote and secure desktops pending |
| Running applications | Native snapshots, per-window application identity, ghost projection, matching and taskbar actions | Indicators, activate, minimize, restore and Close verified with Notepad; Calculator/Clock sharing one host PID retain separate pins/actions/previews; same-HWND/PID application-ID replacement rejects stale actions/pixels; real DWM ghost deduplication, minimize/thumbnail restore, moved bounds, native Close/Wait and recovery verified with owned native fixtures; broader hosted apps, recovery races and multi-window cycling pending |
| Drag and drop | GTK OLE2 runtime, native URI file drops and shared internal gestures | Explorer target feedback and recycle drop, internal reorder/persistence, drag-off immediately after an external applet drop, and Applications-menu pinning/launch/close verified; broader display/source cases pending |
| Desktop integration | Native AppBar and overlap services | All four edges, taskbar coexistence, maximized-window placement and work-area restoration, six visibility modes and focus-preserving edge reveal verified on the baseline; fullscreen/restart/pressure follow-up pending |
| Displays | Per-monitor-v2 startup and live Windows scaling | Native 100% → 125% transition, taskbar separation, reservation, Search and Color Picker accepted on one display; multiple monitors, negative origins, mixed DPI, other scales and display removal pending |
| Previews and overlays | Native Windows graphics capture and asynchronous UI | Occluded/live/resized source pixels and Notepad hover/focus/minimize/activation verified; menu/Search previews and typing focus verified; ghost/original surface switching, stale-target rejection and recovery captures verified; static/minimized source reuse across thumbnail sizes and real recovered hover images verified; shared memory budget and source-resolution limits tested; indicator, outlines, first uncached minimized source and broader display/content cases pending |
| Search and file workflows | Native drive/UNC targets, shell associations, path Search, shared folder UI, native global hotkey, shell handoff and shared canonical/live artwork | Unicode drive/SMB metadata, previews, default-opened Notepad contents, folder stack, native Search Copy Path and Explorer file drop verified; native binding/capture/rebind/cancel, conflict, disable/enable and shutdown release verified; asynchronous native/painted artwork and same-process startup focus verified; regular shell activation with independent input, arbitrary executable/shortcut identity and broader keyboard/file/network acceptance pending |
| System applets | Native Core Audio volume, shell Recycle Bin, aggregate Battery/power, Network monitoring/Wi-Fi/VPN/settings, Bluetooth classic/LE inventory/discovery/pairing/GATT/audio, Mic Shield capture, Cam Shield activity/policy and native notification-area backends | Volume controls, Windows keys, Sound settings and audio verified; Trash notification/recycle/restore/empty and cleanup verified; Battery native absence/oracle, overlays, Power Settings and shutdown verified, with labeled state-rendering fixtures; Network native wired metadata/counters, actual SMB traffic, selection/overlays/settings and cleanup verified; Wi-Fi profiles/radio/permissions exercised with labeled API fixtures; native IKEv2 VPN/profile menus/dialer/data/disconnect verified; Mic Shield actual two-input WASAPI capture/PID/mute/non-default input/poll/Settings verified with generated audio; Bluetooth native factories/watchers/absence/settings, classic/LE/audio menu ownership and twelve pairing-dialog cases verified with labeled API fixtures; Cam Shield native generated-frame/two-client capture/PID/stop/idle/pulse/Settings, policy Lock/Unlock, denied new capture, continued existing capture, backend restart/recovery and restored capture verified; physical cameras and actual UAC consent remain pending; System Tray real legacy/GUID icons, startup seed, pixels, double-click/activation/native menus, updates/hide/show/delete, Task Manager and Explorer handoff verified; two actual Explorer restarts with tray/AppBar/Trash recovery and exact handoff verified; SDK icon rectangles, movement/fallback and provider menu placement verified; independent x86/x64 ANSI/Unicode/GUID/version/structure-size matrices, rich hover, keyboard actions and guarded focus return verified locally and in hosted CI; broader providers, restart races, policy restrictions and physical display/DPI acceptance pending; physical Bluetooth, BLE/discovery/pairing/connections, physical wireless, global networking controls, other VPN authentication/server policies, physical batteries/accessories, other backends and broader devices pending |
| Portable applets | AI Usage native update lock and hooks; Ambient GStreamer/WASAPI playback; native Session, Power Profiles and hardware Brightness backends; shared rendering | AI Usage fixture ingestion and UI verified; Ambient all seven OGG sounds, both noise modes, native controls, volume/mute/stop and actual audio capture verified on the baseline; Session native lock, sign out, restart and power off verified with external OS state; sleep unsupported on this VM; Power Profiles native modes/effective state, Energy Saver override, custom-plan behavior and cleanup verified; Brightness unavailable state and labeled DDC/WMI fixture controls verified on the VM, physical brightness pending; full catalog, other audio devices and live provider sessions pending |
| Packaging and updates | Unsigned preview ZIP and per-user NSIS setup with versioned runtimes and stable launchers; shared update notifications open the release page for manual installation | Native installation, complete payload hashes, installed desktop, in-use rejection, upgrades, startup-choice preservation/disabling and uninstall with preferences/unknown files retained verified; interrupted-copy ownership tested; CI requires a distinct older-version upgrade, three measured starts and a two-minute idle session; native acceptance of these new checks is pending; signing is deferred |
| Existing platforms | Initial regression tests pass | Keep Linux/BSD tests and desktop behavior working |

The first milestone is a visibly working basic dock. Native services and applet
backends follow that proof. Each feature must have its own Windows evidence
before its capability is advertised or its status is marked complete.
