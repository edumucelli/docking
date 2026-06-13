# Docking on Wayland

This document captures the current understanding of what Wayland means for
`docking`, how the project moved its original X11-centric implementation behind
backend services, and which Wayland paths are currently available or still
compositor-dependent.

It is intended as an engineering document for support boundaries, backend
sequencing, and remaining gaps. The user-facing support summary lives in the
README and website.

## Scope

This document focuses on:

- the practical impact of GNOME and Ubuntu moving away from Xorg sessions
- what still works for GTK applications on Wayland
- which Docking features used to be blocked by X11-only assumptions and which
  ones still require backend-specific services
- the difference between:
  - features that work with ordinary GTK on Wayland
  - features that require compositor protocols
  - features that use the GNOME Shell bridge
  - features that deliberately fall back to reduced mode
- migration strategies for remaining compositor-specific work

This document records both completed migration work and planned follow-up work.

## Date Context

The terms "Ubuntu 25" and "GNOME 49" get conflated easily. The actual timeline
matters:

- `Ubuntu 25.04` was released on `April 17, 2025` and ships `GNOME 48`
- GNOME announced on `June 8, 2025` that the `GNOME 49` X11 session is
  disabled by default upstream
- Ubuntu announced on `June 10, 2025` that `Ubuntu 25.10` removes the
  `Ubuntu on Xorg` session and makes GNOME/Mutter Wayland-only

So the immediate pressure point for Docking is not Ubuntu 25.04; it is the
GNOME/Ubuntu stack from `GNOME 49` / `Ubuntu 25.10` onward.

## Executive Summary

There are several different targets people may mean when they say "support
Wayland":

1. Run the X11 backend under `XWayland`
2. Run the GNOME Shell bridge backend on GNOME / Mutter
3. Run the native layer-shell backend on compositors that expose dock/taskbar
   protocols
4. Run compositor-specific rich backends (COSMIC, Hyprland, KWin)
5. Run the reduced backend when compositor integration is unavailable

These are not equivalent.

Short version:

- X11 remains the full-featured backend and also works as an XWayland
  compatibility path where the desktop exposes enough X11 state
- GNOME / Mutter support is handled through a companion GNOME Shell bridge
  extension because Mutter does not expose the common third-party dock protocols
- COSMIC has the richest native Wayland support: running indicators, window
  actions, workspaces, overlap-driven autohide, and preview image capture
  all work through public COSMIC protocols
- Hyprland has native IPC-based window tracking, actions, and workspaces
- KWin / KDE Plasma 6 has native layer-shell placement and workspace support
  via D-Bus; window tracking is limited (KWin 6 does not expose a public
  window-list protocol)
- wlroots-style native Wayland support depends on layer-shell and
  `wlr-foreign-toplevel-management` protocol availability
- reduced mode keeps the dock visible on unsupported Wayland sessions while
  intentionally disabling taskbar/window-management features

## Backend Selection

Docking selects a session backend at startup based on the GTK display type
and the desktop environment. The selection logic lives in
`docking/platform/backends/selection.py`.

For support and compatibility reports, open right-click -> **Diagnostics** in
Docking. The dialog shows the selected backend, session variables, feature
availability, optional helper tools, monitors, and a copyable Markdown report.

**Auto-detection order on native Wayland:**

1. COSMIC (if `XDG_CURRENT_DESKTOP=COSMIC`)
2. Niri (if `XDG_CURRENT_DESKTOP=niri`)
3. KWin / KDE Plasma 6 (if `XDG_CURRENT_DESKTOP=KDE`)
4. Generic layer-shell (if the compositor advertises `zwlr_layer_shell_v1`)
5. GNOME Shell bridge (if the bridge D-Bus service is available)
6. Reduced (fallback)

**Manual override** via `DOCKING_BACKEND`:

```bash
DOCKING_BACKEND=x11          # Force X11 backend
DOCKING_BACKEND=cosmic       # Force COSMIC backend
DOCKING_BACKEND=kwin         # Force KWin backend
DOCKING_BACKEND=wayland      # Force generic layer-shell backend
DOCKING_BACKEND=niri         # Force Niri IPC backend
DOCKING_BACKEND=hyprland     # Force Hyprland backend
DOCKING_BACKEND=gnome-shell  # Force GNOME Shell bridge backend
DOCKING_BACKEND=reduced      # Force reduced (launcher-only) backend
```

**On X11 and XWayland** (`GDK_BACKEND=x11`), the X11 backend is always selected
unless `DOCKING_BACKEND` is explicitly set to a different backend.

## Compositor Support

### COSMIC

**Backend:** `wayland/cosmic_session.py`
**Auto-detected:** Yes
**Status:** Richest native Wayland support

COSMIC exposes the most complete set of public protocols for a third-party dock:

| Capability | Status | Protocol |
| --- | --- | --- |
| Edge placement / exclusive zone | ✓ | `zwlr_layer_shell_v1` via `gtk-layer-shell` |
| Running indicators / active state | ✓ | `zcosmic_toplevel_info_v1` + `ext_foreign_toplevel_list_v1` |
| Window actions (activate, close, minimize, maximize, fullscreen, sticky, move-to-workspace) | ✓ | `zcosmic_toplevel_manager_v1` |
| Workspace listing / switching | ✓ | `ext_workspace_manager_v1` |
| Overlap-driven autohide | ✓ | `zcosmic_overlap_notify_v1` |
| Window previews | ✓ | `ext_foreign_toplevel_image_capture_source_manager_v1` + `ext_image_copy_capture_manager_v1` |
| Applets | ✓ | Backend-neutral applets work; portal-backed color picker and screenshot |

Launch: `DOCKING_BACKEND=cosmic python3 run.py`

Known quirks: `zcosmic_workspace_manager_v2` is advertised but does not send
workspace events with the vendored XML bindings; `ext_workspace_manager_v1`
is used instead. A single cffi `AttributeError` at startup from deprecated
protocol events does not affect runtime.

---

### KWin / KDE Plasma 6

**Backend:** `kwin/session.py`
**Auto-detected:** Yes
**Status:** Layer-shell placement + workspaces; window tracking is limited

KWin 6 does not expose `wlr-foreign-toplevel-management`,
`ext-foreign-toplevel-list`, or `org_kde_plasma_window_management` to
third-party Wayland clients. Its scripting API does not provide a usable
D-Bus bridge for window listing.

| Capability | Status | Mechanism |
| --- | --- | --- |
| Edge placement / exclusive zone | ✓ | `zwlr_layer_shell_v1` via `gtk-layer-shell` |
| Running indicators / active state | ~ | AT-SPI window tracking (best-effort) |
| Window actions | ~ | Limited by window tracking availability |
| Workspace listing / switching | ✓ | KWin D-Bus `VirtualDesktopManager` |
| Overlap-driven autohide | ✗ | Not available |
| Window previews | ✗ | Not available |
| Applets | ✓ | Backend-neutral applets work |

Launch: `DOCKING_BACKEND=kwin python3 run.py`

---

### Hyprland

**Backend:** `wayland/hyprland_ipc.py`
**Auto-detected:** Via `HYPRLAND_INSTANCE_SIGNATURE`
**Status:** IPC-based window tracking, actions, and workspaces

Uses Hyprland's event socket (`.socket2.sock`) for live state and short-lived
command-socket calls for actions. Command calls are intentionally short-lived
to avoid stalling the compositor.

| Capability | Status | Mechanism |
| --- | --- | --- |
| Edge placement / exclusive zone | ✓ | `zwlr_layer_shell_v1` via `gtk-layer-shell` |
| Running indicators / active state | ✓ | Event socket (`openwindow`, `closewindow`, `activewindowv2`) |
| Window actions (focus, close) | ✓ | Dispatch commands via command socket |
| Workspace listing / switching | ✓ | IPC workspace events and commands |
| Overlap-driven autohide | ~ | Geometry available from IPC; not yet wired |
| Window previews | ✗ | Not available |
| Applets | ✓ | Backend-neutral applets work |

Launch: `DOCKING_BACKEND=hyprland python3 run.py`

---

### Niri

**Backend:** `wayland/niri_session.py` + `wayland/niri_ipc.py`
**Auto-detected:** Yes, when desktop detection reports Niri
**Status:** IPC-based window tracking, actions, and layer-shell placement

Uses Niri's JSON IPC socket (`$NIRI_SOCKET`) for live window state and
short-lived request/response calls for actions. The event stream delivers
full current state up-front followed by incremental events — no polling needed.

| Capability | Status | Mechanism |
| --- | --- | --- |
| Edge placement / exclusive zone | ✓ | `zwlr_layer_shell_v1` via `gtk-layer-shell` |
| Running indicators / active state | ✓ | Event stream (`WindowsChanged`, `WindowOpenedOrChanged`, `WindowFocusChanged`) |
| Window actions (focus, close, fullscreen) | ✓ | Action requests (`FocusWindow`, `CloseWindow`, `FullscreenWindow`) via IPC |
| Workspace listing / switching | ✓ | Workspace protocol when available; window workspace state via IPC |
| Overlap-driven autohide | ✗ | Not available (tiling compositor) |
| Window previews | ✓ | `ScreenshotWindow` IPC action → temp-file PNG capture |
| Color Picker | ✓ | Native `PickColor` IPC request |
| Applets | ✓ | Backend-neutral applets work |

Niri is a tiling compositor without a traditional minimize concept, so
`minimize_all` returns `UNSUPPORTED`. Close, focus, and fullscreen actions
work for windows tracked via IPC. The ``OverviewOpenedOrClosed`` event is
tracked and exposed via ``NiriWindowService.is_overview_open``.

Launch: `DOCKING_BACKEND=niri python3 run.py`

---

### Generic wlroots (Sway, river, labwc, Wayfire)

**Backend:** `wayland/session.py` (WaylandLayerShellSessionBackend)
**Auto-detected:** Yes (if compositor advertises `zwlr_layer_shell_v1`)
**Status:** Layer-shell placement + `wlr-foreign-toplevel-management`

| Capability | Status | Mechanism |
| --- | --- | --- |
| Edge placement / exclusive zone | ✓ | `zwlr_layer_shell_v1` via `gtk-layer-shell` |
| Running indicators / active state | ✓ | `zwlr_foreign_toplevel_manager_v1` (if advertised) |
| Window actions (activate, close, minimize) | ✓ | Protocol-dependent; actions gated on capability flags |
| Workspace listing / switching | ✓ | `ext_workspace_manager_v1` (if advertised) |
| Overlap-driven autohide | ✗ | Not available (no overlap protocol on generic wlroots) |
| Window previews | ✗ | Not available |
| Applets | ✓ | Backend-neutral applets work; portal-backed color picker and screenshot |

Falls back to reduced mode when `pywayland` is not installed, the Wayland
connection fails, or the compositor does not advertise the relevant globals.

Launch: `DOCKING_BACKEND=wayland python3 run.py`

---

### GNOME / Mutter

**Backend:** `gnome/session.py` (GnomeShellBridgeSessionBackend)
**Auto-detected:** Via D-Bus bridge availability
**Status:** Bridge-based window/workspace state; no native layer-shell

GNOME/Mutter does not expose `zwlr_layer_shell_v1` or
`wlr-foreign-toplevel-management` to third-party clients. The GNOME Shell
bridge extension exports window/workspace state and actions over D-Bus.
The GTK dock window positions itself via Mutter's `move_resize_frame()`
through the extension.

| Capability | Status | Mechanism |
| --- | --- | --- |
| Edge placement / exclusive zone | ✓ | Mutter `move_resize_frame()` via Shell extension |
| Running indicators / active state | ✓ | D-Bus bridge (window list, active window) |
| Window actions (activate, minimize, close) | ✓ | D-Bus bridge |
| Workspace listing / switching | ✓ | D-Bus bridge |
| Overlap-driven autohide | ✗ | Not available |
| Window previews | ✗ | Not available from bridge |
| Applets | ✓ | Backend-neutral applets work |

**Installation:**

```bash
tools/gnome_bridge.sh install
tools/gnome_bridge.sh enable
tools/gnome_bridge.sh status
```

Launch: `DOCKING_BACKEND=gnome-shell python3 run.py`

Caveats: GNOME Shell may cache GJS modules for an extension UUID. After
editing `extension.js`, a logout/login may be required. The bridge D-Bus
name may not appear immediately after install; a Shell restart or
logout/login may be needed.

**Not yet a full GNOME dock:** the visible dock is still the Python/GTK
window (not a Shell actor), so overview integration, shell-level
autohide/dodge, and panel-style placement are not available. Full GNOME
dock parity would require a GNOME Shell frontend (comparable to
Dash to Dock), not just the bridge.

---

### Reduced (Fallback)

**Backend:** `reduced/session.py`
**Auto-detected:** Yes (when no other backend is available)
**Status:** Launcher shelf only; no taskbar/window-management features

| Capability | Status |
| --- | --- |
| Edge placement / exclusive zone | ✗ (normal GTK window) |
| Running indicators / active state | ✗ |
| Window actions | ✗ |
| Workspace listing / switching | ✗ |
| Overlap-driven autohide | ✗ |
| Window previews | ✗ |
| Pinned launchers / app launching | ✓ |
| Backend-neutral applets | ✓ |
| Renderer, themes, zoom | ✓ |

Launch: `DOCKING_BACKEND=reduced python3 run.py`

## Feature Support Matrix

| Feature | X11 | COSMIC | Hyprland | Niri | KWin 6 | wlroots | GNOME Shell bridge | Reduced |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Edge reservation (struts/exclusive zone) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ |
| Running indicators | ✓ | ✓ | ✓ | ✓ | ~ | ✓ | ✓ | ✗ |
| Active window tracking | ✓ | ✓ | ✓ | ✓ | ~ | ✓ | ✓ | ✗ |
| Window actions (focus, close, minimize) | ✓ | ✓ | ✓ | ✓¹ | ~ | ✓ | ✓ | ✗ |
| Window previews | ✓ | ✓ | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ |
| Workspaces | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ |
| Overlap-driven autohide | ✓ | ✓ | ~ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Pointer barriers | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Background blur hint | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Window Killer | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Pinned launchers | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Backend-neutral applets | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Color Picker | ✓ | ✓ (portal) | ✓ (portal) | ✓ (IPC) | ✓ (portal) | ✓ (portal) | ✗ | ✗ |
| Screenshot | ✓ | ✓ (portal) | ✓ (portal) | ✓ (portal) | ✓ (portal) | ✓ (portal) | ✓ (portal) | ✓ (portal) |

¹ Niri is a tiling compositor — focus and close work, minimize is unsupported.

Legend: ✓ = supported, ~ = partial, ✗ = unavailable

## X11 / XWayland Compatibility

The X11 backend remains the full-featured default. It also works as an
XWayland compatibility path by forcing `GDK_BACKEND=x11` inside a Wayland
session.

```bash
GDK_BACKEND=x11 .venv/bin/docking
```

**What works:** dock placement, struts, launchers, running indicators (for
X11/XWayland apps), window actions, previews, workspaces, overlap-driven
autohide, pointer barriers, blur hints, all applets.

**What is limited in XWayland mode:**
- Running indicators only work for X11/XWayland-visible apps, not native
  Wayland apps
- Window previews only work for X11/XWayland windows
- Multi-monitor cursor-follow fails (GDK pointer polling returns stale
  monitor-0 coordinates)
- Color Picker samples the X11 root window (black on Wayland)
- Window Killer depends on X11 global window inspection
- XWayland rendering can be unstable in long runs (GTK/XWayland/Mutter
  presentation failures for transparent RGBA dock windows)

**Verification:**
```bash
echo "$XDG_SESSION_TYPE"  # should be "wayland"
echo "$WAYLAND_DISPLAY"    # should be set
echo "$DISPLAY"            # should be set (XWayland available)
```

## Compatibility Test Log

> **This section is the raw test data backing the per-compositor summary above.**
> Entries are detailed, timestamped records of real-world test runs. The
> summary tables in the "Compositor Support" section above are derived from
> these entries plus code inspection.

This section is the working record for real-world test results.

Its purpose is operational, not aspirational:

- record exactly what was tested
- separate observed behavior from assumptions
- keep a running list of what works, what partly works, and what fails
- build the evidence needed for a later README section about using Docking in
  Wayland environments

When adding a new entry:

- include the exact desktop/session details
- include the exact launch command
- prefer concrete observations over interpretation
- mark unknown items as `not tested` rather than guessing

### Status Labels

Use these labels consistently:

- `works`
- `partly works`
- `fails`
- `not tested`

### Test Entry Template

Copy this block for each test run:

```md
#### Test: <short name>

- Date:
- Distro:
- Desktop:
- Session type:
- Compositor:
- Display variables:
- Launch command:
- Result summary:

| Area | Status | Notes |
| --- | --- | --- |
| Launch/startup | not tested | |
| Edge placement | not tested | |
| Stays on top | not tested | |
| Screen-edge reservation / struts | not tested | |
| Hover and click interaction | not tested | |
| Menus | not tested | |
| Drag and drop | not tested | |
| Running-window tracking | not tested | |
| Minimize / restore / focus cycling | not tested | |
| Window previews | not tested | |
| Applets (general) | not tested | |
| Autohide | not tested | |
| Pointer barriers | not tested | |
| Overlap-based hide modes | not tested | |
| Multi-monitor behavior | not tested | |
| Suspend / resume recovery | not tested | |
| Notes / anomalies | not tested | |
```

### Current Test Entries

#### Test: Ubuntu 25 XWayland smoke test

- Date: 2026-04-08
- Distro: Ubuntu 25.x
- Desktop: Ubuntu GNOME
- Session type: Wayland
- Compositor: GNOME Shell / Mutter
- Display variables: `XDG_SESSION_TYPE=wayland`, `WAYLAND_DISPLAY=wayland-0`, `DISPLAY=:0`
- Launch command: `GDK_BACKEND=x11 DISPLAY=:0 DOCKING_LOG_LEVEL=DEBUG python3 run.py`
- Result summary: Docking launches as an X11 client under XWayland in a GNOME
  Wayland session. Basic dock placement and basic interaction work in smoke
  testing, but X11-dependent desktop integration is only partial.

