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

The native catalog import audit currently loads 64 of 65 applet classes.
System Tray remains blocked by its eager XEmbed/GdkX11 import.
Importing a class does not verify its controls or native system backend; the
catalog acceptance work remains open.

```sh
python tools/windows_lab.py --share /path/to/shared/artifacts run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows_window_smoke.py' \
  --output '\\host.lan\Data\lab\windows-native'
```

![Native Notepad icon and window menu on Windows 11](../images/windows-window-controls.png)

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
Ordinary Windows documents no longer trigger Unix executable-launcher creation.

`windows_file_smoke.py` creates isolated drive and SMB-share fixtures with
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
python tools/windows_lab.py --share /path/to/shared/artifacts --timeout 240 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows_file_smoke.py' \
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

![Windows folder stack with image thumbnail](../images/windows-folder-stack.png)

![Windows drive path and text preview in Search](../images/windows-file-search.png)

![File dragged from Explorer and pinned to Docking](../images/windows-file-drop.png)

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
the foreground keyboard layout. F12 is reserved by Windows for debuggers and
is rejected. The XDG `NUM` modifier has no Win32 registration equivalent.
The desktop acceptance uses the lab's US keyboard layout; broader keyboard
layouts, dead keys, remote sessions and Windows-reserved combinations still
need acceptance.

`windows_shortcut_smoke.py` runs the complete application composition and GTK
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

![Search opened by the native Windows hotkey](../images/windows-search-hotkey.png)

![Windows shortcut conflict in Preferences](../images/windows-shortcut-conflict.png)

```sh
python tools/windows_lab.py --share /path/to/shared/artifacts --timeout 240 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows_shortcut_smoke.py' \
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

`windows_search_icon_smoke.py` runs the full application with isolated settings.
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
python tools/windows_lab.py --share /path/to/shared/artifacts --timeout 240 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows_search_icon_smoke.py' \
  --output '\\host.lan\Data\lab\search-icons'
```

![Native Windows application artwork in Search](../images/windows-search-icons.png)

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
match the requested Search window. An unrelated application taking focus,
hiding/destroying Search, successful activation or a 750 ms expiry cancels the
request. Foreground changes after success remain under user control.

`windows_focus_smoke.py` runs the complete application and activates Search as
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

```sh
python tools/windows_lab.py --share /path/to/shared/artifacts --timeout 180 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows_focus_smoke.py' \
  --output '\\host.lan\Data\lab\focus-start' --origin start
```

Use `--origin taskbar` and `--origin desktop` for the other startup cases.

References: [foreground activation](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow),
[GUI-thread observations](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getguithreadinfo).

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

`windows_aiusage_smoke.py` runs the full application with AI Usage pinned and
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
python tools/windows_lab.py --share /path/to/shared/artifacts --timeout 180 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows_aiusage_smoke.py' \
  --output '\\host.lan\Data\lab\aiusage'
```

![AI Usage consuming a fixture through its native Windows hook](../images/windows-aiusage.png)

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

`windows_ambient_smoke.py` runs the normal dock and uses native clicks, menu
navigation and wheel events. `windows_vm_audio.py` records the actual QEMU audio
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
python tools/windows_lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows_ambient_smoke.py' \
  --output '\\host.lan\Data\lab\ambient-acceptance'
python tools/windows_vm_audio.py --share /path/to/shared/artifacts \
  --name ambient-acceptance --timeout 300
```

![Ambient playing White Noise on the native Windows desktop](../images/windows-ambient.png)

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

`windows_volume_smoke.py` runs the complete dock with Ambient as a real audio
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
python tools/windows_lab.py --share /path/to/shared/artifacts --wait 1 start \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows_volume_smoke.py' \
  --output '\\host.lan\Data\lab\volume-acceptance'
python tools/windows_vm_audio.py --share /path/to/shared/artifacts \
  --name volume-acceptance --timeout 240
```

![Volume applet and native Windows Sound settings](../images/windows-volume-settings.png)

References: [default audio endpoint](https://learn.microsoft.com/en-us/windows/win32/api/mmdeviceapi/nf-mmdeviceapi-immdeviceenumerator-getdefaultaudioendpoint),
[master volume scalar](https://learn.microsoft.com/en-us/windows/win32/api/endpointvolume/nf-endpointvolume-iaudioendpointvolume-setmastervolumelevelscalar),
[endpoint mute](https://learn.microsoft.com/en-us/windows/win32/api/endpointvolume/nf-endpointvolume-iaudioendpointvolume-setmute),
[Windows Settings URIs](https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-settings).

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

`windows_menu_smoke.py` drives the complete application with native keyboard and
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

![Keyboard selection in a nested Windows menu](../images/windows-menu-keyboard.png)

```sh
python tools/windows_lab.py --share /path/to/shared/artifacts --timeout 180 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows_menu_smoke.py' \
  --output '\\host.lan\Data\lab\menus-keyboard' --shutdown keyboard
python tools/windows_lab.py --share /path/to/shared/artifacts --timeout 180 run \
  'C:\msys64\ucrt64\bin\pythonw.exe' \
  '\\host.lan\Data\repo\tools\windows_menu_smoke.py' \
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
| Running applications | Native snapshots, matching and taskbar actions | Indicators, activate, minimize, restore and Close verified with Notepad; grouping/cycling have unit coverage and need multi-window desktop checks |
| Desktop integration | Native AppBar and overlap services | All four edges, taskbar coexistence, maximized-window placement and work-area restoration, six visibility modes and focus-preserving edge reveal verified on the baseline; fullscreen/restart/pressure follow-up pending |
| Displays | Pending | Multiple monitors, negative origins, DPI changes and display removal |
| Previews and overlays | Native Windows graphics capture and asynchronous UI | Occluded/live/resized source pixels and Notepad hover/focus/minimize/activation verified; menu/Search previews and typing focus verified; indicator, outlines and broader display/content cases pending |
| Search and file workflows | Native drive/UNC targets, shell associations, path Search, shared folder UI, native global hotkey, shell handoff and shared canonical/live artwork | Unicode drive/SMB metadata, previews, default-opened Notepad contents, folder stack, native Search Copy Path and Explorer file drop verified; native binding/capture/rebind/cancel, conflict, disable/enable and shutdown release verified; asynchronous native/painted artwork and same-process startup focus verified; regular shell activation with independent input, arbitrary executable/shortcut identity and broader keyboard/file/network acceptance pending |
| System applets | Native Core Audio master volume | Volume wheel/click controls, limits, external Windows keys, persisted level display, native Sound settings and actual audio verified on the baseline; device routing and other native applet backends pending |
| Portable applets | AI Usage native update lock and hooks; Ambient GStreamer/WASAPI playback; shared rendering | AI Usage fixture ingestion and UI verified; Ambient all seven OGG sounds, both noise modes, native controls, volume/mute/stop and actual audio capture verified on the baseline; full catalog, other audio devices and live provider sessions pending |
| Packaging and updates | Pending | Install, launch, update and uninstall on a clean Windows VM |
| Existing platforms | Initial regression tests pass | Keep Linux/BSD tests and desktop behavior working |

The first milestone is a visibly working basic dock. Native services and applet
backends follow that proof. Each feature must have its own Windows evidence
before its capability is advertised or its status is marked complete.