| Area | Status | Notes |
| --- | --- | --- |
| Launch/startup | works | User reports Docking launches successfully under `GDK_BACKEND=x11` in a Wayland session with XWayland available. |
| Edge placement | works | Dock appears at the bottom of the screen in smoke testing. |
| Stays on top | not tested | |
| Screen-edge reservation / struts | works | In `hide_mode=none`, windows are pushed up rather than overlapping the dock in the Ubuntu 25 XWayland setup. |
| Hover and click interaction | works | Basic interaction appears correct in smoke testing. Tooltips also work. |
| Menus | works | User reports menus work in the Ubuntu 25 XWayland setup. |
| Drag and drop | works | User reports drag and drop works in the Ubuntu 25 XWayland setup, including folder drop stacks. |
| Running-window tracking | partly works | Expected to work for X11/XWayland-visible apps, but not reliably for native Wayland apps. |
| Minimize / restore / focus cycling | fails | User reports minimize, restore, and focus cycling do not work in the Ubuntu 25 XWayland setup. For native Wayland apps, left-click may launch a new instance instead of applying the configured running-app action. |
| Window previews | fails | No reliable previews for native Wayland apps in XWayland compatibility mode. Current preview capture is X11/XID-based. Future support would require compositor protocols or compositor-specific integration, but feasibility depends on the compositor. |
| Applets (general) | partly works | Screenshot applet now works via the XDG desktop portal backend. Brightness control still depends on local system permission/setup for the backlight device even when the backend logic is correct. Keyboard layout switching works after adding a GNOME-specific backend. Window Killer does not work in the tested Ubuntu 25 XWayland setup because it depends on X11/Wnck global window inspection. Color Picker does not work correctly because it samples black from the X11 root window instead of the real Wayland desktop contents. Other applets still need broader verification. |
| Autohide | works | User reports auto hide works in the Ubuntu 25 XWayland setup. |
| Pointer barriers | not tested | |
| Overlap-based hide modes | partly works | User reports overlap-based hide modes work partially. Hide-on-maximized does not appear to work in the Ubuntu 25 XWayland setup. |
| Multi-monitor behavior | fails | User reports Docking stays on the first monitor and does not automatically follow the cursor in the Ubuntu 25 XWayland setup. Debug logging shows both monitors are detected correctly, but GDK pointer polling stays stuck on monitor 0 coordinates, so active-display monitor follow never switches. An experimental Xlib root-pointer fallback detected monitor changes better, but was not reliable enough to keep because the dock could disappear instead of landing visibly on the target monitor. |
| Suspend / resume recovery | not tested | |
| Notes / anomalies | partly works | Ubuntu 25 may not ship any of Docking's older screenshot CLI backends by default, so the XDG desktop portal backend is important for screenshot support in Wayland sessions. Running indicator dots depend on X11 window tracking, so they are not reliable for native Wayland apps even when Docking itself is launched through XWayland. The configured left-click action for running apps also depends on that same running-state detection, so native Wayland apps may open a new instance on left click instead of toggling or cycling windows. Window previews also depend on X11 window IDs and foreign-window pixel capture, so native Wayland window previews are unavailable in this mode; future support would require compositor protocols or compositor-specific integration rather than an XWayland-only workaround, and feasibility depends on the compositor. Multi-monitor cursor-follow also fails in the tested XWayland setup: Docking remains on the first monitor instead of following the cursor. Debug logging shows monitor enumeration is correct, but both GDK pointer query paths return stale monitor-0 coordinates, so the current active-display polling logic never resolves the cursor to monitor 1. An experimental Xlib root-pointer fallback improved pointer detection but was not stable enough for placement and was removed after causing the dock to disappear instead of showing reliably on the target monitor. Brightness control is not blocked by Wayland protocol limitations in the same way previews and task tracking are; after switching Docking to a `brightnessctl`-style backend, the remaining failure was plain user permission denial on the backlight device, while `sudo brightnessctl` succeeded. Window Killer is another X11/Wnck-dependent feature: it relies on a global overlay, root-relative click coordinates, and Wnck window geometry to identify the clicked target, so it does not work reliably for Wayland-native windows in this mode. Color Picker also depends on X11-era screen capture assumptions: it samples pixels from the X11 root window, which does not represent the real Wayland desktop scene here, so picks return black instead of the visible screen color. Future support would require portal/compositor-based capture instead of X11 root-window reads. Separate from those feature gaps, XWayland rendering itself is currently unstable in long runs: the dock can stop receiving draw callbacks while hover, tooltip, and other logic continue, leaving either a frozen last frame on screen or an effectively invisible dock while tooltip popups still appear. A traced run also reported `compositor_active=False`, which is relevant because the dock is an RGBA/compositor-managed window and presentation failures in this environment are plausibly below Docking's state machine. |

#### XWayland freeze findings

Later investigation refined the XWayland behavior beyond the initial smoke
test.

Observed facts:

- Docking can keep processing hover, tooltip, autohide, and click logic after
  the visible dock stops updating.
- In failing runs, the last successful draw callback may be far earlier than
  the visible symptom. A later screenshot of a half-zoomed icon was confirmed
  to be a stale last frame, not evidence that animation was still progressing.
- Tooltip popups are separate windows, so they can continue to appear even when
  the main dock surface has stopped repainting.
- Some failing runs suggested compositor trouble as well: the runtime snapshot
  recorded `xwayland=True` with `compositor_active=False`.

#### Test: COSMIC native Wayland

- Date: 2026-06-08
- Distro: Ubuntu (COSMIC-based)
- Desktop: COSMIC
- Session type: Wayland
- Compositor: cosmic-comp
- Display variables: `XDG_SESSION_TYPE=wayland`, `WAYLAND_DISPLAY=wayland-1`, `XDG_CURRENT_DESKTOP=COSMIC`
- Launch command: `DOCKING_BACKEND=cosmic DOCKING_LOG_LEVEL=DEBUG python3 run.py`
- Result summary: Docking launches as a native Wayland COSMIC client using layer-shell
  for surface placement, `ext_foreign_toplevel_list_v1` + `zcosmic_toplevel_info_v1`
  for window tracking, `zcosmic_toplevel_manager_v1` for window actions (activate,
  close, minimize), `ext_workspace_manager_v1` for workspace listing and switching,
  and `zcosmic_overlap_notify_v1` for overlap-driven visibility.

| Area | Status | Notes |
| --- | --- | --- |
| Launch/startup | works | Docking launches cleanly with the COSMIC backend auto-selected. Log confirms: `Selected session backend: cosmic`. |
| Edge placement | works | Layer-shell positions the dock at the bottom edge. `dock position: monitor=0 geom=(1920,0 1920x1080)` |
| Stays on top | works | Layer-shell TOP layer with exclusive zone keeps dock visible above other windows. |
| Screen-edge reservation / struts | works | Layer-shell exclusive zone reserves edge space for the dock. |
| Hover and click interaction | works | Basic interaction works in smoke testing. |
| Menus | not tested | |
| Drag and drop | not tested | |
| Running-window tracking | works | `terminator.desktop` (Claude Code terminal) detected as running via COSMIC toplevel adapter. Running indicator dot visible in dock. |
| Minimize / restore / focus cycling | partly works | Management capabilities received: `{close, activate, maximize, minimize, move_to_workspace}`. Actions available through protocol but not yet exercised in full UI flow. |
| Window previews | partly works | Standard Wayland image-copy capture protocols are wired in via `WaylandPreviewService` using `ext_foreign_toplevel_image_capture_source_manager_v1` + `ext_image_copy_capture_manager_v1`. Previews not yet exercised in full UI flow but backend infrastructure is in place. |
| Applets (general) | works | Applets load and render correctly. |
| Autohide | works | Auto hide behavior works. |
| Pointer barriers | not tested | |
| Overlap-based hide modes | partly works | `zcosmic_overlap_notify_v1` protocol bound successfully. Overlap subscription requires a realized layer-shell surface (wired in surface service via `on_layer_surface_ready` callback). |
| Multi-monitor behavior | not tested | |
| Suspend / resume recovery | not tested | |
| Notes / anomalies | partly works | `zcosmic_workspace_manager_v2` protocol is advertised but does not send workspace events with the vendored XML-based bindings; `ext_workspace_manager_v1` is used instead and works correctly (3 workspaces discovered). A single cffi `AttributeError: 'NoneType' object has no attribute 'registry'` is logged as "Exception ignored" on startup from deprecated protocol events; it does not affect runtime behavior. |

#### Test: KWin / KDE Plasma 6 native Wayland

- Date: 2026-06-09
- Distro: KDE neon / KDE Plasma 6
- Desktop: KDE Plasma
- Session type: Wayland
- Compositor: KWin 6
- Display variables: `XDG_SESSION_TYPE=wayland`, `WAYLAND_DISPLAY=wayland-0`, `XDG_CURRENT_DESKTOP=KDE`
- Launch command: `DOCKING_BACKEND=kwin DOCKING_LOG_LEVEL=DEBUG python3 run.py`
- Result summary: Docking launches as a native Wayland client using layer-shell
  for surface placement, KWin's D-Bus `VirtualDesktopManager` for workspace
  listing/switching, and AT-SPI for window tracking.

| Area | Status | Notes |
| --- | --- | --- |
| Launch/startup | works | Docking launches cleanly with the KWin backend auto-selected. Log confirms: `Selected session backend: kwin`. |
| Edge placement | works | Layer-shell positions the dock at the configured edge. |
| Stays on top | works | Layer-shell TOP layer keeps dock visible. |
| Screen-edge reservation / struts | works | Layer-shell exclusive zone reserves edge space. |
| Hover and click interaction | works | Basic interaction works in smoke testing. |
| Menus | not tested | |
| Drag and drop | not tested | |
| Running-window tracking | partly works | AT-SPI window tracking provides best-effort window discovery. KWin 6 does not expose a public window-list protocol (`wlr-foreign-toplevel-management`, `ext-foreign-toplevel-list`, and `org_kde_plasma_window_management` are all unavailable to third-party Wayland clients). |
| Minimize / restore / focus cycling | partly works | Actions limited by window tracking availability. |
| Window previews | not tested | |
| Applets (general) | works | Applets load and render correctly. |
| Autohide | works | Auto hide behavior works. |
| Pointer barriers | not tested | |
| Overlap-based hide modes | not tested | |
| Multi-monitor behavior | not tested | |
| Suspend / resume recovery | not tested | |
| Notes / anomalies | partly works | Workspace listing/switching works correctly via KWin's D-Bus `VirtualDesktopManager`. Window tracking is the main limitation: KWin 6 does not provide a public third-party window-list protocol. AT-SPI provides a best-effort path but is not equivalent to Wnck/X11 window tracking. |

#### Test: Hyprland native Wayland

- Date: 2026-06-09
- Distro: (Hyprland session)
- Desktop: Hyprland
- Session type: Wayland
- Compositor: Hyprland
- Display variables: `XDG_SESSION_TYPE=wayland`, `WAYLAND_DISPLAY=wayland-1`, `HYPRLAND_INSTANCE_SIGNATURE=<sig>`
- Launch command: `DOCKING_BACKEND=hyprland DOCKING_LOG_LEVEL=DEBUG python3 run.py`
- Result summary: Docking launches as a native Wayland client using Hyprland's
  event socket for window/workspace tracking and short-lived command-socket
  calls for window actions.

| Area | Status | Notes |
| --- | --- | --- |
| Launch/startup | works | Docking launches with the Hyprland IPC backend. |
| Edge placement | works | Layer-shell positions the dock at the configured edge. |
| Stays on top | works | Layer-shell TOP layer keeps dock visible. |
| Screen-edge reservation / struts | works | Layer-shell exclusive zone reserves edge space. |
| Hover and click interaction | works | Basic interaction works in smoke testing. |
| Menus | not tested | |
| Drag and drop | not tested | |
| Running-window tracking | works | Event socket provides openwindow/closewindow/activewindowv2 events. Window addresses mapped to backend-neutral `WindowId` values. |
| Minimize / restore / focus cycling | partly works | Focus and close actions work via dispatch commands. Full minimize/restore/cycle depends on compositor capability. |
| Window previews | not tested | |
| Applets (general) | works | Applets load and render correctly. |
| Autohide | works | Auto hide behavior works. |
| Pointer barriers | not tested | |
| Overlap-based hide modes | not tested | |
| Multi-monitor behavior | not tested | |
| Suspend / resume recovery | not tested | |
| Notes / anomalies | partly works | Command socket calls are intentionally short-lived to avoid stalling the compositor. Event stream provides workspacev2 and focusedmonv2 events. Workspace listing/switching supported via IPC where the compositor exposes stable IDs. |

#### Test: GNOME Shell bridge prototype

- Date: 2026-06-06
- Distro: Ubuntu GNOME
- Desktop: GNOME Shell 50.1
- Session type: Wayland
- Compositor: GNOME Shell / Mutter
- Display variables: `XDG_SESSION_TYPE=wayland`, `WAYLAND_DISPLAY=wayland-0`, `DISPLAY=:0`
- Launch command: `DOCKING_BACKEND=gnome-shell python3 run.py`
- Result summary: The GNOME Shell extension bridge loads, exports D-Bus state,
  and Docking selects the `gnome-shell-bridge` backend. Active indicators and
  workspace switching work in manual testing.

| Area | Status | Notes |
| --- | --- | --- |
| Extension load | works | `tools/gnome_bridge.sh status` reports the extension as enabled/active and the bridge D-Bus API as available. |
| Backend selection | works | Log reports `Selected session backend: gnome-shell-bridge`. |
| Running-window tracking | partly works | The bridge exports native GNOME window rows and active state; active indicators work in manual testing. App matching now handles Snap-style app-ids (e.g., `firefox_firefox.desktop`) by also trying leading underscore segments. |
| Workspace switching | works | The Workspaces applet can switch GNOME workspaces through the bridge. |
| Minimize / restore / focus cycling | works | Activate, Minimize, and Close bridge methods validated live against GNOME Shell 50.1. Activate now calls `unminimize()` before `activate()` for minimized windows (needs logout/login to reload the updated extension due to GJS module caching). |
| Window previews | fails | The bridge does not implement preview capture. |
| Screen-edge reservation / struts | works | The GNOME Shell extension positions the dock window at the configured screen edge via Mutter's `move_resize_frame()`. The Docking GTK window identifies itself via `GLib.set_prgname("Docking")` so the extension can find it. Fully Wayland-native — no XWayland required. |
| Shutdown | works | Direct SIGTERM smoke test exited within 2 seconds after adding a GTK-main fallback. |
| Notes / anomalies | partly works | GNOME Shell may cache GJS modules for an extension UUID. After editing `extension.js`, a logout/login was required before the running Shell used the corrected source. |

What this means:

- This is not just an autohide or hover state-machine bug.
- The strongest current hypothesis is a GTK/XWayland/Mutter presentation or
  draw-delivery failure affecting Docking's transparent RGBA dock window.
- The main app now has enough evidence. Further reduction work is better done
  in `tools/xwayland_repro.py` than by adding more invasive tracing to Docking
  itself.

### Per-Applet Notes

This table tracks Ubuntu 25 XWayland observations per built-in applet.
Entries marked `not specifically tested` should not be read as confirmed good
or bad behavior; they only mean no applet-specific issue has been recorded yet.

| Applet | Status | Notes |
| --- | --- | --- |
| `aiusage` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `ambient` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `applications` | partly works | The applet launches, but the search box does not receive focus in the tested Ubuntu 25 XWayland setup, so filtering applications does not work. Separate dock-core limitations still apply for running-state dots, previews, and running-app left-click behavior on native Wayland apps. |
| `battery` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `bluetooth` | works | User reports the applet works in the Ubuntu 25 XWayland setup. Availability may still depend on local D-Bus/system bus access. |
| `bookmarks` | partly works | User reports the applet has the same issue as `applications`: form fields cannot be focused or clicked in the tested Ubuntu 25 XWayland setup, so interactive editing/filtering UI is not usable. |
| `brightness` | partly works | Backend logic now prefers `brightnessctl`, but actual brightness changes still depend on local system permission/setup for the backlight device. In the tested setup, plain `brightnessctl` was denied while `sudo brightnessctl` worked. |
| `calculator` | partly works | User reports the applet has a similar focus/input problem to `applications`. Interactive input does not behave correctly in the tested Ubuntu 25 XWayland setup. This may not be a pure Wayland regression; the popup/input model itself may already be fragile on normal X11. |
| `calendar` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `clippy` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `clock` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `colorpicker` | fails | Picks black instead of the visible screen color. Current implementation samples from the X11 root window, which does not represent the real Wayland desktop scene in this setup. |
| `desktop` | works | User reports the applet works in the Ubuntu 25 XWayland setup. Show-desktop semantics may still be constrained by broader Wayland window-management limits. |
| `hydration` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `keyboardlayout` | works | Works after adding a GNOME-specific backend that reads GNOME input sources and switches layouts through GNOME settings, rather than relying on `setxkbmap` or a narrower IBus view. |
| `moon` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `music` | not specifically tested | No confirmed Ubuntu 25 XWayland issue recorded yet. |
| `network` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `notifications` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `pet` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `pomodoro` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `powerprofiles` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `quicknote` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `quote` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `recentfiles` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `screenshot` | works | Works after adding the XDG desktop portal backend. This avoids dependence on older standalone screenshot CLI tools that may not be installed by default on Ubuntu 25. |
| `separator` | partly works | User reports menu-based gap increase/decrease works, but wheel scroll behavior is broken in the Ubuntu 25 XWayland setup. Investigation found the dock scroll handler only interpreted legacy discrete `UP`/`DOWN` events; smooth-scroll devices under Wayland/XWayland are a likely cause. |
| `session` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `stretchcoach` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `systemmonitor` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `todayinhistory` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `trash` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `trivia` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `unitconverter` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `urlshortener` | partly works | User reports the applet works, but the input field only seems to become focusable after closing and reopening the dialog once in the Ubuntu 25 XWayland setup. |
| `volume` | works | User reports the applet works in the Ubuntu 25 XWayland setup. |
| `weather` | partly works | User reports the applet can remain stuck on `loading...` in the Ubuntu 25 XWayland setup. This does not appear Wayland-specific; the fetch path previously had no failure state and could mask API/dependency/network failures as perpetual loading. The applet now shows `unavailable` after a failed fetch instead of staying in loading forever. |
| `windowkiller` | fails | Does not work reliably in this mode. It depends on an X11/Wnck overlay-and-pick flow using global window geometry and root-relative click targeting. |
| `workspaces` | works | User reports the applet works in the Ubuntu 25 XWayland setup. Broader Wnck/X11 workspace limitations may still matter in other scenarios. |

### Less Representative Alternatives

A nested compositor such as `weston` can be useful for quick experiments, but
it is less representative than a real GNOME Wayland session and should not be
treated as the primary compatibility test for documentation.

### What This Test Does Not Prove

A successful `XWayland` run does **not** mean Docking supports native Wayland.
It only means:

- Docking can still run as an X11 application inside a Wayland session
- some or many features may continue to work through XWayland

It does **not** prove:

- native Wayland protocol support
- compositor-portable dock integration
- GNOME Wayland parity with the X11 implementation

## What Existing Wayland Docks Already Prove

Wayland docks and taskbars already exist, but they fall into different classes.

That matters because it shows that "a dock on Wayland" is possible, while also
showing that the implementation strategy depends heavily on the compositor.

### Class 1: GNOME Shell Extensions

Examples:

- `Dash to Dock`
- `Dash to Panel`

These work on GNOME Wayland because they are not ordinary third-party windows.
They run inside GNOME Shell and therefore participate in shell-managed window
state directly.

What this proves:

- GNOME Wayland can support dock/taskbar behavior
- but on GNOME, that behavior is most mature when implemented as shell
  integration rather than as an ordinary GTK application

Why this matters for Docking:

- it is evidence that GNOME Wayland parity is not primarily a GTK problem
- it is more likely a shell-integration problem

### Class 2: Native Wayland Docks on Compositors With Public Protocols

Examples:

- `Cairo-Dock` Wayland mode
- `nwg-dock` for sway
- `nwg-dock-hyprland` for Hyprland
- `sfwbar`
- `Waybar` taskbar mode

These are not GNOME Shell extensions. They are native Wayland clients that rely
on compositor protocols such as:

- layer-shell
- foreign-toplevel management
- compositor-specific IPC in some cases

What this proves:

- a real dock/taskbar can be built as a normal Wayland client on compositor
  families that expose the right protocols
- the backend and feature set are shaped by compositor capabilities, not just by
  GTK

Why this matters for Docking:

- it validates a native-client strategy for wlroots/KWin-style desktops
- it also shows why backend capability detection matters so much

### Class 3: XWayland Compatibility Workarounds

Example:

- `Plank Reloaded` issue `#105`

This is the "force the old X11 dock to keep launching under a Wayland session"
class.

What this proves:

- some X11 docks can still be started under a Wayland session
- but launching is not the same thing as native desktop integration

Why this matters for Docking:

- it is relevant as a temporary user-facing workaround
- it is not evidence that Docking can achieve full Wayland parity by keeping the
  existing architecture

### What This Ecosystem Snapshot Means

The current ecosystem points to the same conclusion repeatedly:

- GNOME Wayland docks tend to succeed as shell extensions
- non-GNOME Wayland docks tend to succeed as protocol-based native clients
- XWayland hacks can keep old docks launching, but they do not solve the native
  integration problem

This is useful because it gives Docking a realistic map of what "Wayland
support" could actually mean in practice.

### Can Docking Bypass the Compositor?

No, not for the capabilities that matter to a dock.

On Wayland, global window state, stacking order, workspace membership, foreign
window geometry, activation policy, and foreign-window pixels belong to the
compositor. A normal client can only use information and requests that the
compositor intentionally exposes through Wayland protocols, desktop portals,
IPC, or shell extensions.

This means "copy the compositor implementation" is not a replacement for a
runtime compositor API:

- copying or vendoring protocol XML/client bindings can reduce build-time or
  packaging dependencies
- copying compositor-side logic does not grant access to compositor-owned
  state
- reimplementing protocol helpers such as `gtk-layer-shell` inside Docking
  would still require the compositor to advertise and honor the same protocol
- GNOME-level parity requires GNOME Shell integration because GNOME Shell
  extensions run inside the shell and can access Mutter/Shell APIs that
  ordinary clients cannot

The practical rule is: prefer public protocols first, isolate
compositor-specific bridges second, and keep unsupported features reduced
rather than pretending that plain GTK can recover X11-style authority.

## What GTK4 and Wayland Do and Do Not Allow

The important distinction is not "GTK4 versus GTK3" so much as "Wayland versus
X11".

GTK on Wayland can still provide monitor-level information:

- monitor enumeration
- monitor geometry
- monitor scale factor
- local surface/widget coordinates
- pointer events relative to the application's own surfaces

What a normal Wayland client generally does not get is old X11-style global
window-management authority:

- arbitrary control over top-level window placement in global coordinates
- free inspection of other applications' windows
- access to global stacking/workspace state as a general client API
- X11 window identifiers (`XID`) and X11 property tricks
- X11-style screen reservation via struts
- direct capture of other windows by ID

This matters because Docking is not just "a window with icons". It is a dock:
it wants to know about other applications, reserve screen edges, coordinate
autohide against window overlap, switch workspaces, preview other windows, and
generally behave like a small shell component.

### Monitor Coordinates Versus Window Coordinates

This distinction is easy to miss.

On Wayland, a GTK application can still usually know about monitors as display
objects:

- monitor count
- monitor geometry rectangles
- monitor scale factor
- which monitor a given surface belongs to

So "GTK4 will not allow screen coordinates" is too broad.

What changes is the status of top-level windows and foreign applications. A
normal Wayland client generally cannot:

- set its own top-level window to an arbitrary global coordinate
- ask for the exact global position of arbitrary top-level windows
- inspect unrelated client windows the way an X11 dock did
- assume access to one global desktop coordinate space for window-management
  behavior

For Docking this means:

- monitor targeting is still plausible
- exact X11-style dock placement semantics are not guaranteed by plain GTK
- popup placement code that assumes stable global coordinates needs review
- anything involving other applications' windows moves beyond ordinary GTK

### GTK3 on Wayland Versus GTK4 on Wayland

Many of Docking's limitations on Wayland are not unique to GTK4. They come from
Wayland's security and compositor model.

GTK4 does matter in one important sense:

- it aligns more strictly with the Wayland model
- it does not preserve the old X11 worldview where application code expects to
  manage top-level windows via global coordinates

So the practical reading is:

- this is not just a GTK4 migration problem
- it is a Wayland architecture problem that GTK4 makes harder to ignore

## The Three Categories

The porting discussion is much easier if every feature is assigned to one of
three buckets.

### 1. Works With GTK and Wayland

Meaning:

- a normal app can implement the feature using ordinary GTK/GDK and standard
  desktop APIs
- no compositor-specific protocol is required
- no shell extension is required
- the feature is relatively portable

This is the "our app controls its own UI and its own state" category.

This category is the least controversial. If a feature only requires:

- drawing pixels in our own surface
- handling input delivered to our own surface
- reading our own configuration/theme/app state
- launching desktop files or talking to DBus/system services

then it is usually still in scope for an ordinary GTK application on Wayland.

Examples:

- dock rendering
- theme loading
- icon zoom/hover animation
- pinned launcher shelf
- applet UI that only talks to DBus or other system services
- monitor enumeration
- settings windows and most in-app menus

For Docking, this is the part of the project that is still reusable without
fundamental redesign:

- `docking/core/`
- most of `docking/ui/renderer.py`
- most pure applet rendering/state code

Concretely, examples in the current tree that fit here reasonably well:

- icon rendering and hover zoom in `docking/ui/renderer.py`
- theme and config logic in `docking/core/`
- service-driven applets such as clock, weather, notifications, pomodoro,
  hydration, and other applets that do not inspect foreign windows
- launching pinned apps by desktop file ID

This category does not mean "fully working dock". It means the application's
own user interface can still exist as a normal Wayland app.

### 2. Needs Compositor Protocol

Meaning:

- plain GTK is not enough
- the compositor must expose an extra Wayland protocol for the feature
- support varies by compositor family
- the feature must be implemented through a backend that understands those
  protocols

This is the "possible on Wayland, but only if the desktop cooperates" bucket.

This is where many classic X11 dock behaviors move.

On X11, Docking can often "just do it" because the X server and window manager
expose broad global state and control to clients. On Wayland, many of those
behaviors become explicit compositor capabilities.

If the compositor does not expose the needed protocol, the feature does not
exist for an ordinary client regardless of how much GTK code is written.

Examples in the Wayland ecosystem:

- `wlr-layer-shell`
  - for panel/dock surfaces attached to screen edges
  - often used for exclusive zones and anchored layer surfaces
- `wlr-foreign-toplevel-management`
  - for tasklist-like discovery/control of top-level windows
- `ext-foreign-toplevel-list-v1`
  - for stable mapped-toplevel handles and app/title metadata, without actions
- `ext-workspace-v1`
  - for workspace enumeration and switching
- `ext-image-copy-capture-v1` and related image-capture-source protocols
  - for future output/toplevel capture where the compositor supports them
- XDG desktop portals
  - for user-mediated screenshots, screencast, and color picking

This bucket is the natural place for a native dock on wlroots/KWin-style
compositors.

For Docking, this category would likely cover:

- real edge anchoring of the dock surface
- exclusive-zone style screen reservation instead of X11 struts
- tasklist enumeration for running windows
- window activation/minimize/close actions
- workspace listing and switching
- live previews or window image capture

This category also implies backend work. A protocol-backed feature should not
be welded into the current Wnck/X11 codepaths.

### 3. Needs GNOME Shell Extension or Privileged Integration

Meaning:

- neither ordinary GTK nor the public compositor protocols available to normal
  apps are enough
- the functionality must run inside the shell, or through a shell-approved
  integration point
- the result is GNOME-specific and typically higher maintenance

This is the "the desktop shell itself must participate" bucket.

This category is the most GNOME-specific and the least portable.

If a capability is considered part of the shell's own window-management model,
GNOME's answer is often not "ordinary apps can do that with a public client
API". Instead, the answer tends to be:

- the shell controls that
- the compositor controls that
- or an extension can integrate with the shell

In other words, this bucket is not about "advanced GTK". It is about crossing
the boundary from ordinary application behavior into shell behavior.

On GNOME Wayland, this is likely where a real taskbar/dock solution lands for
features such as:

- native Wayland tasklist integration
- workspace-aware shell behavior
- shell-level autohide/dodge semantics
- shell-aware previews and window management

For Docking, "GNOME Wayland support" should be treated as a shell-integration
project, not as a straightforward widget or toolkit port.

This bucket likely includes, on GNOME:

- true tasklist semantics for native Wayland apps
- real workspace-aware grouping and switching parity
- shell-level dodge/autohide behavior based on other windows
- shell-aware previews and "show desktop" semantics

## Why Docking Is X11-Centric Today

Docking currently depends on X11 in multiple independent subsystems.

### Window Tracking

`docking/platform/backends/x11/impl/window_tracker.py` is built around `libwnck` and X11 window
identities:

- `Wnck.Screen`
- `Wnck.Window`
- XIDs
- WM_CLASS/class-group matching
- active/urgent/running aggregation from X11-visible windows

This powers:

- running indicators
- active-window state
- urgent state
- click-to-focus/minimize/cycle behavior
- window titles
- preview window lists

This is not portable to native GNOME Wayland as-is.

It is not a single API-replacement problem. The current tracker assumes an
entire model of the desktop:

- windows are globally visible to clients
- they have stable XIDs
- their grouping metadata can be queried
- active/urgent state can be observed by ordinary clients
- actions like activate/minimize/close are available through the same client
  path

Any Wayland backend will need a different mental model, not just a different
library call.

### Previews

`docking/ui/preview.py` captures thumbnails through:

- `GdkX11.X11Window.foreign_new_for_display`
- XID-based capture
- direct pixel reads from foreign X11 windows

That model is specific to X11/XWayland.

It also has secondary consequences:

- menu rows and thumbnail clicks are keyed by XID
- activation and close actions flow through XID-based helpers
- the preview UI assumes previews are coupled to real foreign window objects

This makes previews one of the most X11-shaped subsystems in the tree.

### Autohide Dodge

`docking/platform/backends/x11/impl/dodge.py` observes other windows via `Wnck` geometry/state in
order to decide whether the dock should hide.

That entire mechanism assumes client-visible global window geometry and global
window state.

### Screen Reservation and Blur Hints

`docking/platform/backends/x11/impl/struts.py` uses X11 properties directly:

- `_NET_WM_STRUT`
- `_NET_WM_STRUT_PARTIAL`
- `_DOCKING_BACKGROUND_BLUR_REGION`

These are X11-specific concepts. They do not have a drop-in Wayland equivalent.

This is important because "keep windows from overlapping the dock" is not just
one small feature. On X11 Docking combines:

- top-level placement
- struts
- pointer barriers
- input shaping

Together these create the dock feel. On Wayland, these behaviors need to be
rethought as compositor-facing roles and policies rather than copied directly.

### Pointer Barriers and Input Shape

Docking currently relies on X11-era techniques for interaction quality:

- pointer barriers
- input region shaping via `input_shape_combine_region`
- precise top-level positioning assumptions

These are not portable one-to-one to Wayland clients.

### X11/Wnck Applet Services

Several applet features are tied to X11/Wnck/Xlib concepts, but their GTK
applet code now consumes applet-facing services rather than importing those
libraries directly:

- workspaces through `WorkspaceService`
- show desktop through `DesktopActionService`
- window killer through `WindowPickService`
- desk-presence idle time through `IdleService`
- color picking through `ScreenCaptureService`

These are not "small compatibility problems"; they are feature-model problems.

## Current Wayland Reality by Strategy

### Strategy A: Run Docking Through XWayland

This is the short-term hack path.

Practical example from Plank Reloaded issue `#105`:

- forcing GTK to use `GDK_BACKEND=x11`
- running the app as an X11/XWayland client inside a GNOME Wayland session

The issue specifically proposes changing Plank's autostart command from:

- `Exec=plank &`

to:

- `Exec=env XDG_SESSION_TYPE=x11 GDK_BACKEND=x11 plank &`

and also notes that `bamfdaemon.service` crashes in that setup and can be
masked with:

- `systemctl --user mask bamfdaemon.service`

This is important context because it demonstrates the exact shape of the
workaround: it does not make Plank speak Wayland better; it tries to force the
application back onto the X11 GTK backend while the user is otherwise logged
into a Wayland GNOME session.

What this gives:

- the dock window may still appear
- some GTK/X11 behavior may still function
- developers may get a quick way to keep the process launchable in a Wayland
  session

What this does not give:

- native Wayland integration
- reliable visibility/control of native Wayland app windows
- a future-proof architecture
- freedom from X11-only dependencies like Wnck, XIDs, struts, and X11 capture

This may be useful as a temporary compatibility workaround, but it should not
be treated as Wayland support.

#### How To Interpret the Plank `#105` Workaround

This workaround is best described as:

- an `XWayland compatibility mode`
- a way to keep an X11-shaped dock process launchable for some users
- a stopgap, not a port

Why it may help some users temporarily:

- the dock window may still appear
- launchers may still be usable
- some purely local GTK behavior may continue working acceptably

Why it is fundamentally limited:

- it does not convert Wnck-based task tracking into native Wayland task
  tracking
- it does not grant visibility into native Wayland clients the way X11 did
- it does not replace X11-only mechanisms like XIDs, struts, or X11 window
  capture
- it may rely on secondary components that are themselves unstable in that mode,
  such as the `bamfdaemon` note in the issue

What it means for a future Docking support statement:

- this is reasonable material for a future FAQ or troubleshooting note
- it is not a sufficient basis for claiming Wayland support
- it should be documented, if ever offered to users, as an unsupported or
  best-effort workaround with incomplete task/window integration

In other words, this may become part of a pragmatic interim answer for some
users, but it should remain clearly separated from any future native Wayland
roadmap.

Practical reading of this mode:

- it is best understood as "keep an X11 dock limping along under a Wayland
  desktop"
- it is not "the dock now works on Wayland"

Inference from the current codebase plus GNOME's client model:

- Docking would still be fundamentally blind to native Wayland window
  management
- app/task integration would be incomplete
- any behavior relying on global X11 window state would remain fragile or
  incorrect for native Wayland applications

### Strategy B: Native Wayland Dock on wlroots/KWin-Style Desktops

This is the most plausible path for a true native dock without shell
modification.

In this world Docking would need:

- a layer-shell style surface for panel/dock placement
- foreign-toplevel integration for tasklist/window actions
- workspace protocol support where available
- alternative preview/image-capture support

This still requires major backend work, but the protocol model exists.

This is likely the fastest path to a real native-Wayland Docking if the
project is willing to support some compositor families before all compositor
families.

Why this path is attractive:

- the dock problem is already recognized in these ecosystems
- the needed protocol families exist
- `gtk-layer-shell` already exists as a GTK-facing integration library

Why it is still a major project:

- Docking would need a real platform backend split
- the current Wnck-based tasklist model would need replacement
- previews would need a new strategy
- some applets would still need per-feature redesign
- compositor support matrices would need to be tracked and tested

### Strategy C: Full Dock on GNOME Wayland

This is the hardest case.

The key issue is not "GTK4 removed a function". The issue is that Mutter
intentionally does not expose the common third-party dock/taskbar protocols
used by other Wayland compositor families.

As a result, a normal Wayland client on GNOME does not get enough authority to
behave like a classic X11 dock.

Inference for Docking:

- a GNOME Shell extension or comparable shell integration is the most likely
  route for true GNOME Wayland parity
- a plain GTK application is unlikely to achieve feature parity on GNOME
  Wayland with the current public APIs

This is the most important strategic point in this document.

If the project goal is:

- "Docking should be a real dock on Ubuntu GNOME Wayland"

then the project should plan around shell integration early.

If instead the project goal is:

- "Docking should be available as a launcher shelf on Wayland"

then a normal-app Wayland path may still be worthwhile, but it should be
understood as a reduced feature target on GNOME.

## GNOME Shell Extension Support

GNOME Shell extension support is feasible, but it is not the same kind of
backend as X11, layer-shell, foreign-toplevel, or compositor IPC.

GNOME Shell extensions run inside the `gnome-shell` process using GJS
JavaScript. They can use Mutter/Shell APIs through namespaces such as `Meta`,
`Shell`, `Clutter`, and `St`. This is why extensions such as Dash to Dock and
Dash to Panel can work on GNOME Wayland while an ordinary third-party GTK
client cannot.

The key architectural consequence is that a GNOME extension is shell
integration, not a normal Python/GTK runtime backend.

### What an Extension Can Access

Inside GNOME Shell, an extension can use Mutter's `Meta.Window` and
`Meta.Workspace` abstractions rather than Wayland client protocols.

Relevant capabilities include:

- list and inspect windows through Shell/Mutter workspace and display state
- read window title, app identity, focus state, minimized/maximized/fullscreen
  state, monitor, workspace, geometry, and PID where Mutter exposes them
- observe window-added, window-removed, focus, workspace, size, and position
  changes
- activate, focus, minimize, unminimize, maximize, close, move, resize, stick,
  and change-workspace for windows where Mutter permits it
- inspect and activate workspaces through `Meta.Workspace` /
  `global.workspace_manager`
- create visible shell UI with `St` and `Clutter` actors, styled by the
  extension's shell CSS

This is fundamentally different from trying to bypass Wayland from a normal
client. The extension does not escape the compositor; it runs as part of the
shell/compositor environment and uses the shell's internal APIs.

### Option 1: Full GNOME Shell Frontend

This is the path used by serious GNOME docks.

In this model, Docking would ship a GNOME Shell extension that owns the visible
dock/shelf UI. The dock would be implemented with shell actors (`St`, Clutter,
GNOME Shell UI modules), not with the existing GTK `DockWindow`.

What it can solve:

- true GNOME Wayland shelf/dock placement
- running and active application indicators
- workspace-aware window grouping
- shell-level autohide and dodge behavior
- show-desktop and window actions
- monitor-aware shell placement
- behavior comparable to Dash to Dock / Dash to Panel

What it costs:

- the GTK renderer/widgets cannot be reused directly
- much of the visual shell would need a GJS/St implementation or a shared
  model/theme layer that both Python and GJS can consume
- GNOME Shell APIs change across releases, so every supported GNOME version
  needs explicit compatibility work
- bugs in the extension can affect `gnome-shell` stability, performance, and
  user session behavior
- distribution follows GNOME extension packaging/review/version rules, not
  ordinary Python package rules

This is the only realistic path for full GNOME Wayland parity, but it is closer
to building a second frontend than adding another backend service.

### Option 2: GNOME Shell Bridge Extension

A smaller extension could expose GNOME window/workspace state to the existing
Python Docking process over D-Bus.

Existing extensions such as `ws-dbus`, `Window Calls`, and `Focused Window
D-Bus` prove this pattern is possible: a shell extension can list windows,
report focused-window changes, expose workspace data, and perform actions such
as focus, move, resize, minimize, maximize, close, or workspace switching.

What it can help with:

- active/running indicators for native GNOME Wayland windows
- window titles and window menu rows
- workspace-aware filtering
- focus/close/minimize-style actions
- possibly geometry-backed dodge decisions if the bridge exports enough state

What it cannot fully solve:

- the Python GTK dock window is still an ordinary Wayland client
- it does not turn `DockWindow` into a real shell panel
- it does not provide layer-shell-style edge reservation on GNOME
- it creates a trust boundary where an extension exposes shell authority to an
  external process over D-Bus
- bridge APIs would be project-specific and must be versioned/tested like any
  other public interface

This bridge is useful as an experiment or reduced GNOME support layer, but it
should not be described as full GNOME dock parity unless the visible dock
surface problem is also solved.

### Option 3: Hybrid Extension and App

A hybrid design could let the GNOME Shell extension own the visible dock while
the Python app continues to provide configuration, applet data, indexing, or
other non-shell services.

Possible shape:

- GNOME extension draws the shelf/dock and talks to Mutter/Shell for windows
  and workspaces
- Python process provides model data, applet state, launcher metadata, settings,
  and background services over D-Bus or another local IPC
- both frontends share desktop-file matching rules, icon lookup rules, and theme
  tokens as much as practical

This could preserve some Docking logic without forcing the GTK window to do
things GNOME Wayland does not permit.

The risk is complexity: there would be two frontends, two runtimes, two
packaging paths, and a new IPC contract.

### Can These Options Live in This Codebase?

Yes, but they fit the current Docking codebase at different depths.

The bridge extension is the cleanest fit. It can live in the same repository as
an optional GNOME integration package, for example under
`docking/platform/backends/gnome/extension/`. The
extension would expose D-Bus state/actions, and the existing Python app would
consume that through a GNOME-specific backend service.

The hybrid extension can also live in the same repository, but it requires a
clear IPC contract and shared data boundaries. In this model, the GNOME
extension owns the visible shelf, while Python Docking provides settings,
launcher metadata, applet data, indexing, theme tokens, or other non-shell
services.

The full GNOME Shell frontend can live in the same repository too, but it should
be treated as a second frontend. It can reuse concepts, configuration schema,
desktop-file matching rules, icon lookup rules, and generated data. It cannot
directly reuse the GTK `DockWindow`, GTK widgets, or Cairo/GTK renderer inside
GNOME Shell.

A possible repository shape:

```text
docking/
  platform/backends/
    x11/
    reduced/
    wayland/
    gnome/
      extension/    # GNOME Shell JS extension
shared/
  app-matching-rules.json
  theme-tokens.json
```

Important boundaries:

- do not import Python code into GNOME Shell
- do not try to use GTK widgets as GNOME Shell actors
- share data, schemas, generated metadata, matching rules, and IPC contracts
  rather than UI objects
- keep GNOME extension packaging separate from the Python package, even if both
  live in the same source repository
- keep tests and release gates separate enough that GNOME extension breakage
  does not block X11 or layer-shell backends unless that is intentional

### GNOME Extension Maintenance Risks

GNOME Shell extensions are powerful because they are close to the shell. That
is also why they are fragile.

Known risks:

- `metadata.json` must list supported `shell-version` values; unsupported
  versions may not load
- GNOME 45 and later use ES modules for extension code, while older Shell
  versions use different import patterns
- GNOME Shell and Mutter APIs can change between releases, especially for
  overview, workspace, layout, and actor internals
- extension `enable()` must create/connect state and `disable()` must undo all
  changes; leaked signals, actors, timers, or monkey patches are common causes
  of review rejection and runtime instability
- actor allocation/rendering mistakes can cause shell log spam, high CPU, or
  visible stutter
- extensions submitted through extensions.gnome.org are reviewed and versioned
  by GNOME extension distribution rules

For Docking, this means GNOME extension support should be planned as an
explicit product track:

- first decide whether GNOME requires full parity or reduced support is enough
- if full parity is required, plan a GNOME Shell frontend
- if only indicators/actions are needed, prototype a bridge extension first
- keep the native Wayland client backend for layer-shell compositors separate
  from the GNOME extension codepath
- do not try to make a normal GTK Wayland window behave like a GNOME Shell dock
  by copying compositor code

### Recommended GNOME Path

The safest staged plan is:

1. Keep GNOME/Mutter native Wayland in reduced mode for the normal Python/GTK
   app.
2. Prototype a small GNOME Shell bridge extension that exposes read-only window
   and workspace state over D-Bus.
3. Use that bridge to validate app ID matching, running indicators, active
   indicators, and workspace filtering.
4. Only after that, decide whether to build a full GNOME Shell frontend that
   owns the visible shelf surface.

This avoids committing to a full GJS frontend before proving that the shell
integration model fits Docking's architecture.

## Case Study: Cairo-Dock on Wayland

Cairo-Dock is one of the most useful comparison points because it is not just
"launching under Wayland". It contains an actual Wayland implementation split
across multiple backends and protocols.

That makes it a good case study for what a serious dock port looks like in
practice.

### High-Level Conclusion

Cairo-Dock does not solve Wayland by replacing a few X11 calls with Wayland
calls.

Instead, it does all of the following:

- builds a dedicated Wayland container backend
- uses `gtk-layer-shell` for dock surface behavior
- probes multiple task/window-management protocols at runtime
- picks compositor-specific backends where available
- adds compositor-specific IPC integrations where protocol support is
  insufficient
- explicitly disables unsupported plugins and features on Wayland

This is a much stronger architecture than an `XWayland` workaround, but it is
also much more complex than a plain toolkit migration.

### What Cairo-Dock Claims Publicly

Its own Wayland README says:

- Wayland support is intended for compositors supporting layer-shell
- main targets are compositors such as Wayfire, labwc, KWin, and Cosmic
- GNOME Shell / Mutter is not supported
- taskbar functionality depends on compositor support for taskbar/window
  protocols
- support is still experimental

It also lists known limitations including:

- no general global keyboard shortcuts
- overlap detection only working on KWin
- buggy `keep below` behavior on some KWin versions
- limited multi-monitor support
- workspace tracking listed as unsupported
- several rendering and popup limitations

This already tells us that Cairo-Dock's Wayland support is selective and
capability-dependent rather than universal parity.

### Build-Time Structure

Cairo-Dock treats Wayland support as several separate build capabilities rather
than one switch:

- base Wayland client support
- generated protocol support via `wayland-scanner`
- `gtk-layer-shell`
- EGL rendering support
- optional JSON / evdev support for compositor IPC and shortcuts

This is significant because it mirrors the real problem:

- "can start as a Wayland client" is not the same as
- "can behave like a dock" which is not the same as
- "can render efficiently with OpenGL" which is not the same as
- "can support taskbar and shortcuts on a given compositor"

In other words, Cairo-Dock models Wayland as a capability matrix.

### Runtime Detection and Backend Selection

At runtime, Cairo-Dock:

- checks whether GTK is actually using a Wayland display
- obtains the `wl_display`
- walks the Wayland registry
- binds interfaces as they appear
- selects compositor type and functionality based on what is present

This is a key design difference from an X11-first architecture. The code does
not assume that "Wayland" is one backend with one fixed feature set.

Instead it makes decisions such as:

- if Cosmic protocols are available, use the Cosmic path
- else if Plasma window-management is available, use the KWin path
- else try `wlr-foreign-toplevel-management`
- separately try Plasma virtual desktops or `ext-workspaces`
- separately try hotspot support via Wayfire shell or layer-shell fallback

This is probably the single most important architectural lesson from the
project.

### Dock Surface Implementation

For the dock surface itself, Cairo-Dock relies heavily on `gtk-layer-shell`.

That layer is used for:

- anchoring the dock to a screen edge
- reserving edge space through exclusive zone behavior
- moving the dock to a monitor
- switching between top and bottom layers
- handling keyboard interactivity on layer-shell surfaces
- positioning and managing subdocks, menus, and dialogs

It also contains a number of Wayland-specific workarounds and heuristics:

- popup/subdock initialization tricks to make relative placement work
- special handling for keyboard-mode transitions
- aimed-point adjustment heuristics when relative placement information is
  incomplete

This matters because it shows that even "basic dock placement" is not just
"create a GTK window and move it to the edge". Cairo-Dock treats dock
positioning as a specialized Wayland surface role.

### Shared Wayland Window Model

One of the stronger parts of Cairo-Dock's design is that it keeps a common
internal Wayland window model in `cairo-dock-wayland-wm.c`.

That layer:

- stores pending changes for titles, app IDs, state, attention, stickiness,
  geometry, and workspace position
- defers notification processing until idle to avoid reentrancy problems during
  Wayland event dispatch
- converts protocol-specific events into the dock's internal window/task model
- manages activation state, stack ordering, creation, destruction, and grouping

This is important because the compositor-specific protocol handlers are not
allowed to become the entire application model. They feed a shared abstraction.

That is likely the most reusable lesson for Docking:

- protocol handlers should populate a backend-neutral task/window model
- they should not leak protocol-specific assumptions directly into rendering and
  item logic

### Taskbar and Window Control Backends

For taskbar behavior, Cairo-Dock does not rely on one protocol.

It currently supports multiple families:

- `wlr-foreign-toplevel-management`
- `plasma-window-management`
- Cosmic toplevel protocols built on top of `ext-foreign-toplevel-list` plus
  Cosmic management/info extensions

These backends expose different action sets and quality levels.

#### wlroots-style path

On the `wlr-foreign-toplevel-management` path, Cairo-Dock supports:

- activate
- close
- minimize
- maximize
- fullscreen
- transient relationships
- a thumbnail rectangle hint

But it also has limitations:

- capability reporting is partly guessed
- sticky / above support is delegated to Wayfire-specific integration helpers
- kill is not available
- it uses hacks such as fake initial geometry for minimize-on-click behavior

This is workable, but clearly not parity with the old X11 world.

#### KWin / Plasma path

The KWin path is richer.

Through Plasma protocols, Cairo-Dock can do more:

- activate
- close
- minimize
- maximize
- fullscreen
- keep-above
- attempt sticky
- move windows between desktops
- expose actual capability flags
- use PIDs for kill requests

This is one of the best examples of why compositor-specific backends matter:
the KWin path is meaningfully more capable than the generic wlroots path.

#### Cosmic path

The Cosmic path is even more specialized.

It combines:

- `ext-foreign-toplevel-list`
- `cosmic_toplevel_info`
- `cosmic_toplevel_management`
- `cosmic_workspace`
- `cosmic_overlap_notify`

This lets Cairo-Dock do more than generic taskbar control. In particular, it
adds a compositor-native overlap notification path for dock visibility logic.

That is exactly the kind of feature that ordinary GTK cannot provide by itself.

### Workspace Handling

The workspace story is more nuanced than Cairo-Dock's README alone suggests.

Its README still says workspace tracking is not supported on Wayland. But the
source tree now contains explicit workspace backends for:

- Plasma virtual desktops
- `ext-workspaces`

This suggests that the code is ahead of the older high-level support summary,
or that workspace support is considered partial and compositor-dependent rather
than generally complete.

Important practical points:

- KWin has a dedicated Plasma virtual desktop implementation
- `ext-workspaces` is used for compositors that expose that protocol
- the code has to normalize very different compositor behaviors and workspace
  coordinate systems
- comments in the source note compositor-specific oddities for labwc and Cosmic

The lesson is that "workspace support" is not a simple yes/no feature on
Wayland. It is protocol-specific and often compositor-specific even when based
on a nominally shared protocol.

### Dock Visibility and Overlap Detection

This is one of the clearest examples of Cairo-Dock going beyond plain GTK.

On X11, overlap-based autohide can be implemented from client-visible window
geometry. On Wayland, Cairo-Dock has to use different approaches depending on
compositor support.

Examples:

- generic fallback can rely only on whatever geometry/window data the selected
  task backend exposes
- Wayfire gets extra functionality through its own IPC integration
- Cosmic gets a dedicated overlap-notify protocol path
- the README still documents overlap detection as only really working on KWin,
  which shows how compositor-dependent this remains in practice

This is a strong warning for Docking:

- "autohide on overlap" should be treated as a backend capability
- not as something any Wayland backend can automatically provide

### Hidden Dock Recall / Hotspots

Cairo-Dock has a dedicated Wayland hotspot subsystem for recalling hidden docks.

It does not assume a single implementation.

It can use:

- a Wayfire shell hotspot protocol when available
- or a fallback built from transparent `gtk-layer-shell` edge windows acting as
  hotspot sensors

This is notable because it shows the kind of creative redesign needed on
Wayland. Features that were previously natural consequences of X11 global
control may have to be rebuilt as separate surfaces or protocol objects.

### Rendering and EGL

Cairo-Dock also has a dedicated EGL path for Wayland.

That code:

- detects whether GTK is running on Wayland or X11
- selects EGL platform entry points accordingly
- creates `wl_egl_window` objects for Wayland surfaces
- manually sets buffer scale for Wayland surfaces
- reinitializes EGL surfaces on map/unmap under Wayland
- uses `gtk_widget_set_double_buffered()` despite it being officially
  unsupported in this use case

This is worth calling out because their own README explicitly admits that part
of the Wayland OpenGL stack depends on behavior GTK does not officially promise
for Wayland.

That does not invalidate the implementation, but it is a reminder that mature
Wayland support can still require uncomfortable edge-case engineering.

### Keyboard Shortcuts

Cairo-Dock's README says global keyboard shortcuts are not generally available
on Wayland, and the code supports that reading.

However, the source also shows a compositor-specific exception:

- Wayfire IPC can expose a path for shortcut registration

This is another good example of a Wayland feature that is:

- unavailable as a general client capability
- but potentially restorable on some compositors through private or
  compositor-specific integration

For Docking, this strongly suggests that global shortcuts should be treated as
a compositor capability, not a baseline guarantee.

### Plugin and Feature Gating

Cairo-Dock explicitly tracks whether plugin modules support X11 and/or Wayland.

The module loader:

- stores support flags per module
- disables modules that do not support the current backend
- can blacklist incompatible modules on Wayland
- exposes a debug option to disable that blacklist

This is one of the most pragmatic parts of its design.

It means Cairo-Dock does not claim that every plugin automatically works just
because the main process can run on Wayland. Unsupported modules are disabled
deliberately.

That is a useful model for Docking, especially for:

- Wnck-dependent applets
- preview-heavy features
- shell-like utilities such as show-desktop or window-killer behavior

### Limits and Caveats Visible in the Source

Even after all of the above, Cairo-Dock still shows clear limitations:

- Wayland support is declared experimental in the application UI
- missing `gtk-layer-shell` support triggers a runtime warning
- unsupported plugins are blacklisted
- some capabilities are guessed rather than negotiated cleanly
- several code comments document compositor quirks and hacks
- some functionality remains available only on one compositor family
- GNOME / Mutter is still explicitly unsupported

This is important because it shows that even a serious multi-year Wayland port
does not magically turn the ecosystem into a uniform platform for dock apps.

### What Docking Can Learn From Cairo-Dock

The most important lessons are:

- a serious Wayland dock needs a real backend split
- Wayland support should be modeled as capabilities, not as a binary switch
- a shared internal task/window abstraction is valuable
- compositor-specific integrations are sometimes unavoidable
- plugin and feature gating are part of the architecture, not just a temporary
  hack
- GNOME Wayland remains a separate problem even when other Wayland desktops are
  supported

Just as importantly, Cairo-Dock is evidence that:

- a normal dock app can support Wayland on some compositor families
- but that does not imply GNOME / Mutter support
- and it does not remove the need for explicit feature scoping and backend
  contracts

For Docking, Cairo-Dock strengthens the case for:

- starting with an explicit `x11` versus `wayland` platform split
- defining backend capability interfaces early
- treating `wlroots/KWin/Cosmic` and `GNOME/Mutter` as separate targets
- planning for a reduced Wayland mode before full parity

It also weakens any hope that a plain "GTK4 port" by itself would solve the
core problem.

## Feature Classification (Conceptual)

> See [Feature Support Matrix](#feature-support-matrix) above for the current
> per-backend support status. This section is the conceptual framework that
> guided the backend architecture.

The table below classifies major Docking capabilities by likely Wayland path.

| Feature | GTK + Wayland | Needs Protocol | Needs GNOME Shell Integration |
| --- | --- | --- | --- |
| Dock rendering and themes | Yes | No | No |
| Pinned launchers | Yes | No | No |
| Launching apps | Yes | No | No |
| Non-Wnck applets | Yes | No | No |
| Monitor enumeration | Yes | No | No |
| Edge-attached dock surface | No | Yes | On GNOME, likely yes |
| Running indicators / tasklist | No | Yes | On GNOME, likely yes |
| Focus/minimize/close app windows | No | Yes | On GNOME, likely yes |
| Workspace applet | No | Yes | On GNOME, likely yes |
| Window previews | No | Yes | On GNOME, likely yes |
| X11-style dodge overlap detection | No | Yes or redesign | On GNOME, likely yes |
| Strut-style reserved screen space | No | Needs layer/exclusive-zone equivalent | On GNOME, likely yes |
| Desktop applet show-desktop parity | No | Possibly | Likely yes |
| Window killer applet | No | No general client API | Likely yes |

### Feature Notes

Some rows deserve extra explanation.

#### Edge-Attached Dock Surface

On X11, Docking positions a top-level dock window directly and reserves space
with struts.

On Wayland, a panel or dock surface is typically a compositor-defined role, not
just a regular window that happens to sit at the edge. That is why this moves
out of ordinary GTK and into protocol or shell territory.

#### Running Indicators and Tasklist

This is the biggest conceptual shift.

On X11, Docking can inspect global window state via Wnck and derive:

- which apps are running
- which one is active
- how many windows belong to each app
- which ones need attention

On Wayland, that information is not generally exposed to ordinary clients. A
Wayland dock needs compositor cooperation here.

#### Previews

Current Docking previews are not just visual decoration. They are based on:

- enumerating app windows
- capturing foreign window contents
- activating or closing those windows by XID

That is an especially X11-specific cluster of behavior, so previews should be
treated as one of the last features to regain on Wayland rather than as an
early porting target.

#### Workspaces

The workspaces applet is not just a local UI control. It assumes that an app
can observe and manipulate workspace state through Wnck. GNOME upstream
discussion makes it clear that workspaces are compositor functionality and are
not available to ordinary Wayland clients in the same way.

#### Window Killer

The window-killer applet is an especially strong example of an X11-era feature.
It overlays the screen, reads root-relative coordinates, finds the topmost
window at that point, and kills its PID. This should be assumed unavailable to
ordinary Wayland clients unless a compositor-specific mechanism exists.

## GNOME-Specific Constraint

GNOME Wayland is not just "Wayland with a different theme". It has a different
trust model.

Normal Wayland clients on GNOME are intentionally not given the same kind of
global knowledge/control that X11 allowed:

- no general global coordinate ownership for window placement
- no general workspace API for clients
- no general tasklist/window-manager API for arbitrary clients

That is why a feature that seems "normal for a dock" on X11 may actually be a
shell feature on GNOME Wayland.

This should inform project planning:

- "Wayland support" is not one thing
- "GNOME Wayland support" is a separate workstream

### Why Mutter Is the Hard Case

Mutter is the critical constraint for Ubuntu GNOME.

On compositor families that expose common dock/taskbar protocols, a third-party
client can often build a useful dock through public Wayland interfaces. On
Mutter, the relevant dock/taskbar protocols commonly used elsewhere are not the
default path for third-party clients.

That makes a large practical difference:

- public Wayland protocol support is uneven across compositors
- a feature that is protocol-backed on wlroots or KWin may have no equivalent
  route for a normal client on GNOME
- the path of least resistance on GNOME often becomes shell integration rather
  than ordinary client code

For Docking, this means "Wayland support on some desktops" and "Wayland support
on Ubuntu GNOME" should be treated as related but distinct goals.

## Incremental Refactor Plan Before Any Wayland Backend

> **Status: Phases 1-8 complete.** The refactor described below has been
> executed. The X11 backend is fully isolated behind backend contracts,
> the reduced backend validates the architecture, and multiple native
> Wayland backends (COSMIC, Hyprland, KWin, GNOME Shell bridge) are
> implemented. This section is preserved as the design rationale for the
> current architecture.

The most practical next step is not "start writing Wayland code". It is:

- make `x11` an explicit backend instead of the implicit default
- stop leaking `Wnck`, `XID`, and `GdkX11` types across the whole runtime
- create intermediate states that are worth shipping even if Wayland support is
  still zero

This matters because the current codebase does not merely "contain some X11
code". It is architected as if X11-shaped desktop integration were the default
runtime model for the whole application.

If that is not changed first, any future Wayland work becomes a risky
cross-cutting rewrite touching startup, UI, previews, applets, placement, and
task tracking all at once.

The safer strategy is to refactor in place through a sequence of small,
behavior-preserving steps.

### Goal of the Refactor

The immediate goal is not:

- add native Wayland support
- change user-visible behavior
- remove current X11 features

The immediate goal is:

- isolate platform-specific behavior behind explicit backend contracts
- preserve current X11 behavior as the only implemented backend
- make unsupported capabilities explicit instead of implicit
- create clean insertion points for later wlroots/KWin and GNOME-specific work

If done well, this refactor pays off even if no Wayland code is written for a
while:

- X11 code becomes easier to reason about
- feature-specific bugs become easier to localize
- Wnck-only applets stop infecting unrelated parts of the app
- future support matrices become much easier to define honestly

### What Was Coupled (Historical)

The pre-refactor runtime had some useful separation already, but several
important modules still assumed X11 directly. This section describes the
state before the backend refactor; all X11 coupling points listed below
have since been isolated behind backend service interfaces.

Relatively good existing seams:

- `docking/platform/model.py`
  - consumes aggregates rather than raw `Wnck.Window` objects
  - already behaves somewhat like a backend-neutral state sink
- much of `docking/core/`
  - config, item identity, theme, and layout logic are not inherently X11
- most rendering in `docking/ui/renderer.py`
  - paints the dock rather than managing foreign windows

Main X11 coupling points:

- `docking/app.py`
  - startup constructs an explicit session backend
- `docking/ui/dock_window.py`
  - still coordinates UI shell behavior through backend services
- `docking/ui/placement.py`
  - owns placement policy while X11 edge integration lives behind
    `SurfaceService`
- `docking/ui/preview.py`
  - owns preview UI while XID capture lives behind `PreviewService`
- `docking/platform/backends/x11/impl/window_tracker.py`
  - still contains the X11/Wnck running-window implementation used by
    `X11WindowService`
- `docking/platform/backends/x11/impl/dodge.py`
  - uses Wnck geometry/state to decide overlap hiding
- X11/Wnck applet service implementations:
  - `docking/platform/backends/x11/services/workspaces.py`
  - `docking/platform/backends/x11/services/actions.py`
  - `docking/platform/backends/x11/services/picking.py`
  - `docking/platform/backends/x11/services/idle.py`
  - `docking/platform/backends/x11/services/capture.py`

This is why the right initial milestone is not a `wayland backend`. It is a
cleanly isolated `x11 backend`.

### Current Backend Shape

The backend boundary is capability-oriented at `docking/platform/backends/base.py`.

Composition:

- `SessionBackend`
  - top-level runtime object passed into startup/UI composition
- `WindowService`
  - running apps/windows, focus/minimize/close/cycle, active/urgent state
- `SurfaceService`
  - edge placement integration, reserved space, pointer barriers, input-region
    support specific to the platform
- `VisibilityService`
  - overlap/dodge monitoring and related hide/show signals
- `PreviewService`
  - preview image capture
- `WorkspaceService`
  - workspace listing/switching where supported
- `DesktopActionService`
  - shell-like actions such as show desktop
- `ScreenCaptureService`, `IdleService`, and `WindowPickService`
  - applet-facing capabilities that depend on compositor/session support

The backend should also publish an explicit capability set, for example:

- `tasklist`
- `previews`
- `workspaces`
- `show_desktop`
- `window_killer`
- `struts`
- `barriers`
- `overlap_tracking`

This is important because it allows the application to say:

- this feature does not exist on this backend

instead of:

- this feature exists conceptually everywhere but happens to crash, no-op, or
  remain half-implemented somewhere else

### Neutral Data Types to Introduce Early

The most important rule for the refactor is:

- backend-specific object types should stop escaping their backend

That means code outside backend implementations should not traffic in:

- `Wnck.Window`
- `Wnck.Workspace`
- XIDs
- `GdkX11.X11Window`

Instead, introduce backend-neutral runtime types early, even while they are
implemented only by X11.

Examples:

- `WindowId`
  - opaque backend-tagged identifier for a window
- `RunningWindowInfo`
  - title, active, urgent, minimized, app identity, and handle
- `RunningAppInfo`
  - app-level aggregate currently consumed by the model
- `WorkspaceSnapshot`
  - backend-neutral workspace identity and label
- `PreviewImage`
  - a backend-neutral image wrapper, with UI-owned fallback when capture is unavailable

This matters because the type boundary is usually where portability fails.
Once UI and model code expect XIDs or `Wnck.Window`, the backend abstraction is
already compromised.

### Current Package Layout

The implemented structure:

- `docking/platform/backends/base.py`
  - backend protocols/interfaces and shared neutral dataclasses
- `docking/platform/backends/x11/__init__.py`
  - X11 backend composition root
- `docking/platform/backends/x11/services/windows.py`
  - `X11WindowService` adapter over the Wnck tracker implementation
- `docking/platform/backends/x11/services/surface.py`
  - X11 surface adapter; struts, barriers, and blur helpers live under `impl/`
- `docking/platform/backends/x11/services/visibility.py`
  - X11 visibility adapter; dodge/overlap monitor lives under `impl/`
- `docking/platform/backends/x11/services/previews.py`
  - X11 preview adapter; foreign-window capture helpers live under `impl/`
- `docking/platform/backends/x11/services/workspaces.py`
  - Wnck workspace support
- `docking/platform/backends/x11/services/actions.py`
  - show desktop and related shell-style actions

The existing `docking/platform/` package can remain, but should gradually stop
being "the place where X11 is the application" and become a narrower host for:

- backend-neutral state/model pieces
- launcher integration
- environment/session helpers
- backend selection/bootstrap code

This `backends/x11` and `backends/wayland` split is a good idea, but only if it
is used to enforce real boundaries rather than to create two large grab-bag
directories.

The point is not just:

- "put X11 files in one folder"

The point is:

- make backend ownership obvious
- keep backend-specific imports out of the rest of the tree
- allow one backend to implement only the capabilities it can honestly support
- make it possible to land one subsystem move at a time

### Current Tree Organization

The codebase should be thought of as four layers:

1. `docking/core/`
2. `docking/platform/`
3. `docking/ui/`
4. `docking/applets/`

The backend split belongs under `docking/platform/`, not at the repository root
and not mixed directly into `docking/ui/`.

Recommended structure:

- `docking/platform/backends/base.py`
  - backend protocols
  - backend-neutral dataclasses
  - capability definitions
  - small shared helper types
- `docking/platform/backends/selection.py`
  - chooses which backend to construct based on runtime/session
  - initially can always return `X11SessionBackend`
- `docking/platform/backends/x11/__init__.py`
  - exports `X11SessionBackend`
- `docking/platform/backends/x11/services/windows.py`
  - Wnck window tracking and window actions
- `docking/platform/backends/x11/services/surface.py`
  - struts, barriers, blur-region behavior, X11 surface helpers
- `docking/platform/backends/x11/services/visibility.py`
  - dodge/autohide overlap integration
- `docking/platform/backends/x11/services/previews.py`
  - X11 preview adapter; foreign-window capture helpers live under `impl/` and preview action support
- `docking/platform/backends/x11/services/workspaces.py`
  - Wnck workspace support
- `docking/platform/backends/x11/services/actions.py`
  - show desktop or similar shell-style actions
- `docking/platform/backends/wayland/__init__.py`
  - future `WaylandSessionBackend`
- `docking/platform/backends/wayland/windows.py`
  - future task/window protocol adapters
- `docking/platform/backends/wayland/surface.py`
  - future layer-shell or compositor-specific surface behavior
- `docking/platform/backends/wayland/visibility.py`
  - future overlap/hotspot behavior where supported
- `docking/platform/backends/wayland/previews.py`
  - future preview/image-capture support or explicit unavailability
- `docking/platform/backends/wayland/workspaces.py`
  - future workspace protocol support
- `docking/platform/backends/wayland/actions.py`
  - future compositor-backed desktop actions

### What Should Stay Outside Backend Folders

Not everything should move under `backends/`.

These should remain backend-neutral if possible:

- `docking/platform/model.py`
  - canonical dock state and applet ownership
- `docking/platform/launcher.py`
  - desktop-file and launch metadata
- `docking/platform/environment.py`
  - environment/session tweaks and detection
- `docking/core/`
  - config, items, layout, theme, position
- most of `docking/ui/renderer.py`
  - visual rendering of dock items

These should become backend consumers rather than backend owners:

- `docking/app.py`
  - constructs the selected backend and passes it downward
- `docking/ui/factory.py`
  - asks the backend for visibility/edge integration collaborators
- `docking/ui/dock_window.py`
  - coordinates UI behavior through backend contracts
- `docking/ui/placement.py`
  - stays responsible for placement policy, not raw X11 calls
- `docking/ui/preview.py`
  - stays responsible for preview UI, not capture backend details

This separation matters because it prevents the tree from turning into
`backends/` versus "everything else still imports X11 directly anyway".

### Current Import Direction Rules

The code organization only helps if import direction is explicit.

Recommended dependency flow:

- `docking/core/`
  - imports nothing from backends
- `docking/platform/model.py`
  - imports backend-neutral types only
- `docking/platform/backends/*`
  - may import `core`, `launcher`, `model`, and external platform libraries
- `docking/ui/`
  - may import backend interfaces from `backends/base.py`
  - should not import `backends/x11/*` or `backends/wayland/*` directly
- `docking/applets/`
  - should depend on backend interfaces or applet-facing services
  - should not import `Wnck`, `GdkX11`, or backend implementation modules

In short:

- `x11` and `wayland` modules implement backend contracts
- the rest of the application should depend on contracts, not implementations

That is the architectural value of the folder split.

### Composition Root

The composition root is in:

- `docking/app.py`
- `docking/ui/factory.py`
- parts of `docking/ui/dock_window.py`

That should gradually become:

- `docking/app.py`
  - load config/theme/model/launcher
  - select backend
  - pass backend into UI/runtime assembly
- `docking/platform/backends/selection.py`
  - runtime backend choice
- `docking/ui/factory.py`
  - build the dock window using backend interfaces only

The important discipline is:

- backend selection happens once at startup
- implementation modules under `backends/x11` or `backends/wayland` are not
  chosen ad hoc throughout the UI layer

This avoids a common failure mode where a nominal backend system exists, but UI
code still contains scattered `if x11` logic and direct imports of backend
implementation modules.

### Applet-Facing Service Layer

Applets consume narrow backend services rather than importing full backend
objects or X11/Wayland libraries directly.

One useful refinement would be to avoid making applets import the full backend
object directly. Instead, expose small applet-facing services such as:

- `WorkspaceService`
- `DesktopActionService`
- `TaskService` if app/task state ever becomes applet-relevant

These services can be:

- backed by X11 today
- absent or capability-gated on reduced/Wayland backends later

That keeps applets from becoming coupled to the entire platform surface.

### How the Moves Mapped to Files

The refactor sequence was:

1. Add `docking/platform/backends/base.py`
   - define backend protocols and neutral dataclasses
2. Add `docking/platform/backends/x11/__init__.py`
   - define `X11SessionBackend`
3. Move the `WindowTracker` implementation under
   `backends/x11/impl/window_tracker.py` and expose it through
   `backends/x11/services/windows.py`
4. Move `docking/platform/dodge.py` logic under `backends/x11/impl/dodge.py` and expose monitor creation through `backends/x11/services/visibility.py`
5. Move `docking/platform/struts.py` and `docking/platform/barriers.py`
   under `backends/x11/impl/` and expose surface behavior through
   `backends/x11/services/surface.py`
6. Move X11 preview capture helpers under `backends/x11/impl/preview_capture.py` and expose capture through `backends/x11/services/previews.py`
7. Move Wnck workspace/desktop action code into backend-facing service modules

Temporary forwarding modules were useful only while a move was in progress.
The completed shape should keep X11 implementation code under `backends/x11/`
rather than leaving second homes in `docking/platform/`.

### Why `/x11` and `/wayland` Is Better Than Mixing by Feature Alone

An alternative structure would be to keep files like `window_tracker.py`,
`struts.py`, and `previews.py` at top level and later add `window_tracker_wayland.py`
or similar variants.

That is usually weaker for this kind of project because:

- backend ownership becomes hard to see
- import direction tends to get messy
- code often grows `if backend == ...` branches in shared modules
- partial ports leave both implementations intertwined

By contrast, explicit `x11/` and `wayland/` backend directories make it easier
to say:

- this module is backend implementation code
- this other module is backend-neutral coordination code

That clarity is worth a lot during a multi-phase port.

### Caution About Over-Abstracting Too Early

The `x11` and future `wayland` directories should not become an excuse to
invent a huge generic framework before it is needed.

Good abstraction here is:

- small capability interfaces
- neutral dataclasses where a real cross-backend concept exists
- one composition root that wires a backend once

Bad abstraction here is:

- dozens of speculative interfaces with only one implementation
- moving everything into `backends/` even when the code is really core/UI logic
- forcing fake parity between X11 and future Wayland behaviors

So yes, documenting and adopting `/x11` and `/wayland` backend modules is a
good idea. It is probably the cleanest structure for Docking. But it only pays
off if the split is paired with strict contract boundaries and import rules.

### Why This Was Incremental

The refactor deliberately optimized for intermediate states that were
useful and low risk.

Good intermediate states look like:

- no user-visible behavior change
- one X11 subsystem isolated behind a new interface
- tests still exercising the same runtime behavior
- less X11 leakage into unrelated modules than before

Bad intermediate states look like:

- partially implemented `wayland` modules with no caller
- giant renames with no functional boundary improvement
- a fake generic backend interface that still returns `Wnck.Window`
- broad "future-proofing" edits that change many files without tightening any
  real contract

### Detailed Staged Plan

The following sequence is intentionally conservative. Each phase should be able
to land independently.

Preparatory note:

- the architecture boundary is now documented in this file
- the numbered phases below assume new platform work follows those boundaries
- new X11-specific code outside backend modules should be treated as debt, not
  as the default pattern going forward

#### [x] Phase 1: Backend Interfaces and `X11SessionBackend`

Purpose:

- make the platform split explicit without changing behavior

Work:

- add `SessionBackend` and the small capability-specific interfaces in
  `docking/platform/backends/base.py`
- introduce neutral dataclasses and capability definitions that the rest of the
  application can depend on
- add `docking/platform/backends/x11/__init__.py` exporting
  `X11SessionBackend`
- add `docking/platform/backends/selection.py`
  - initially this can always select `X11SessionBackend`
- keep all actual logic in X11-backed code paths
- make startup construct `X11SessionBackend()` instead of wiring raw X11
  objects directly
- pass the backend into UI/runtime assembly from the composition root

Likely touch points:

- `docking/app.py`
- `docking/platform/__init__.py`
- new `docking/platform/backends/base.py`
- new `docking/platform/backends/x11/`
- new `docking/platform/backends/selection.py`
- `docking/ui/factory.py`

Important discipline:

- do not add a fake `WaylandSessionBackend` yet just to complete the shape
- do not move large subsystems yet
- keep existing X11 behavior intact behind the new composition root

Exit criteria:

- app startup depends on a backend object
- only one backend exists, and it is X11
- UI/runtime code receives backend contracts rather than constructing X11
  helpers directly
- behavior remains unchanged

#### [x] Phase 2: `WindowService`

Purpose:

- isolate the single most important X11 dependency first

Work:

- move the `WindowTracker` implementation under
  `backends/x11/impl/window_tracker.py` and expose it through
  `backends/x11/services/windows.py`
- keep Wnck internals inside the X11 implementation, but stop exposing
  `Wnck.Window` and raw XID lists upward
- define the `WindowService` contract around:
  - running-app aggregates
  - window handles
  - activation/minimize/close/cycle operations
  - active/urgent state
- preserve `DockModel.update_running()` as the aggregate sink, since that is
  already a good backend-neutral seam
- convert existing callers to depend on `backend.windows`

Likely touch points:

- new `docking/platform/backends/x11/services/windows.py`
- removed temporary shim `docking/platform/window_tracker.py`
- `docking/app.py`
- `docking/ui/dock_window.py`
- `docking/ui/preview.py`
- `docking/platform/model.py`
- any tests that currently assume XID lists as a public contract

Why this phase comes early:

- tasklist/running state is the central dependency that many other features use
- once this is backend-shaped, later work has a stable foundation

Exit criteria:

- callers talk to `WindowService`, not `WindowTracker`
- no non-backend module needs `Wnck.Window` in its public contract

#### [x] Phase 3: `PreviewService`

Purpose:

- isolate one of the most X11-specific UI subsystems

Work:

- move X11 thumbnail capture helpers into `backends/x11/impl/preview_capture.py` and expose them through `backends/x11/services/previews.py`
- define `PreviewService` around:
  - "capture preview image for this window handle"
  - "capture compact menu thumbnail for this window handle"
- make `PreviewPopup` depend on `backend.previews`
- replace direct `GdkX11.X11Window.foreign_new_for_display` and Wnck use in UI
  code with backend calls
- keep the current X11 preview behavior unchanged, including fallback behavior

Important design point:

- preview support should be modeled as optional capability, not baseline dock
  behavior

Why this phase is independent:

- previews are user-visible but modular enough to isolate without touching core
  placement or dodge logic

Exit criteria:

- `docking/ui/preview.py` no longer imports `GdkX11` or `Wnck`
- preview actions operate through `WindowService` and backend-neutral handles

#### [x] Phase 4: `SurfaceService`

Purpose:

- separate "dock UI geometry" from "platform edge/surface behavior"

Work:

- move X11 struts, pointer barriers, and blur-region helpers under
  `backends/x11/impl/`, and expose surface integration through
  `backends/x11/services/surface.py`
- define `SurfaceService` around:
  - surface/edge initialization
  - reserved space support
  - pointer barrier support
  - blur-region support where applicable
  - input-region and surface capability queries
- keep `DockPlacementController` as the coordinator for monitor choice and
  placement policy, but stop making it own raw X11 operations directly
- make `DockWindow` and placement code ask `backend.surface` what is supported
  rather than checking `GdkX11` types directly

Likely touch points:

- `docking/ui/placement.py`
- `docking/ui/dock_window.py`
- `docking/platform/backends/x11/impl/struts.py`
- `docking/platform/backends/x11/impl/barriers.py`

Why this phase matters:

- it untangles one of the most important conceptual confusions in the current
  code: monitor/layout policy versus X11-specific edge integration

Exit criteria:

- placement code coordinates platform behavior through `SurfaceService`
- raw `GdkX11` type checks are confined to X11 backend code

#### [x] Phase 5: `VisibilityService`

Purpose:

- make autohide overlap logic a backend capability instead of a universal dock
  assumption

Work:

- move the Wnck-based overlap monitor under `backends/x11/impl/dodge.py` and expose monitor creation through `backends/x11/services/visibility.py`
- define `VisibilityService` around:
  - overlap tracking support
  - visibility monitor creation
  - callbacks/signals for "should hide" state changes
- make `build_dock_window()` or equivalent composition root request a
  visibility monitor from the backend rather than importing X11 dodge logic
  directly
- allow a backend to say "overlap tracking unsupported" cleanly
- keep autohide policy in UI/runtime code, but make foreign-window overlap
  observation backend-owned

Likely touch points:

- `docking/ui/factory.py`
- `docking/platform/backends/x11/impl/dodge.py`
- `docking/ui/autohide.py`

Why this phase is useful even on X11:

- it makes autohide policy clearer
- it removes one more place where UI code assumes the ability to inspect other
  windows globally

Exit criteria:

- UI composition no longer imports `WindowDodgeMonitor` directly
- overlap-driven hiding is explicitly capability-backed

#### [x] Phase 6: Applet Capability Split

Purpose:

- stop treating all applets as if they are equally portable

Work:

- expose applet-facing services such as `WorkspaceService` and
  `DesktopActionService` rather than making applets depend on X11 modules
  directly
- move Wnck workspace and desktop-action logic into backend-owned service
  implementations
- gate workspace/desktop/window-killer applets on backend capabilities
- keep service-driven applets unchanged
- document which applets are:
  - backend-neutral
  - backend-dependent
  - unavailable on reduced backends

Likely touch points:

- `docking/applets/workspaces/applet.py`
- `docking/applets/desktop/applet.py`
- `docking/applets/windowkiller/applet.py`
- applet loading paths and capability checks

Why this phase matters:

- applets are one of the easiest places to overpromise Wayland support
- backend capability gating here provides an honest future user story

Exit criteria:

- Wnck applets no longer import Wnck outside backend implementations
- applet availability can be explained in terms of capabilities

#### [x] Phase 7: Reduced / Non-X11 Validation Backend

Purpose:

- prove that backend capability handling is real before native Wayland exists

Work:

- add a non-X11 backend used only for development/tests, or a reduced runtime
  mode that implements launcher-only behavior
- possible shapes:
  - `ReducedSessionBackend` for tests and contract validation
  - `ReducedSessionBackend` for launcher-shelf behavior
- make unsupported features fail closed and intentionally:
  - no tasklist
  - no previews
  - no workspaces
  - no overlap tracking
- verify that the dock can still render, launch pinned apps, and run
  backend-neutral applets without X11 integration

Why this is valuable:

- it tests the architecture before the project spends effort on real Wayland
  protocols
- it flushes out hidden X11 assumptions still leaking through the codebase

Important caution:

- this does not need to be shipped to users immediately
- it can be an internal validation backend first

Exit criteria:

- the application can run with a backend that intentionally lacks classic dock
  powers
- unsupported features degrade explicitly rather than crashing or depending on
  hidden X11 assumptions

#### [x] Phase 8: Actual Wayland Backend Work

Only after the earlier phases are complete does it make sense to begin actual
Wayland implementation work.

At that point the project can choose between at least two honest tracks:

- `normal-app Wayland backend`
  - likely wlroots/KWin/Cosmic first
- `GNOME shell-integration track`
  - for Ubuntu GNOME parity later

This is the point where protocol selection, compositor support matrices, and
GNOME-specific architectural decisions become productive rather than premature.

### Recommended Order of Attack

If the goal is smallest-risk progress, the best sequence is probably:

1. backend interfaces and `X11SessionBackend`
2. `WindowService`
3. `PreviewService`
4. `SurfaceService`
5. `VisibilityService`
6. applet capability split
7. reduced/non-X11 validation backend
8. actual Wayland backend work

This order is not arbitrary.

Why this order works:

- window tracking is the central dependency and unlocks cleaner contracts for
  later phases
- previews are highly X11-specific but reasonably self-contained
- placement/surface work is easier once startup and task tracking already speak
  backend contracts
- dodge and applet gating benefit from those earlier boundaries
- a reduced backend becomes much more useful after the first six phases

### What Each Intermediate Milestone Should Deliver

The project should avoid phases that are only "code churn".

Each milestone should produce one of these concrete improvements:

- fewer backend-specific imports in UI/core modules
- a smaller public surface for X11-only types
- a new capability check that prevents unsupported behavior from leaking
- a test seam that allows backend behavior to be mocked without weakening
  production contracts
- a launcher-only or reduced-capability runtime mode that can actually start

That gives the refactor a practical definition of success even before Wayland
is supported.

### Testing Strategy for the Refactor

The testing goal is not to invent fake generic behavior. It is to preserve
today's X11 behavior while tightening contracts.

Good test directions:

- contract tests for backend-neutral dataclasses and interface adapters
- behavior tests for `X11SessionBackend` using existing X11-backed integration
  points
- UI tests rewritten to depend on backend interfaces instead of Wnck objects
- applet tests that verify capability gating explicitly

Bad test directions:

- weakening production code with `getattr`/`hasattr` to support loose mocks
- pretending every backend supports every action
- hard-coding test-only platform fallbacks into runtime code

### Risks to Avoid During the Refactor

The most likely failure modes are:

- inventing a generic interface that is generic in name only
- keeping X11 types in public method signatures while claiming backend
  abstraction
- trying to solve GNOME Wayland strategy while basic backend isolation is still
  unfinished
- adding a partial Wayland backend too early and forcing the whole tree to
  absorb incomplete assumptions
- treating unsupported features as bugs instead of as explicit capability gaps

### What Success Looks Like Before Any Wayland Support Exists

The backend refactor is already valuable if, at the end of it:

- X11 remains the only fully supported runtime
- startup wires an explicit backend object
- UI/core layers stop importing Wnck and `GdkX11` directly
- Wnck applets are clearly marked as backend-dependent
- the application can be reasoned about in terms of platform capabilities
- adding a reduced backend or a real Wayland backend becomes incremental work
  rather than a redesign

That is the right standard for "further improvements" at this stage. The goal
is not to land Wayland immediately. The goal is to make future platform work
possible without a big bang.

## Recommended Engineering Direction

> **Status: Phases 1-4 complete.** The backend refactor is done, reduced
> Wayland is a working product mode, non-GNOME and GNOME Wayland tracks are
> separated, and the GNOME Shell bridge prototype validates the
> shell-integration approach. This section is preserved as the design
> rationale that guided the current architecture.

The detailed step-by-step roadmap is in the previous section. This section is
the shorter strategic reading of that roadmap.

### 1. Do the Backend Refactor First ✓

The backend refactor has been completed:

- X11 is an explicit backend under `backends/x11/`
- X11-only contracts are isolated behind backend service interfaces
- A reduced-capability runtime validates the architecture
- Multiple native Wayland backends are implemented on this foundation

### 2. Treat Reduced Wayland as a Valid Intermediate Product ✓

The reduced backend (`backends/reduced/`) is a working product mode:

- pinned launchers
- click to launch
- renderer/themes/zoom
- backend-neutral applets
- no tasklist, no previews, no workspace applet, no dodge/struts/barriers

It serves as the automatic fallback on unsupported Wayland sessions.

### 3. Separate Non-GNOME Wayland From GNOME Wayland ✓

These are now separate backend tracks:

- COSMIC, Hyprland, KWin, and generic wlroots backends for non-GNOME compositors
- GNOME Shell bridge backend for GNOME / Mutter

### 4. Revisit GNOME Only After the Backend Boundaries Exist ✓

The GNOME Shell bridge prototype validates the shell-integration approach.
The bridge exports window/workspace state and actions over D-Bus from a
GNOME Shell extension, consumed by a Python backend through backend-neutral
contracts. The GTK dock window is still an ordinary Wayland client (not a
Shell actor), so full GNOME dock parity (shell-owned surface, overview
integration) remains a separate frontend-scale project.

## Code Areas Most Relevant to a Port (Historical)

> These were the main porting hotspots identified before the backend refactor.
> All X11-bound platform pieces are now isolated under `backends/x11/`.
> UI code interacts with backend-neutral services. The Wayland backend
> implementations live under `backends/wayland/`, `backends/kwin/`, and
> `backends/gnome/`.

The following modules are the main porting hotspots.

### Core and UI pieces likely reusable

- `docking/core/config.py`
- `docking/core/theme.py`
- `docking/core/layout.py`
- `docking/core/items.py`
- `docking/ui/renderer.py`

These still need adaptation, but they are not fundamentally tied to X11.

### X11-bound platform pieces

- `docking/platform/backends/x11/impl/window_tracker.py`
- `docking/platform/backends/x11/impl/dodge.py`
- `docking/platform/backends/x11/impl/struts.py`
- `docking/platform/backends/x11/impl/barriers.py`

These should be assumed non-portable as currently designed.

### UI code with X11/global-coordinate assumptions

- `docking/ui/dock_window.py`
- `docking/ui/placement.py`
- `docking/ui/preview.py`
- `docking/ui/display.py`
- parts of `docking/ui/menu.py`
- parts of `docking/ui/tooltip.py`

These will need redesign even if the renderer survives intact.

### Applets with X11/Wnck service dependencies

These applets no longer import Wnck/Xlib/GdkX11 directly, but their feature
semantics still require backend services:

- `docking/applets/workspaces/applet.py`
- `docking/applets/desktop/applet.py`
- `docking/applets/windowkiller/applet.py`
- `docking/applets/deskpresence/applet.py`
- `docking/applets/colorpicker/applet.py`

Native Wayland support for them should be treated as feature-specific backend
work, not as incidental applet fixes.

## Open Questions

### Resolved

These questions were answered through implementation:

- **Is the first goal "launcher shelf on Wayland" or "full dock on Wayland"?**
  Both. The reduced backend provides launcher-shelf mode on unsupported
  compositors; rich backends (COSMIC, Hyprland) provide full dock behavior
  where compositor protocols permit it.

- **Is GNOME a mandatory first-class target, or is "Wayland support on some
  compositors first" acceptable?** Wayland support on some compositors first
  was the chosen path. COSMIC, Hyprland, KWin, and generic wlroots backends
  were implemented. GNOME/Mutter native parity requires the Shell bridge
  extension, which is functional for window/workspace state and actions but
  does not provide native layer-shell edge placement.

- **Is a GNOME Shell extension acceptable as part of the project architecture?**
  Yes. The GNOME Shell bridge extension ships with Docking under
  `docking/platform/backends/gnome/extension/`.

- **Is an XWayland fallback/workaround worth documenting?** Yes — the
  `GDK_BACKEND=x11` XWayland compatibility path is documented in this file
  with known limitations.

- **What should the backend interface look like?** The backend interface is
  `SessionBackend` composed of capability-specific services (`WindowService`,
  `SurfaceService`, `VisibilityService`, `PreviewService`,
  `WorkspaceService`, `DesktopActionService`, `ScreenCaptureService`,
  `IdleService`, `WindowPickService`) with `PlatformCapabilities` flags.
  All are defined in `docking/platform/backends/base.py`.

- **Which applets must work in the first Wayland-capable release?**
  Backend-neutral applets (clock, weather, battery, volume, etc.) work across
  all backends. X11/Wnck-dependent applets (Workspaces, Desktop, Window Killer,
  Color Picker, Desk Presence) are gated on backend capabilities and degrade
  cleanly when unsupported.

- **Which X11-only features are acceptable to drop or defer?** Previews are an
  optional capability; window picking/killer is X11-only; pointer barriers have
  no normal-client Wayland equivalent; X11-style struts are replaced by
  layer-shell exclusive zones.

- **Should previews be treated as an optional capability?** Yes.
  `PreviewService` is a backend capability; COSMIC provides native toplevel
  image capture; other backends fall back to app icon/title.

- **Should unsupported applets be explicitly disabled?** Yes. Backend
  capability flags gate applet availability; settings UI greys out
  unsupported features with explanatory tooltips.

### Still Open

- **PR 19d: Wayfire IPC backend** — not yet implemented. Wayfire requires the
  `ipc` plugin and companion plugins (`ipc-rules`, `wm-actions`) which vary
  by user configuration.

- **Niri IPC backend** — implemented. Uses Niri's JSON IPC socket for window
  tracking and actions with event-stream updates.

- **Multi-monitor behavior** on non-X11 backends needs broader testing.

- **GNOME Shell extension review/distribution** — the bridge extension works
  locally but has not been submitted to extensions.gnome.org.

## Suggested Support Language

When documenting Wayland support publicly, avoid ambiguous statements like
"Wayland supported". Current precise language:

- **X11:** full support
- **X11 via XWayland** (`GDK_BACKEND=x11`): Docking may launch as an X11 client
  inside a Wayland session; task/window integration is incomplete for native
  Wayland apps and unsupported
- **COSMIC native Wayland:** richest native support — running indicators,
  window actions, workspaces, overlap-driven autohide, preview image capture
- **Hyprland native Wayland:** IPC-based window tracking, actions, and
  workspaces
- **Niri native Wayland:** IPC-based window tracking, actions, and
  layer-shell placement
- **KDE Plasma 6 native Wayland:** layer-shell placement and workspace support;
  window tracking is limited (AT-SPI best-effort)
- **Generic wlroots native Wayland** (Sway, river, labwc): layer-shell placement;
  running indicators and window actions where `wlr-foreign-toplevel-management`
  is available; no overlap/preview support
- **GNOME / Mutter via Shell bridge:** window/workspace state and actions
  through the GNOME Shell bridge extension; not a full GNOME dock
- **Reduced mode:** launcher shelf only; no taskbar/window-management features

## Current Recommendation

> **Status: Updated 2026-06-10.** The recommendations below have been acted on.

The most realistic interpretation of "make Docking available on Wayland" has been
implemented as:

1. ✓ Backend-sensitive code split from reusable UI/core logic
2. ✓ Reduced native Wayland launcher shelf mode implemented
3. ✓ Compositor families with public dock/taskbar protocols targeted first
   (COSMIC, wlroots via `wlr-foreign-toplevel-management`, Hyprland via IPC)
4. ✓ GNOME Wayland parity treated as a separate shell-integration effort
   (GNOME Shell bridge prototype complete)

Remaining compositor integration work:

- **Wayfire** — IPC backend not yet implemented (plugin-dependent)
- **Niri** — implemented; JSON IPC for window tracking, actions, and
  event-stream updates
- **GNOME Shell frontend** — the bridge provides window/workspace state and
  actions; a full GNOME Shell frontend (shell-owned dock surface, overview
  integration) would be a separate project comparable to Dash to Dock
- **KWin 6 window tracking** — currently limited to AT-SPI; full parity
  depends on KWin adding a public window-list protocol or D-Bus API

## Current XWayland Instability Investigation

This section documents a separate problem from general "Wayland feature
support". Even when Docking launches successfully through `XWayland`, the dock
surface is currently unstable in long or interaction-heavy runs.

This section should be treated as a living investigation log. As new traces,
repro improvements, and reduction results are discovered, they should be added
here so the document becomes the running record of how the issue is being
narrowed down.

### Observed Symptoms

The failures seen so far are presentation failures, not full application
crashes.

Observed variants:

- the dock freezes visually after some interactions while hover, clicks, and
  logs keep working
- the last dock frame stays stuck on screen, for example with an icon frozen in
  a half-zoomed state
- the dock can appear effectively transparent or absent while tooltip popups
  still appear at the expected dock position

Important interpretation:

- tooltip popups are separate windows, so "tooltips still work" does not mean
  the main dock surface is still repainting
- in traced failing runs, Docking logic remained alive while draw delivery to
  the main dock surface stopped
- one healthy baseline run under `XWayland` also recorded
  `compositor_active=False`, which matters because Docking is an RGBA,
  compositor-managed dock window

### What We Confirmed

From traced runs of the real app:

- the dock can stop receiving draw callbacks while hover, tooltip, autohide,
  and click logic continue
- a later screenshot of a half-zoomed dock did not represent a live animation;
  it was a stale last frame that remained on screen after draw delivery stopped
- in another class of report, the dock appeared visually absent while tooltips
  still appeared, which is consistent with the same "logic alive, presentation
  broken" family of failures

Current working hypothesis:

- the main problem is below Docking's state machine
- the likely failure layer is GTK/XWayland/Mutter presentation or draw
  delivery for this specific kind of transparent RGBA dock window

### Reproduction Script

To avoid repeatedly instrumenting the main app, the investigation now uses:

- `tools/xwayland_repro.py`

That script is a reduction matrix, not a full Docking clone. It exists to
toggle one window or rendering trait at a time and answer:

- does the failure require a dock-type window hint?
- does it require RGBA transparency?
- does it require keep-above / sticky behavior?
- does it require hide/show transitions?
- does it require motion-driven redraw churn?
- does it depend on an offscreen `OPERATOR_SOURCE` blit path like the real app?

Current repro features:

- X11/XWayland launch path via `GDK_BACKEND=x11`
- dock-style window flags
- RGBA visual
- autohide `off` / `snap` / `animate`
- optional tick pump and redraw watchdog
- optional motion spam
- optional offscreen blit path to match Docking more closely
- trace logging for draw delivery, redraw requests, and stall detection

The repro is intentionally smaller than Docking. It still does not model every
part of the real application. Notable Docking behaviors that remain candidates
for triggering the real bug include:

- tooltip popup windows
- X11 input-shape updates
- blur hint updates
- richer item rendering and hover-zoom behavior
- the full applet/model/runtime stack

### Web Research Summary

Web research did not find an exact public report for "GTK3 dock under
XWayland freezes while tooltips still work", but it did find related evidence
that this class of bug is credible upstream.

Most relevant findings:

- GNOME / Mutter has had XWayland freeze bugs where windows become visually
  stuck during interaction while the application itself is still alive
- there are separate reports involving popups, drag interactions, or redraw
  failures under XWayland on GNOME / Mutter
- there are transparency-related compositor bugs in nearby stacks, which is
  relevant because Docking uses an RGBA, transparent, compositor-managed window

Most relevant references:

- GNOME Discourse report of buggy / frozen XWayland windows during popup/drag
  interaction:
  https://discourse.gnome.org/t/buggy-xwayland-windows-when-used-with-graphics-tablet/29611
- Mozilla bugs for XWayland / GNOME window freeze behavior during interaction:
  https://bugzilla.mozilla.org/show_bug.cgi?id=1919397
  https://bugzilla.mozilla.org/show_bug.cgi?id=1827210
- Ubuntu / Mutter redraw and presentation bug references in nearby areas:
  https://bugs.launchpad.net/bugs/2054510
  https://bugs.launchpad.net/bugs/2107245
- Transparency-related compositor bug reference:
  https://bugs.launchpad.net/bugs/2099879
- GTK / GDK window documentation:
  https://gnome.pages.gitlab.gnome.org/gtk/gdk3/class.Window.html
- Mutter `WindowActor.freeze` API documentation:
  https://gnome.pages.gitlab.gnome.org/mutter/meta/method.WindowActor.freeze.html

These are not proof of Docking's exact failure, but together they support the
conclusion that this is plausibly an upstream presentation problem rather than
only an application-state bug.

### Next Steps

The next useful work is reduction, not more invasive tracing in the main app.

Immediate plan:

1. continue narrowing the repro toward the smallest failing combination
2. prioritize missing main-surface behaviors over tooltip work:
   - add X11 input-shape updates to the repro
   - then add blur-region hints
   - then add richer Docking-like hover/zoom churn
3. treat tooltip popup support as secondary evidence, not the primary trigger,
   because the real app already showed that tooltips can remain alive after the
   main dock surface freezes
4. compare healthy versus failing combinations, not just failing runs in
   isolation
5. if the repro becomes small and reliable enough, prepare an upstream-quality
   bug report with:
   - exact command
   - expected result
   - actual result
   - environment snapshot
   - minimal failing matrix combination

## Native Wayland Implementation Plan

> **Status: Largely implemented.** This section describes the target
> architecture that guided the current implementation. The backend refactor
> (PRs 1-18), compositor-specific backends (PRs 19a-c, 19e-f), and the
> capability model below are now the codebase reality. Wayfire (19d) and
> Niri remain unimplemented.

This section is the concrete plan for reaching the same class of Wayland
support as Cairo-Dock while preserving the existing X11 userbase.

The target is not "one dock that works everywhere on Wayland". Cairo-Dock does
not achieve that either. The realistic target is:

- keep the current X11/Wnck implementation as the default, proven path for X11
  and XWayland
- add a native Wayland path for compositors that expose the protocols a
  third-party dock needs
- make unsupported compositor/feature combinations degrade explicitly rather
  than silently regressing X11 behavior

### Cairo-Dock Baseline

The Cairo-Dock Wayland implementation establishes the useful bar:

- use `gtk-layer-shell` / `wlr-layer-shell` for dock placement, edge anchoring,
  keep-above/below, and exclusive-zone reservation
- use compositor window-management protocols for taskbar state:
  - `wlr-foreign-toplevel-management` for wlroots-style compositors
  - `plasma-window-management` for KWin / Plasma
  - COSMIC `toplevel-info`, `toplevel-management`, `ext-foreign-toplevel-list`,
    and workspace protocols for COSMIC
- add compositor-specific integration where generic protocols are not enough:
  - KWin for window geometry, virtual desktops, stacking order, show desktop,
    PID / kill, and richer capabilities
  - Wayfire IPC for scale/expo, sticky/above, keybindings, menu properties, and
    overlap tracking
  - Niri IPC for overview-style actions
  - COSMIC overlap notification for dodge-style hiding
- explicitly exclude GNOME Shell / Mutter from native third-party dock support
  unless implemented as a GNOME Shell extension or private Shell integration

Cairo-Dock's important trick is architectural, not a security bypass: it
registers Wayland globals, binds only protocols the compositor advertises, then
normalizes those events into its existing window-manager abstraction.

### Current Docking X11 Assumptions

Docking currently has several X11 assumptions that must remain stable until a
native Wayland backend is feature-complete enough to enable intentionally.

`docking.platform.window_tracker.WindowTracker` is the core X11 taskbar
backend:

- imports `Wnck` and `Gtk` directly
- scans `Wnck.Screen.get_windows()`
- filters `Wnck.WindowType.DESKTOP` and `Wnck.WindowType.DOCK`
- matches windows through class-group, WM_CLASS instance, and desktop-file
  heuristics
- stores XIDs in `RunningWindowInfo` / `RunningAppInfo`
- uses Wnck objects and XIDs for activate, minimize, close, focus cycling,
  window titles, and preview handoff

`docking.platform.running` is also X11-shaped today:

- `RunningWindowInfo.xid` is required
- `RunningAppInfo.xids` is the stable handoff for previews and menus
- `RunningWindowInfo.window` carries a live Wnck object

`docking.ui.preview` is backend-service based:

- preview UI consumes `WindowSnapshot` / `WindowId`
- X11 thumbnail capture lives in `docking.platform.backends.x11.services.previews`
- activation routes through `WindowService`

`docking.ui.menu` is backend-service based for open-window rows:

- lists `WindowSnapshot` rows through `WindowService`
- activates and closes by `WindowId`
- X11 compact thumbnails are provided by `PreviewService`

`docking.platform.backends.x11.impl.dodge.WindowDodgeMonitor` is Wnck-specific:

- listens to Wnck screen/window signals
- reads active workspace, active window, geometry, maximized state, and window
  type
- implements all current overlap-based hide modes from those X11 concepts

`docking.ui.placement` is backend-service based, while X11 edge-integration
details live under `docking.platform.backends.x11.impl`:

- struts use `_NET_WM_STRUT_PARTIAL` through Xlib
- blur hints use `_DOCKING_BACKGROUND_BLUR_REGION` through Xlib
- pointer barriers use XFixes/XInput2 through
  `docking.platform.backends.x11.impl.barriers`
- placement already guards struts/barriers with `GdkX11.X11Display` /
  `GdkX11.X11Window`, which is a useful pattern to preserve

Several applet features are X11/Wnck/Xlib-bound through backend services:

- Desktop show-desktop is backed by Wnck on X11
- Workspaces are backed by Wnck on X11
- Window Killer uses Wnck window picking and PID lookup on X11
- Desk Presence idle tracking uses XScreenSaver/Xlib on X11
- Color Picker samples the X11 root window on X11 and is expected to fail for
  native Wayland contents without a portal/compositor capture backend

The current app bootstrap now wires an explicit X11 session backend. A native
Wayland port should add new backend implementations rather than reintroducing
direct Wnck/X11 ownership in UI or applet modules.

### Non-Negotiable X11 Compatibility Rules

The current X11 behavior is the production baseline. Wayland work must follow
these rules:

1. Do not remove or rewrite the Wnck tracker while adding Wayland support.
2. Do not change the public behavior of `WindowTracker` until an adapter
   interface exists and X11 tests cover it.
3. Do not make `GdkX11`, `Wnck`, Xlib, XFixes, or XInput imports mandatory for
   the native Wayland backend path.
4. Do not make `gtk-layer-shell`, Wayland scanner output, pywayland bindings, or
   any compositor protocol dependency mandatory for the X11 path.
5. Select the backend at runtime from the actual GTK display/session, not from
   distro, desktop name, or wishful configuration.
6. Keep the X11 code path used for:
   - real X11 sessions
   - `GDK_BACKEND=x11` in a Wayland session
   - automated tests that mock Wnck/GdkX11 behavior
7. Treat unsupported native Wayland features as capability gaps, not errors.
   The dock should keep running with disabled previews/dodge/window actions when
   the compositor does not expose the required protocol.

### Target Architecture

Add a platform backend layer rather than making UI code branch on X11 versus
Wayland everywhere.

The earlier shorthand of "add a `WindowService`" is not sufficient by itself.
Window tracking is the biggest dependency, but it is only one platform service.
Docking also needs platform-owned services for surface roles, screen
reservation, visibility/dodge, previews, workspaces, show-desktop actions,
screen capture, idle detection, and window picking. If only the taskbar tracker
is abstracted, native Wayland will still leak X11 assumptions through previews,
menus, applets, placement, and imports.

The target should be a `SessionBackend` composed of small services:

```text
docking.platform.backends
  base.py
    SessionBackend
    PlatformCapabilities
    WindowService
    SurfaceService
    VisibilityService
    PreviewService
    WorkspaceService
    DesktopActionService
    ScreenCaptureService
    IdleService
    WindowPickService
    WindowSnapshot
    WindowId
    ActionResult

  selection.py
    create_session_backend(...)

  x11/
    X11SessionBackend
    WnckWindowService
    X11SurfaceService
    X11VisibilityService
    X11PreviewService
    WnckWorkspaceService
    WnckDesktopActionService
    X11IdleService
    X11WindowPickService

  wayland/
    WaylandRegistry
    WaylandSessionBackend
    WaylandLayerShellSurfaceService
    WaylandForeignToplevelWindowService
    WaylandPlasmaWindowService
    WaylandCosmicWindowService
    WaylandPlasmaWorkspaceService
    WaylandCosmicWorkspaceService
    PortalScreenCaptureService
    NoopWindowService
    NoopVisibilityService
```

The session backend should be selected once, early in startup, from the actual
GTK display and runtime protocol availability:

```text
class SessionBackend:
    name
    display_server  # "x11", "wayland", or "none"
    capabilities
    windows
    surface
    visibility
    previews
    workspaces
    desktop_actions
    screen_capture
    idle
    window_picker

    start()
    stop()
```

The UI should depend on backend-neutral operations. For windows, that means:

```text
WindowService.start()
WindowService.stop()
WindowService.list_windows(desktop_id) -> tuple[WindowSnapshot, ...]
WindowService.list_preview_windows(desktop_id) -> tuple[WindowSnapshot, ...]
WindowService.icon_name_for_desktop(desktop_id) -> str
WindowService.activate(window_id) -> ActionResult
WindowService.activate_most_recent(desktop_id) -> ActionResult
WindowService.cycle(desktop_id) -> ActionResult
WindowService.minimize_all(desktop_id) -> ActionResult
WindowService.close(window_id) -> ActionResult
WindowService.close_all(desktop_id) -> ActionResult
WindowService.close_focused() -> ActionResult
WindowService.toggle_focus(desktop_id) -> ActionResult
```

The action return value matters. The current Wnck code is mostly fire-and-forget,
but native Wayland protocols have many optional actions. The caller needs to
know whether an action succeeded, was unsupported, targeted a stale window, or
failed:

```text
ActionResult.OK
ActionResult.UNSUPPORTED
ActionResult.NOT_FOUND
ActionResult.FAILED
```

`WindowSnapshot` should replace direct Wnck/XID exposure at UI boundaries:

```text
id: WindowId
desktop_id: str
app_id: str | None
wm_class: str | None
title: str
active: bool
urgent: bool
minimized: bool | None
maximized: bool | None
fullscreen: bool | None
geometry: Rect | None
workspace_id: str | None
can_activate: bool
can_minimize: bool
can_close: bool
can_preview: bool
```

The backend owns the mapping from `WindowId` to a live `Wnck.Window`, Wayland
protocol handle, or compositor-specific object. On X11, `WindowId.value` is the
XID. On Wayland, it must be an internal stable handle for the compositor
toplevel object, not an XID.

`RunningAppInfo` should grow neutral IDs before any native Wayland backend is
enabled:

```text
RunningWindowInfo.window_id: WindowId
RunningWindowInfo.xid: int | None
RunningAppInfo.window_ids: tuple[WindowId, ...]
RunningAppInfo.xids: tuple[int, ...]  # X11 compatibility for existing model state
```

This lets preview/menu/action code use `WindowId` while existing X11 model state
and tests continue to expose XIDs for compatibility.

### Service Boundaries

The service split should be explicit because each service has different
Wayland constraints.

`WindowService` owns taskbar state and actions:

- X11: current Wnck tracker
- wlroots: `zwlr_foreign_toplevel_manager_v1`
- KWin: `org_kde_plasma_window_management`
- COSMIC: `ext_foreign_toplevel_list_v1` plus COSMIC management protocols
- unsupported native Wayland: no-op service with launcher-only behavior

`SurfaceService` owns dock surface roles and edge integration:

- X11: `WindowTypeHint.DOCK`, keep-above, struts, blur hints, pointer barriers,
  and X11 input-region behavior
- native Wayland: layer-shell setup, anchors, exclusive zones, monitor
  assignment, layer choice, and Wayland-safe input-region behavior
- unsupported native Wayland: normal GTK window or reduced/no-op surface
  behavior with clear logging

This service needs lifecycle hooks, not just a "set struts" method:

```text
configure_before_realize(window)
on_realize(window)
position_or_anchor(request)
set_reservation(request)
clear_reservation()
update_input_region(rect)
set_blur_region(rect)  # optional, currently X11-specific
```

Layer-shell setup usually has to happen before the window is first mapped. If
the abstraction is introduced too late in `DockPlacementController.on_realize`,
native Wayland may already have received an ordinary toplevel role.

`VisibilityService` owns foreign-window overlap observation:

- X11: current Wnck-based `WindowDodgeMonitor`
- KWin: geometry/workspace/active-window based monitor
- COSMIC: overlap notification based monitor
- generic wlroots: likely unsupported unless compositor-specific IPC is added

The service creates overlap monitors when the selected backend can support them:

```text
create_monitor(get_dock_rect, on_change) -> VisibilityMonitor | None
```

If unsupported, the runtime should degrade to normal autohide behavior for that
session and log the capability gap without mutating the saved user config.

`PreviewService` owns preview image capture. UI owns icon/title fallback when
capture is unavailable:

- X11: current XID / `GdkX11.X11Window.foreign_new_for_display` capture
- native Wayland: return preview images only if compositor support exists;
  otherwise let UI fallback render the app icon/title
- portal capture may help screenshot-style workflows, but should not be assumed
  to provide per-window live thumbnails

Window listing and preview capture must be separate. Native Wayland can often
list windows without being able to capture their pixels.

`WorkspaceService` and `DesktopActionService` are required for applets and some
window-filtering behavior:

- X11: Wnck workspaces and show-desktop
- KWin: Plasma virtual desktops and show-desktop
- COSMIC/ext-workspace: workspace protocol support
- generic wlroots: optional, only if the compositor exposes workspace support

`ScreenCaptureService`, `IdleService`, and `WindowPickService` cover applet
features that cannot be hidden inside `WindowService`:

- Color Picker needs portal/compositor capture, not X11 root-window sampling
- Desk Presence needs an idle source; Xss is X11-only
- Window Killer needs a window-pick/PID/kill service; generic Wayland should
  not claim this capability

### Import and Startup Constraints

Backend selection now happens before UI composition decides which concrete
platform services to use. That ordering must be preserved for native Wayland:
backend-neutral startup code should not import X11-only libraries before the
selected backend is known.

The startup shape is:

```text
config = Config.load()
launcher = Launcher()
model = DockModel(...)
backend = create_session_backend(config=config, launcher=launcher, model=model)
window = build_dock_window(..., backend=backend)
backend.start()
```

Optional platform dependencies must be imported inside backend implementations
or factories, not by the top-level app or backend-neutral UI modules.

The important import boundary is:

- backend-neutral UI code can import service contracts and neutral dataclasses
- X11-only modules can remain under `docking.platform.backends.x11`
- native Wayland/reduced backends must be selectable without backend-neutral
  modules importing `GdkX11`, `Wnck`, Xlib, or XFixes at import time

### Application Matching

The current `WindowMatcher` is built around X11 WM_CLASS and Wnck class-group
names. Wayland uses compositor `app_id`, which is similar in purpose but not
the same identity source. The matcher should become an explicit
source-aware service rather than mixing the two models silently.

Suggested shape:

```text
ApplicationMatcher.match_x11(
    wm_class,
    class_instance,
    class_group,
) -> desktop_id | None

ApplicationMatcher.match_wayland(
    app_id,
    title=None,
) -> desktop_id | None
```

Wayland matching should try:

- exact visible pinned aliases
- exact app ID as a desktop ID
- `{app_id}.desktop`
- Flatpak-style IDs
- lowercase / hyphen / no-space normalization
- installed desktop-file reverse lookup
- `StartupWMClass` fallback when app IDs are poor

This preserves the existing X11 matcher while making Wayland identity bugs
observable in logs.

### Capability Model

Capabilities should be fine-grained enough for UI, hide modes, and applets to
make correct decisions.

Window/task capabilities:

```text
tracks_windows
tracks_active_window
tracks_attention
tracks_minimized
tracks_maximized
tracks_fullscreen
tracks_stacking_order
supports_activate
supports_minimize
supports_close
supports_window_menu
```

Geometry/workspace capabilities:

```text
tracks_window_geometry
tracks_window_workspace
supports_current_workspace_filter
supports_workspace_list
supports_workspace_switch
supports_show_desktop
```

Surface/visibility capabilities:

```text
supports_layer_shell
supports_screen_reservation
supports_input_region
supports_pointer_barrier
supports_background_blur_hint
supports_overlap_active
supports_overlap_any
supports_overlap_maximized
```

Applet/service capabilities:

```text
supports_screen_color_pick
supports_idle_time
supports_window_pick
supports_window_pid
supports_process_kill
```

Avoid broad flags such as `supports_wayland` or `supports_taskbar`; they are
too coarse to drive real behavior.

### Migration Order From Current Code

The safest first moves are still X11-preserving refactors:

1. Define `WindowId`, `WindowSnapshot`, `ActionResult`, and `WindowService`.
2. Add an X11 adapter around the current `WindowTracker` without changing
   behavior, and add `window_ids` alongside existing XIDs in running-state
   dataclasses. This is the combined PR 2 + PR 3 step and is already merged.
3. Wire the X11 window service into startup behind an X11-only backend/factory
   path.
4. Convert `MenuHandler` from Wnck windows/XIDs to `WindowSnapshot`.
5. Convert `PreviewPopup` from XID lists to `WindowSnapshot` plus
   `PreviewService`.
6. Move dodge creation behind `VisibilityService`.
7. Move struts/barriers/blur/input-region ownership behind `SurfaceService`.
8. Remove transitional X11 compatibility APIs after all X11 UI callers use
   backend-neutral services.
9. Add a `ReducedSessionBackend` or reduced backend and verify Docking can run
   without Wnck task powers.
10. Only after that, start layer-shell and Wayland toplevel implementation.

The key test before real Wayland code is: X11 should first run entirely through
backend-neutral services, with XID/Wnck compatibility APIs removed
from backend-neutral UI paths. After that, Docking should be able to run with a
backend that intentionally lacks taskbar, preview, workspace, and overlap
powers. That flushes out hidden X11 assumptions before compositor protocols are
involved.

### Behavior-Parity Tests

Each migration PR that moves behavior behind a new service boundary must include
a behavior-parity test at that boundary. The test should prove not only that the
new API returns a plausible result, but that it preserves the important old
mechanism and ownership split.

For example, when moving preview capture out of `docking.ui.preview`, the test
must encode that the old popup path captured directly from the XID, did not ask
Wnck whether the window was minimized, and left app-icon fallback rendering to
the UI after capture returned `None`. Without that kind of test, a refactor can
look structurally correct while still changing visible behavior during pointer
movement, hide transitions, or transient window-state changes.

Before merging a service-boundary PR, explicitly identify the old call path that
matters and add at least one regression test that would fail if the new service
quietly switched to a different lower-level mechanism.

### X11 Window-Service Migration Shape

Current X11 runtime path:

```text
docking.app
  |
  +--> X11SessionBackend
       |
       +--> X11RuntimeServices
            |
            +--> X11WindowService -> impl/window_tracker.py -> Wnck.Screen
            +--> X11PreviewService -> impl/preview_capture.py
            +--> X11SurfaceService -> impl/struts.py + impl/barriers.py
            +--> X11VisibilityService -> impl/dodge.py
            +--> applet services for workspaces, actions, picking, idle, capture
```

Backend-neutral callers now use service methods:

```text
docking.app
  |
  +--> create_session_backend()
       |
       +--> X11SessionBackend
            |
            +--> backend.windows.list_windows() -> WindowSnapshot + WindowId.x11(xid)
            +--> backend.windows.activate(WindowId), close(WindowId), cycle(...)
            +--> backend.previews.capture(WindowId)
            +--> backend.surface / visibility / applet services
            |
            +--> DockModel.update_running(unchanged X11 aggregate)
```

PRs 2 through 11 are now merged: Docking has the X11 session backend, concrete
X11 service adapters, `WindowId` values beside existing XIDs, menu and preview
UI backed by snapshots/services, and applets consuming applet-facing services.
`X11WindowService` remains the only X11 window-service construction path, and
its startup is guarded so Wnck screen signals are not connected twice.

### Completed PR Order (Historical)

The PR sequence below was designed to keep every step reviewable and preserve
the existing X11 runtime after each merge. All PRs 1-18 and sub-PRs 19a-c,
19e-f are merged. Wayfire (19d) remains unimplemented.

#### PR Summary

All backend-refactor PRs (1-18) and compositor-specific PRs (19a-c, 19e-f)
are merged. Each step preserved the existing X11 runtime while isolating
platform-specific behavior behind backend contracts.

| PR | Scope | Key Deliverable |
| --- | --- | --- |
| 1 | Backend contracts | `base.py`: `SessionBackend`, `WindowService`, `SurfaceService`, `VisibilityService`, `PreviewService`, `PlatformCapabilities`, `WindowId`, `WindowSnapshot`, `ActionResult` |
| 2-3 | X11 window adapter | `X11WindowService` wrapping Wnck tracker; `WindowId` and `window_ids` alongside existing XIDs |
| 4 | X11 runtime wiring | `docking.app` constructs `X11SessionBackend`; Wnck signal connection made idempotent |
| 5 | Menu window rows | `MenuHandler` uses `WindowSnapshot` / `WindowId` instead of Wnck windows / XIDs |
| 6 | Preview popup | `PreviewPopup` uses `PreviewService` / `WindowSnapshot`; `GdkX11`/`Wnck` removed from `docking/ui/preview.py` |
| 7 | Session backend shape | `X11SessionBackend` complete with all services; `selection.py` added; `docking.app` wires backend once |
| 8 | Visibility service | `WindowDodgeMonitor` behind `VisibilityService`; `docking.ui.factory` no longer imports dodge directly |
| 9 | Surface service | Struts, barriers, blur hints, input-region behind `SurfaceService`; `GdkX11` checks confined to X11 backend |
| 10 | Applet services | `WorkspaceService`, `DesktopActionService`, `WindowPickService`, `IdleService`, `ScreenCaptureService` extracted; Wnck-dependent applets consume backend services |
| 11 | Cleanup | Transitional X11 compatibility APIs removed from backend-neutral paths |
| 12 | X11 hardening | Dodge geometry fix; active-display polling cleanup on destroy; Window Killer PID ownership; Workspaces applet watch idempotency |
| 13 | Reduced backend | `ReducedSessionBackend` with no-op services; validates architecture; `DOCKING_BACKEND=reduced` |
| 14 | Wayland detection | GTK Wayland display detection; native Wayland selects reduced/no-op; X11 unchanged |
| 15 | Layer-shell surface | `WaylandLayerShellSessionBackend`; `gtk-layer-shell` integration for native dock surface placement |
| 16 | Foreign-toplevel | `WaylandForeignToplevelWindowService`; `wlr-foreign-toplevel-management` via vendored protocol + `pywayland`; `WaylandAppIdMatcher` |
| 17 | Workspace/idle/portal | `WaylandWorkspaceService` via `ext_workspace_v1`; `WaylandPortalColorPickerService` via XDG Desktop Portal |
| 18 | Protocol runtime | `WaylandProtocolRuntime` with separate `pywayland` `Display`, `wl_registry` binding, GLib fd integration, event dispatch |
| 19a | COSMIC backend | `CosmicSessionBackend` with native toplevel info/management, workspaces, overlap notify, image-capture previews |
| 19b | Hyprland backend | IPC event socket for windows/workspaces; short-lived command socket for actions; `hyprctl -j` snapshots |
| 19c | KWin backend | `KWinSessionBackend` with D-Bus `VirtualDesktopManager` for workspaces, AT-SPI window tracking, layer-shell surface |
| 19e | GNOME bridge | GNOME Shell extension (`extension.js`) + Python `GnomeShellBridgeSessionBackend`; D-Bus window/workspace/action bridge |
| 19f | — | Extension loads, D-Bus state export, backend integration complete |

**Not yet implemented:** Wayfire IPC backend (PR 19d).

### Advance Research Findings Before More Wayland Work

These findings cut across PR16-19 and should guide implementation order before
more compositor-specific code lands.

#### Protocol Binding Strategy

Docking should treat Wayland protocol bindings as source-controlled interface
artifacts, not as invisible local setup.

Practical rules:

- use `pywayland` as the optional runtime binding for direct Wayland protocol
  clients
- vendor protocol XML plus generated bindings for protocols that `pywayland`
  does not ship, such as wlroots or compositor-specific protocols
- keep the XML source next to generated code and include it in package data
- document the upstream source URL and protocol version for each vendored XML
- avoid generating bindings implicitly during normal installation unless the
  build system has a clear, reproducible scanner step
- keep generated code isolated under a `protocols/` package and keep Docking
  logic in service/adapters

Risks:

- staging/unstable/compositor-specific protocols can be replaced or revised
- generated bindings can drift from XML if updates are manual
- different protocol families may need different source repositories
  (`wayland-protocols`, `wlr-protocols`, COSMIC protocols, Plasma protocols)

Recommended next step:

- add a small developer tool or documented command that regenerates vendored
  bindings from vendored XML and compares the result

#### Wayland Event-Loop Integration

The critical runtime spike is integrating a direct Wayland client connection
with GTK's GLib main loop.

Preferred first spike:

- create a separate `pywayland.client.Display` connection instead of borrowing
  GTK's internal Wayland connection
- bind the registry and requested globals on that separate connection
- integrate `display.get_fd()` with `GLib.io_add_watch`
- dispatch pending events and flush outgoing requests without blocking GTK
- prove clean shutdown removes GLib sources and disconnects the display

Why not start by reusing GTK's connection:

- GTK3 does not expose `GdkWayland` through normal PyGObject introspection
- using `ctypes` to pull `wl_display` out of GDK is possible but fragile
- sharing GTK's internal display connection risks dispatch ordering surprises

Exit criteria for the spike:

- event callbacks arrive on the GTK main loop
- Docking exits without leaked GLib sources or file descriptors
- failed Wayland connections fall back to reduced services

#### App ID Matching Corpus

Wayland taskbar correctness depends on matching compositor `app_id` values to
Docking desktop IDs. This is inherently messy.

Known failure modes:

- app ID matches desktop file basename exactly (`firefox`)
- app ID is reverse-DNS (`org.gnome.Nautilus`)
- app ID is a server/helper process (`gnome-terminal-server`)
- Flatpak/Snap IDs may not match visible desktop files
- Electron and Chromium PWAs can create long app IDs that do not match the
  exported desktop file
- Wine/Steam games may use `.exe`, `steam_app_*`, or launcher-specific IDs
- app ID can be missing or `null` on some compositors/apps

Recommended next step:

- add an app ID corpus test file with real samples and expected desktop IDs
- add a user override map similar to Waybar's `app_ids-mapping`
- log unmatched app IDs at debug level so users can report mapping fixes

Matching order should be:

1. user override map
2. visible dock item desktop IDs and aliases
3. exact `<app_id>.desktop`
4. `StartupWMClass`/runtime alias lookup
5. reverse-DNS basename fallback
6. optional title-based fallback only for known difficult cases

#### Capability UX

Unsupported backend actions should not silently do nothing.

UI rules:

- disable controls when the feature exists but is unavailable in the current
  backend or current window state
- hide controls only when they are irrelevant for the current context
- prefer stable labels; do not rename disabled actions into "Cannot ..."
- add tooltips or adjacent explanations when the reason is not obvious
- let `WindowSnapshot.can_activate`, `can_minimize`, `can_close`,
  `can_preview`, and `PlatformCapabilities` drive menu/action sensitivity

Recommended next step:

- make dock window menu rows capability-aware before adding more backends
- add tests that unsupported actions are insensitive, not no-op clickable

#### Manual Compositor Test Matrix

Compositor backends need manual smoke profiles because CI will not run Sway,
Hyprland, Niri, Wayfire, COSMIC, Plasma, and GNOME Shell reliably.

Each compositor profile should include:

- session/compositor version and installed optional packages
- exact backend expected to activate
- startup logs to verify selected backend and capability flags
- open/focus/close two or three common apps
- verify running dots, active indicator, instance counts, and window menu rows
- test activate, close, minimize, maximize, and workspace actions only when
  supported
- test fallback when protocol/socket/dependency is missing
- test shutdown/restart for background event reader cleanup
- re-run the X11 smoke pass after backend-specific changes

Recommended next step:

- add a `docs/wayland-smoke-matrix.md` or a section in this file with exact
  manual commands and expected results per compositor

#### Packaging Strategy

Wayland support has two different dependency classes.

System/GIR dependencies:

- `gtk-layer-shell` is not a pip package; it is a native library plus GIR
  typelib
- Debian/Ubuntu package: `gir1.2-gtklayershell-0.1`
- Fedora/Arch package: usually `gtk-layer-shell`
- Flatpak/Snap/Nix need explicit runtime inclusion and GI typelib paths

Python/runtime dependencies:

- `pywayland` is a Python package with CFFI/libwayland runtime expectations
- vendored protocol XML/generated bindings do not remove the need for
  `pywayland`
- compositor-specific IPC wrappers such as `pywayfire` should remain optional

Dependency model:

- vendored XML/generated bindings define protocol messages, requests, events,
  and enums
- `pywayland` provides the live runtime connection: registry binding, event
  dispatch, request sending, and file-descriptor integration
- higher-level libraries such as `gtk-layer-shell` replace direct `pywayland`
  usage only for the protocol they wrap
- socket/IPC backends such as Niri, Hyprland, and Wayfire do not need
  `pywayland` unless they also bind Wayland protocols directly

`pyproject.toml` should eventually expose `pywayland` as an optional extra, not
as a default X11 dependency:

```toml
[project.optional-dependencies]
wayland = [
    "pywayland>=0.4.18",
]
```

Then developers or users testing native Wayland protocol backends can install:

```bash
pip install -e ".[wayland]"
```

Packaging policy:

- keep X11 packages working without Wayland extras
- mark native Wayland dependencies as recommended/optional until parity is real
- for Flatpak/Snap, decide whether native Wayland support is included in the
  package or documented as unavailable in that format
- include vendored XML in package data so source provenance is shipped

#### Preview and Capture Feasibility

Native Wayland previews should be treated as a separate future backend, not a
side effect of foreign-toplevel tracking.

Potential capture paths:

- `ext-image-capture-source-v1` plus `ext-image-copy-capture-v1`
- `ext_foreign_toplevel_image_capture_source_manager_v1` for toplevel capture
- COSMIC image-capture-source extensions for workspaces or other sources
- Plasma/KWin task-manager style PipeWire/KPipeWire window thumbnail requests
- XDG desktop portals for user-mediated screenshot/screencast flows

Constraints:

- portals are user-mediated and not suitable for silent hover thumbnails
- old `wlr-screencopy` is output/region-oriented and deprecated for new work
- toplevel image capture support is still uneven across compositors
- PipeWire paths may need compositor-specific permission/stream handling

Recommended next step:

- do a dedicated preview PR that first proves one compositor can capture a
  toplevel into a `PreviewImage` without blocking or prompting on every hover

#### Workspace Model Mismatch

"Current workspace" is not portable as one simple integer.

Examples:

- `ext_workspace_v1` exposes workspace groups assigned to sets of outputs
- Hyprland workspaces are explicit, monitor-associated, and include special
  workspaces
- Niri has dynamic per-monitor workspaces that can disappear, reorder, and be
  addressed by monitor-local index or stable names
- KWin has virtual desktops and activities
- COSMIC exposes workspace protocols and may also use `ext_workspace_v1`

Policy:

- backend should define what `workspace_id` means
- UI should not assume workspace numbers are stable across all compositors
- `current_workspace_only` should be disabled unless the backend can map both
  windows and active workspace reliably
- workspace applet should display backend-provided names/ordering, not infer a
  universal grid

Recommended next step:

- add workspace model notes/tests before implementing `ext_workspace_v1`

#### GNOME Shell Extension Bridge Spike

GNOME Shell bridge support is feasible and should be explored separately from
normal Wayland backends.

Feasible bridge shape:

- GJS extension exports a narrow D-Bus interface with
  `Gio.DBusExportedObject.wrapJSObject`
- first version is read-only: list windows, focused window, workspace list, and
  active workspace
- Docking consumes that D-Bus interface through a GNOME-specific backend

Rules:

- do not use `org.gnome.Shell.Eval`
- do not enable unsafe mode
- do not expose arbitrary JavaScript execution
- expose specific data/actions only
- clean up every signal, actor, timer, and D-Bus export in `disable()`

Recommended next step:

- prototype a read-only bridge and validate focused-window/app-list state before
  attempting a full GNOME Shell frontend

#### Security and Trust Model

Compositor IPC and shell extensions are authority surfaces.

Trust assumptions:

- unsandboxed same-user host processes are usually trusted by compositors
- sandboxed applications should not automatically receive compositor IPC,
  shell-extension D-Bus, or unfiltered Wayland sockets
- D-Bus bridges are callable by same-user host processes unless explicitly
  filtered
- compositor IPC sockets can perform powerful actions such as focusing,
  closing, moving, or spawning clients

Rules for Docking:

- expose narrow, named methods only
- never expose arbitrary code execution
- never keep dangerous synchronous command sockets open
- document who can call each bridge and what authority it grants
- fail closed when permissions/dependencies are missing
- avoid leaking backend-private handles into UI or public APIs

Recommended next step:

- add a security note for each compositor-specific backend before shipping it

### Cairo-Dock Parity Checklist

The detailed staged refactor plan earlier in this document remains the
implementation order. To specifically reach Cairo-Dock's Wayland class of
support, the backend work eventually needs the following native pieces.

Dock surface support:

- keep the existing GTK shelf UI where possible, but give the main dock window a
  native layer-shell surface role instead of a normal `xdg_toplevel` role
- `gtk-layer-shell` / `wlr-layer-shell` initialization before the main dock
  window is first mapped
- edge anchors for all four dock positions
- layer-shell exclusive zones as the native replacement for X11 struts
- monitor targeting through layer-shell monitor assignment
- layer switching for keep-above / keep-below where the compositor supports it
- clear fallback when layer-shell is missing, especially on GNOME/Mutter

Taskbar/current-open-app context:

- generic wlroots backend using `zwlr_foreign_toplevel_manager_v1`
- running dots and active indicators come from foreign-toplevel state, not from
  layer-shell
- active app state is driven by the toplevel `activated` state and then
  aggregated through Docking's desktop ID/app matching
- multiple windows for one app are multiple toplevel handles that should map to
  one dock item and window menu group
- optional `ext_foreign_toplevel_list_v1` tracking for compositors that expose
  stable mapped-toplevel handles but not actions
- KWin backend using `org_kde_plasma_window_management` only if the private
  protocol / one-client binding tradeoff is accepted
- COSMIC backend using `ext_foreign_toplevel_list_v1`,
  `zcosmic_toplevel_info_v1`, and `zcosmic_toplevel_manager_v1`
- compositor-specific IPC for Hyprland, Niri, and Wayfire when their richer
  state/action APIs are explicitly selected
- one Wayland-aware app matcher that resolves compositor `app_id` values to
  Docking desktop IDs without disturbing the existing WM_CLASS matcher
- backend-neutral window IDs so UI code no longer requires XIDs

Window actions:

- activate through compositor protocol plus current `wl_seat`
- close where the protocol exposes close
- minimize only where the compositor supports it
- maximize/fullscreen only where the UI exposes those actions and the backend
  advertises support
- clear capability flags so the UI can hide or disable unsupported actions

Geometry, workspace, and dodge behavior:

- KWin geometry/workspace/stacking-order support only through the Plasma
  private protocol or a KWin scripting bridge
- COSMIC overlap notification support for native overlap-based hiding
- Hyprland/Niri/Wayfire IPC where those compositors expose geometry, workspace,
  and focus/action state
- optional Wayfire IPC for overlap, scale/expo, sticky/above, and other extras
- reduced behavior on generic foreign-toplevel compositors where geometry and
  workspaces are unavailable

Preview and menu behavior:

- X11 preview capture remains the X11 preview backend
- native Wayland should return preview images only when a compositor provides a
  real capture path; otherwise UI renders the app icon/title fallback
- XDG desktop portals are acceptable for user-mediated screenshots and color
  picking, but not for silent arbitrary-window previews
- `ext-image-copy-capture-v1` is the future protocol-shaped preview candidate
  where toplevel capture sources are available
- menus list `WindowSnapshot` objects rather than Wnck windows
- activate/close menu actions use backend-neutral window IDs

Applet behavior:

- Desktop, Workspaces, Window Killer, Color Picker, and Desk Presence need
  backend or portal services before they can claim native Wayland support
- X11 applet behavior must remain unchanged
- unsupported native Wayland applet actions should be disabled or visibly marked
  unavailable, not allowed to fail silently

Packaging and startup:

- native Wayland dependencies remain optional for the X11 package
- optional imports live inside backend factories
- unsupported native Wayland sessions select a no-op or reduced backend and log
  the limitation instead of crashing
- X11 and `GDK_BACKEND=x11` XWayland sessions continue selecting the current
  Wnck/X11 path

## References

These references were used to shape the current understanding.

### Upstream session direction

- Ubuntu 25.10 drops GNOME on Xorg:
  https://discourse.ubuntu.com/t/ubuntu-25-10-drops-support-for-gnome-on-xorg/62538
- GNOME 49 X11 session removal:
  https://blogs.gnome.org/alatiera/2025/06/08/the-x11-session-removal/
- GNOME X11 session removal FAQ:
  https://blogs.gnome.org/alatiera/2025/06/23/x11-session-removal-faq/

### GTK / GNOME behavior under Wayland

- GTK4 / Wayland: no app-controlled global toplevel positioning:
  https://discourse.gnome.org/t/gtk4-position-the-window/25095
- GNOME discussion: workspaces are compositor functionality, not client API:
  https://discourse.gnome.org/t/configure-gnome-text-editor-to-open-in-the-same-workspace/19978
- GNOME discussion: X11 tools only see XWayland windows, not native Wayland
  windows:
  https://discourse.gnome.org/t/gnome-terminal-window-id-cannot-be-found-by-xdotool-nor-wmctrl/14835

### Wayland protocol references

- GtkLayerShell docs:
  https://lazka.github.io/pgi-docs/GtkLayerShell-0.1/index.html
- `wlr-layer-shell` protocol:
  https://wayland.app/protocols/wlr-layer-shell-unstable-v1
- `wlr-foreign-toplevel-management` protocol:
  https://wayland.app/protocols/wlr-foreign-toplevel-management-unstable-v1
- `ext-foreign-toplevel-list-v1` protocol:
  https://wayland.app/protocols/ext-foreign-toplevel-list-v1
- `ext-workspace-v1` protocol:
  https://wayland.app/protocols/ext-workspace-v1
- `ext-image-capture-source-v1` protocol:
  https://wayland.app/protocols/ext-image-capture-source-v1
- `ext-image-copy-capture-v1` protocol:
  https://wayland.app/protocols/ext-image-copy-capture-v1
- `ext-idle-notify-v1` protocol:
  https://wayland.app/protocols/ext-idle-notify-v1
- XDG desktop portal Screenshot / PickColor:
  https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Screenshot.html
- KWin / Plasma window-management protocol:
  https://wayland.app/protocols/kde-plasma-window-management
- XDG activation protocol:
  https://wayland.app/protocols/xdg-activation-v1

### Compositor-specific integration references

- GNOME Shell extension architecture:
  https://gjs.guide/extensions/overview/architecture.html
- GNOME Shell extension anatomy / packaging:
  https://gjs.guide/extensions/overview/anatomy.html
- GNOME Shell extension version targeting:
  https://gjs.guide/extensions/development/targeting-older-gnome.html
- Mutter `Meta.Window` API:
  https://mutter.gnome.org/meta/class.Window.html
- Mutter `Meta.Workspace` API:
  https://gnome.pages.gitlab.gnome.org/mutter/meta/class.Workspace.html
- `ws-dbus` GNOME Shell D-Bus bridge example:
  https://github.com/kemallette/ws-dbus
- `Window Calls` GNOME Shell D-Bus bridge example:
  https://extensions.gnome.org/extension/4724/window-calls/
- `Focused Window D-Bus` GNOME Shell D-Bus bridge example:
  https://extensions.gnome.org/extension/5592/focused-window-d-bus/
- Hyprland IPC:
  https://wiki.hypr.land/IPC/
- Hyprland dispatchers:
  https://wiki.hypr.land/Configuring/Dispatchers/
- Niri IPC:
  https://niri-wm.github.io/niri/IPC.html
- Niri IPC request/reference docs:
  https://niri-wm.github.io/niri/niri_ipc/enum.Request.html
- Wayfire IPC / `pywayfire`:
  https://github.com/WayfireWM/pywayfire
- Wayfire IPC developer notes:
  https://github.com/WayfireWM/wayfire/wiki/IPC-for-developers
- COSMIC protocol bindings:
  https://pop-os.github.io/libcosmic/cosmic/cctk/cosmic_protocols/index.html
- COSMIC toplevel management protocol:
  https://wayland.app/protocols/cosmic-toplevel-management-unstable-v1
- COSMIC overlap notify protocol:
  https://wayland.app/protocols/cosmic-overlap-notify-unstable-v1
- COSMIC image capture source protocol:
  https://wayland.app/protocols/cosmic-image-capture-source-unstable-v1
- KWin scripting tutorial:
  https://develop.kde.org/docs/plasma/kwin/
- PyWayland scanner:
  https://pywayland.readthedocs.io/en/latest/scanner.html
- PyWayland client module:
  https://pywayland.readthedocs.io/en/latest/module/client.html
- Waybar `wlr/taskbar` app ID mapping reference:
  https://man.archlinux.org/man/waybar-wlr-taskbar.5.en

### Related downstream project discussion

- Plank Reloaded issue #105: XWayland/X11 workaround under GNOME 49
  https://github.com/zquestz/plank-reloaded/issues/105

### Existing Wayland dock/taskbar examples

- Dash to Dock extension:
  https://extensions.gnome.org/extension/307/dash-to-dock/
- Dash to Panel extension:
  https://extensions.gnome.org/extension/1160/dashtopanel/
- Cairo-Dock Wayland notes:
  https://raw.githubusercontent.com/Cairo-Dock/cairo-dock-core/master/README_Wayland.md
- nwg-dock:
  https://github.com/nwg-piotr/nwg-dock
- nwg-dock-hyprland:
  https://github.com/nwg-piotr/nwg-dock-hyprland
- nwg-shell dock page:
  https://nwg-piotr.github.io/nwg-shell/nwg-dock.html
- Waybar `wlr/taskbar` man page:
  https://www.mankier.com/5/waybar-wlr-taskbar
- sfwbar man page:
  https://www.mankier.com/1/sfwbar
