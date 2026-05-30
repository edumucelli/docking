# Docking on Wayland

This document captures the current understanding of what Wayland means for
`docking`, why the existing codebase is fundamentally X11-centric, and what
future work would be required to make the project available on Wayland in a
serious way.

It is intended as a baseline engineering document, not as a promise of support.

## Scope

This document focuses on:

- the practical impact of GNOME and Ubuntu moving away from Xorg sessions
- what still works for GTK applications on Wayland
- which Docking features are blocked by X11-only assumptions today
- the difference between:
  - features that work with ordinary GTK on Wayland
  - features that require compositor protocols
  - features that likely require GNOME Shell integration
- plausible migration strategies for future work

This document does not define implementation details for a specific port yet.

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

There are three very different targets people may mean when they say "support
Wayland":

1. Run the current X11 dock under `XWayland`
2. Build a native Wayland dock on compositors that expose dock/taskbar
   protocols
3. Build a full GNOME dock on GNOME Wayland

These are not equivalent.

Short version:

- Running under `XWayland` may let Docking appear on screen, but it is not a
  real Wayland port and does not solve the core desktop-integration problem
- A native Wayland dock is plausible on compositor families that expose
  layer-shell and foreign-toplevel protocols
- A full dock on GNOME Wayland is a separate, harder problem because Mutter
  intentionally does not expose the common protocols that third-party docks use

## How To Test Today

The most practical test Docking can support today is not "native Wayland".
It is:

- a real Wayland desktop session
- with `XWayland` available
- forcing Docking to run as an X11 client via `GDK_BACKEND=x11`

That is the correct compatibility test for the current codebase because Docking
still depends heavily on X11-only integration such as:

- `libwnck` window tracking
- X11 window IDs and foreign-window capture
- `_NET_WM_STRUT_PARTIAL` struts
- X11 pointer barriers
- X11 property hints such as `_DOCKING_BACKGROUND_BLUR_REGION`

### Recommended Test Environment

For this project, the easiest meaningful setup is:

- install a GNOME session on the current Linux system
- log into a GNOME Wayland session
- run Docking with `GDK_BACKEND=x11`

On Debian-based systems, that usually means:

```bash
sudo apt-get install gnome-session
```

Then:

1. Log out of the current desktop session.
2. At GDM, choose `GNOME` rather than an Xorg session.
3. Log in and verify the session type.

Verification commands:

```bash
echo "$XDG_SESSION_TYPE"
echo "$WAYLAND_DISPLAY"
echo "$DISPLAY"
```

Expected result for a Wayland session with XWayland:

- `XDG_SESSION_TYPE=wayland`
- `WAYLAND_DISPLAY` is set
- `DISPLAY` is also set

That means the desktop session is Wayland, while X11 applications can still run
through XWayland.

### Launch Command

Run Docking like this:

```bash
GDK_BACKEND=x11 .venv/bin/docking
```

This is the mode that should be tested and documented today.

### What To Verify

Smoke-test these behaviors first:

- Docking launches and stays visible
- hover, click, drag-and-drop, and menus work
- pinned launchers and running-window matching still behave correctly
- applets that do not rely on X11-specific tools continue to work

Pay special attention to likely X11-sensitive features:

- window previews
- workspace switching
- show-desktop behavior
- window-killer targeting
- brightness control
- screen-edge reservation and placement
- pointer barriers and autohide edge behavior
- overlap-based hide modes

## Compatibility Test Log

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
- `ext-workspace-v1`
  - for workspace enumeration and switching
- screencopy/image-capture style protocols
  - for thumbnails/previews

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

`docking/platform/window_tracker.py` is built around `libwnck` and X11 window
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

`docking/platform/dodge.py` observes other windows via `Wnck` geometry/state in
order to decide whether the dock should hide.

That entire mechanism assumes client-visible global window geometry and global
window state.

### Screen Reservation and Blur Hints

`docking/platform/struts.py` uses X11 properties directly:

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

### X11/Wnck Applets

Several applets are directly tied to X11/Wnck concepts:

- `docking/applets/workspaces/applet.py`
- `docking/applets/desktop/applet.py`
- `docking/applets/windowkiller/applet.py`

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

## Feature Matrix

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

### What Is Coupled Today

The current runtime has some useful separation already, but several important
modules still assume X11 directly.

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
  - startup constructs `WindowTracker` directly as part of core runtime wiring
- `docking/ui/factory.py`
  - imports `WindowDodgeMonitor` directly from `docking.platform.dodge`
- `docking/ui/dock_window.py`
  - imports `GdkX11`, blur helpers, preview popup, and placement pieces that
    assume X11 semantics
- `docking/ui/placement.py`
  - directly manages X11 struts and pointer barriers
- `docking/ui/preview.py`
  - directly uses `GdkX11`, `Wnck`, and XID capture
- `docking/platform/window_tracker.py`
  - exposes X11-shaped behavior upward, including `Wnck.Window` and XID-driven
    operations
- `docking/platform/dodge.py`
  - uses Wnck geometry/state to decide overlap hiding
- X11/Wnck applets:
  - `docking/applets/workspaces/applet.py`
  - `docking/applets/desktop/applet.py`
  - `docking/applets/windowkiller/applet.py`

This is why the right initial milestone is not a `wayland backend`. It is a
cleanly isolated `x11 backend`.

### Proposed Backend Shape

The backend boundary should be capability-oriented, not one giant "platform"
class with dozens of unrelated methods.

Recommended composition:

- `SessionBackend`
  - top-level runtime object passed into startup/UI composition
- `WindowBackend`
  - running apps/windows, focus/minimize/close/cycle, active/urgent state
- `SurfaceBackend`
  - edge placement integration, reserved space, pointer barriers, input-region
    support specific to the platform
- `VisibilityBackend`
  - overlap/dodge monitoring and related hide/show signals
- `PreviewBackend`
  - preview capture, preview support flags, preview action routing
- `WorkspaceBackend`
  - workspace listing/switching where supported
- `DesktopActionsBackend`
  - shell-like actions such as show desktop or window-killer style actions

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

- `WindowHandle`
  - opaque backend-tagged identifier for a window
- `RunningWindow`
  - title, active, urgent, minimized, app identity, and handle
- `RunningAppState`
  - app-level aggregate currently consumed by the model
- `WorkspaceInfo`
  - backend-neutral workspace identity and label
- `PreviewImage` or preview result type
  - explicit success/fallback/unavailable states instead of raw X11 capture

This matters because the type boundary is usually where portability fails.
Once UI and model code expect XIDs or `Wnck.Window`, the backend abstraction is
already compromised.

### Proposed Package Layout

One practical direction would be:

- `docking/platform/backends/base.py`
  - backend protocols/interfaces and shared neutral dataclasses
- `docking/platform/backends/x11/__init__.py`
  - X11 backend composition root
- `docking/platform/backends/x11/windows.py`
  - current `WindowTracker` logic, reshaped behind `WindowBackend`
- `docking/platform/backends/x11/surface.py`
  - struts, barriers, blur-region support, X11-specific surface behavior
- `docking/platform/backends/x11/visibility.py`
  - current dodge/overlap monitor
- `docking/platform/backends/x11/previews.py`
  - XID-based preview capture
- `docking/platform/backends/x11/workspaces.py`
  - Wnck workspace support
- `docking/platform/backends/x11/actions.py`
  - show desktop, window-killer, or related shell-style actions

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

### Recommended Tree Organization

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
- `docking/platform/backends/x11/windows.py`
  - Wnck window tracking and window actions
- `docking/platform/backends/x11/surface.py`
  - struts, barriers, blur-region behavior, X11 surface helpers
- `docking/platform/backends/x11/visibility.py`
  - dodge/autohide overlap integration
- `docking/platform/backends/x11/previews.py`
  - XID-based preview capture and preview action support
- `docking/platform/backends/x11/workspaces.py`
  - Wnck workspace support
- `docking/platform/backends/x11/actions.py`
  - show desktop, window-killer, or similar shell-style actions
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

### Import Direction Rules

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

### Composition Root Guidance

The composition root should be as small as possible.

Today the runtime is effectively composed in:

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

### Suggested Applet-Facing Service Layer

Applets are a special case because some are portable and some are deeply tied
to window-manager behavior.

One useful refinement would be to avoid making applets import the full backend
object directly. Instead, expose small applet-facing services such as:

- `WorkspaceService`
- `DesktopActionService`
- `TaskService` if app/task state ever becomes applet-relevant

These services can be:

- backed by X11 today
- absent or capability-gated on reduced/Wayland backends later

That keeps applets from becoming coupled to the entire platform surface.

### How the First Moves Should Map to Files

A practical first sequence for code organization would be:

1. Add `docking/platform/backends/base.py`
   - define backend protocols and neutral dataclasses
2. Add `docking/platform/backends/x11/__init__.py`
   - define `X11SessionBackend`
3. Move `WindowTracker` logic into `backends/x11/windows.py`
   - leave a compatibility shim if needed temporarily
4. Move `docking/platform/dodge.py` logic into `backends/x11/visibility.py`
5. Move `docking/platform/struts.py` and `docking/platform/barriers.py`
   responsibilities behind `backends/x11/surface.py`
6. Move X11 preview capture into `backends/x11/previews.py`
7. Move Wnck workspace/desktop action code into backend-facing service modules

During that migration, temporary forwarding modules are acceptable if they help
avoid giant changesets, for example:

- `docking/platform/window_tracker.py`
  - short-lived wrapper delegating to `backends/x11/windows.py`

But those wrappers should be treated as transitional, not as permanent second
homes for backend logic.

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

### Why This Should Be Incremental

The refactor should deliberately optimize for intermediate states that are
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

#### Phase 1: Backend Interfaces and `X11SessionBackend`

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

#### Phase 2: `WindowBackend`

Purpose:

- isolate the single most important X11 dependency first

Work:

- move `WindowTracker` logic into `backends/x11/windows.py`
- keep Wnck internals inside the X11 implementation, but stop exposing
  `Wnck.Window` and raw XID lists upward
- define the `WindowBackend` contract around:
  - running-app aggregates
  - window handles
  - activation/minimize/close/cycle operations
  - active/urgent state
- preserve `DockModel.update_running()` as the aggregate sink, since that is
  already a good backend-neutral seam
- convert existing callers to depend on `backend.windows`
- leave temporary compatibility shims only if they reduce risk and are clearly
  transitional

Likely touch points:

- new `docking/platform/backends/x11/windows.py`
- temporary shim `docking/platform/window_tracker.py`
- `docking/app.py`
- `docking/ui/dock_window.py`
- `docking/ui/preview.py`
- `docking/platform/model.py`
- any tests that currently assume XID lists as a public contract

Why this phase comes early:

- tasklist/running state is the central dependency that many other features use
- once this is backend-shaped, later work has a stable foundation

Exit criteria:

- callers talk to `WindowBackend`, not `WindowTracker`
- no non-backend module needs `Wnck.Window` in its public contract

#### Phase 3: `PreviewBackend`

Purpose:

- isolate one of the most X11-specific UI subsystems

Work:

- move X11 thumbnail capture logic into `backends/x11/previews.py`
- define `PreviewBackend` around:
  - "is preview supported?"
  - "list previewable windows for this app"
  - "capture preview image for this window handle"
  - preview-window activation/close actions
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
- preview actions operate through backend-neutral handles

#### Phase 4: `SurfaceBackend`

Purpose:

- separate "dock UI geometry" from "platform edge/surface behavior"

Work:

- move X11 struts, pointer barriers, blur-region helpers, and other X11
  surface-specific integration behind `backends/x11/surface.py`
- define `SurfaceBackend` around:
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
- `docking/platform/struts.py`
- `docking/platform/barriers.py`

Why this phase matters:

- it untangles one of the most important conceptual confusions in the current
  code: monitor/layout policy versus X11-specific edge integration

Exit criteria:

- placement code coordinates platform behavior through `SurfaceBackend`
- raw `GdkX11` type checks are confined to X11 backend code

#### Phase 5: `VisibilityBackend`

Purpose:

- make autohide overlap logic a backend capability instead of a universal dock
  assumption

Work:

- move current Wnck-based overlap monitor into `backends/x11/visibility.py`
- define `VisibilityBackend` around:
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
- `docking/platform/dodge.py`
- `docking/ui/autohide.py`

Why this phase is useful even on X11:

- it makes autohide policy clearer
- it removes one more place where UI code assumes the ability to inspect other
  windows globally

Exit criteria:

- UI composition no longer imports `WindowDodgeMonitor` directly
- overlap-driven hiding is explicitly capability-backed

#### Phase 6: Applet Capability Split

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

#### Phase 7: Reduced / Non-X11 Validation Backend

Purpose:

- prove that backend capability handling is real before native Wayland exists

Work:

- add a non-X11 backend used only for development/tests, or a reduced runtime
  mode that implements launcher-only behavior
- possible shapes:
  - `NullSessionBackend` for tests and contract validation
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

#### Phase 8: Actual Wayland Backend Work

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
2. `WindowBackend`
3. `PreviewBackend`
4. `SurfaceBackend`
5. `VisibilityBackend`
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

The detailed step-by-step roadmap is in the previous section. This section is
the shorter strategic reading of that roadmap.

### 1. Do the Backend Refactor First

The next serious engineering work should be the incremental backend refactor,
not a direct Wayland implementation attempt.

That means:

- make X11 explicit
- isolate X11-only contracts
- validate a reduced-capability runtime before chasing protocol support

Without that groundwork, later Wayland work will tend to become a big-bang
rewrite.

### 2. Treat Reduced Wayland as a Valid Intermediate Product

A first useful Wayland target does not need full dock parity.

The realistic reduced target remains:

- pinned launchers
- click to launch
- renderer/themes/zoom
- backend-neutral applets
- no tasklist
- no previews
- no workspace applet
- no X11 dodge/struts/barriers

That would already be a meaningful milestone because it proves the backend
architecture and creates a truthful "available on Wayland" story for a limited
mode.

### 3. Separate Non-GNOME Wayland From GNOME Wayland

After the refactor, the project should still treat these as different tracks:

- `native client on wlroots/KWin/Cosmic-like compositors`
- `GNOME Wayland integration on Ubuntu GNOME`

The first path is where public protocols are most likely to pay off.
The second path is where shell integration is most likely to dominate.

Trying to collapse both into one near-term milestone would likely slow the
project down.

### 4. Revisit GNOME Only After the Backend Boundaries Exist

For GNOME Wayland, the project will eventually need to choose deliberately
between:

- limited normal-app launcher shelf behavior
- deeper shell-integrated behavior

That decision should happen after the backend refactor makes capability gaps
explicit. Before that, the codebase is not yet in a shape where GNOME-specific
strategy decisions can be implemented cleanly.

## Code Areas Most Relevant to a Port

The following modules are the main porting hotspots.

### Core and UI pieces likely reusable

- `docking/core/config.py`
- `docking/core/theme.py`
- `docking/core/layout.py`
- `docking/core/items.py`
- `docking/ui/renderer.py`

These still need adaptation, but they are not fundamentally tied to X11.

### X11-bound platform pieces

- `docking/platform/window_tracker.py`
- `docking/platform/dodge.py`
- `docking/platform/struts.py`
- `docking/platform/barriers.py`

These should be assumed non-portable as currently designed.

### UI code with X11/global-coordinate assumptions

- `docking/ui/dock_window.py`
- `docking/ui/placement.py`
- `docking/ui/preview.py`
- `docking/ui/display.py`
- parts of `docking/ui/menu.py`
- parts of `docking/ui/tooltip.py`

These will need redesign even if the renderer survives intact.

### Applets with direct X11/Wnck coupling

- `docking/applets/workspaces/applet.py`
- `docking/applets/desktop/applet.py`
- `docking/applets/windowkiller/applet.py`

These should be treated as feature-specific Wayland projects, not as incidental
fixes.

## Open Questions

These questions should be answered before committing to a large porting effort.

### Product Questions

- Is the first goal "launcher shelf on Wayland" or "full dock on Wayland"?
- Is GNOME a mandatory first-class target, or is "Wayland support on some
  compositors first" acceptable?
- Is a GNOME Shell extension acceptable as part of the project architecture?
- Is an `XWayland` fallback/workaround worth documenting for users during the
  transition period, even if it is not treated as real support?

### Technical Questions

- What should the backend interface look like for:
  - window/task discovery
  - activation/minimize/close actions
  - workspace state
  - edge placement / exclusive zone
  - previews
- Which applets must work in the first Wayland-capable release?
- Which X11-only features are acceptable to drop or defer?
- Should previews be treated as an optional capability instead of a baseline
  dock feature on Wayland?
- Should the first Wayland-capable release explicitly disable Wnck-dependent
  applets on unsupported sessions?

## Suggested Support Language for the Future

When the project eventually documents Wayland support publicly, it will help to
avoid ambiguous statements like "Wayland supported".

More precise language would look like:

- `X11`: full support
- `Wayland via XWayland workaround`: Docking may launch as an X11 client inside
  a Wayland session, but task/window integration is incomplete and unsupported
- `Wayland (experimental launcher mode)`: pinned launchers and compatible
  applets work; tasklist/workspace/preview features are limited or unavailable
- `Wayland on wlroots/KWin (experimental native backend)`: partial dock
  integration depending on compositor protocol support
- `GNOME Wayland`: reduced support as normal app, or extension-backed support if
  such an integration exists

This is worth deciding early because it prevents the project from inheriting
the confusion currently seen around "it launches under Wayland" versus "it
works as a real dock under Wayland".

## Current Recommendation

The most realistic interpretation of "make Docking available on Wayland" is:

1. split backend-sensitive code from reusable UI/core logic
2. implement a reduced native Wayland launcher shelf
3. target compositor families with public dock/taskbar protocols first
4. treat GNOME Wayland parity as a separate, explicitly shell-integration-heavy
   effort

If the project instead targets GNOME Wayland parity first, it should be planned
as a shell integration project from day one.

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

`docking.ui.preview` is X11-specific:

- imports `GdkX11` and `Wnck`
- captures thumbnails by creating `GdkX11.X11Window.foreign_new_for_display`
  from an XID
- activates thumbnails by calling `WindowTracker.activate_xid`

`docking.ui.menu` is X11-specific for open-window rows:

- calls `get_windows_for()`
- reads `window.get_xid()`
- captures Wnck windows with `capture_window()`
- activates and closes by XID

`docking.platform.dodge.WindowDodgeMonitor` is Wnck-specific:

- listens to Wnck screen/window signals
- reads active workspace, active window, geometry, maximized state, and window
  type
- implements all current overlap-based hide modes from those X11 concepts

`docking.ui.placement` and `docking.platform.struts` are X11-specific at the
edge-integration layer:

- struts use `_NET_WM_STRUT_PARTIAL` through Xlib
- blur hints use `_DOCKING_BACKGROUND_BLUR_REGION` through Xlib
- pointer barriers use XFixes/XInput2 through `docking.platform.barriers`
- placement already guards struts/barriers with `GdkX11.X11Display` /
  `GdkX11.X11Window`, which is a useful pattern to preserve

Several applets are X11/Wnck-bound:

- Desktop applet toggles show-desktop through Wnck
- Workspaces applet is Wnck-based
- Window Killer selects a topmost Wnck window and kills its PID
- Desk Presence idle tracking uses X11 screensaver APIs
- Color Picker currently samples the X11 root window and is expected to fail
  for native Wayland contents

The current app bootstrap also hardwires `WindowTracker` in `docking.app` and
hardwires `WindowDodgeMonitor` in `docking.ui.factory`. A native Wayland port
must introduce factories/adapters there rather than replacing those classes in
place.

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

The earlier shorthand of "add a `WindowBackend`" is not sufficient by itself.
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
WindowService.start(model)
WindowService.stop()
WindowService.list_windows(desktop_id) -> tuple[WindowSnapshot, ...]
WindowService.activate(window_id) -> ActionResult
WindowService.activate_most_recent(desktop_id) -> ActionResult
WindowService.cycle(desktop_id) -> ActionResult
WindowService.minimize_all(desktop_id) -> ActionResult
WindowService.close(window_id) -> ActionResult
WindowService.close_all(desktop_id) -> ActionResult
WindowService.snapshot_running() -> dict[desktop_id, RunningAppInfo]
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
id: str | int
backend: "x11" | "wayland-wlr" | "wayland-plasma" | "wayland-cosmic"
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
workspace: backend-specific workspace id | None
can_activate: bool
can_minimize: bool
can_close: bool
can_preview: bool
native_handle: internal only
```

`native_handle` should not be exposed to normal UI code. The backend owns the
mapping from `WindowId` to a live `Wnck.Window`, Wayland protocol handle, or
compositor-specific object. On X11, `id` can initially remain the XID for
compatibility. On Wayland, `id` must be an internal stable handle for the
compositor toplevel object, not an XID.

`RunningAppInfo` should grow neutral IDs before any native Wayland backend is
enabled:

```text
RunningWindowInfo.window_id: WindowId
RunningWindowInfo.xid: int | None
RunningAppInfo.window_ids: tuple[WindowId, ...]
RunningAppInfo.xids: tuple[int, ...]  # X11 compatibility during migration
```

This lets preview/menu/action code migrate away from XIDs incrementally while
existing X11 tests and compatibility paths continue to work.

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

The service should answer whether a hide mode is supported:

```text
supports_hide_mode(mode) -> bool
create_monitor(get_dock_rect, on_change) -> VisibilityMonitor | None
```

If unsupported, the runtime should degrade to normal autohide behavior for that
session and log the capability gap without mutating the saved user config.

`PreviewService` owns preview image capture and fallback:

- X11: current XID / `GdkX11.X11Window.foreign_new_for_display` capture
- native Wayland: icon/title preview cards unless a compositor exposes a real
  capture path
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

Backend selection has to happen earlier than it does today.

Today `docking.app` imports and constructs `WindowTracker` directly, while
`docking.ui.factory` imports and starts `WindowDodgeMonitor`. Several UI and
applet modules also import `GdkX11` or `Wnck` at module import time. A native
Wayland no-op backend cannot protect the process if importing UI code has
already required X11-only libraries.

The startup shape should become:

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

Concrete import cleanup needed before native Wayland can degrade safely:

- `docking.ui.preview` must stop importing `GdkX11` and `Wnck` directly
- `docking.ui.menu` must stop consuming Wnck windows and XIDs directly
- `docking.ui.dock_window` and `docking.ui.placement` should stop owning raw
  `GdkX11` checks directly once `SurfaceService` exists
- Wnck applets must move Wnck calls behind service implementations or lazy
  imports
- X11-only modules can remain, but backend-neutral code must not import them

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
supports_screenshot
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
   behavior.
3. Add `window_ids` alongside existing XIDs in running-state dataclasses.
4. Convert `MenuHandler` from Wnck windows/XIDs to `WindowSnapshot`.
5. Convert `PreviewPopup` from XID lists to `WindowSnapshot` plus
   `PreviewService`.
6. Add `SessionBackend` and wire the current X11 services through it.
7. Move dodge creation behind `VisibilityService`.
8. Move struts/barriers/blur/input-region ownership behind `SurfaceService`.
9. Add a `NullSessionBackend` or reduced backend and verify Docking can run
   without Wnck task powers.
10. Only after that, start layer-shell and Wayland toplevel implementation.

The key test before real Wayland code is: Docking should be able to run with a
backend that intentionally lacks taskbar, preview, workspace, and overlap
powers. That flushes out hidden X11 assumptions before compositor protocols are
involved.

### Proposed PR Order

The PR sequence should keep every step reviewable and preserve the existing X11
runtime after each merge. The first Wayland-specific PR should not appear until
the app can run through explicit backend services on X11.

#### PR 1: Backend Contracts Only

Add backend-neutral types and protocols without changing runtime wiring.

Scope:

- add `docking/platform/backends/base.py`
- define `WindowId`, `WindowSnapshot`, `ActionResult`
- define service protocols:
  - `WindowService`
  - `SurfaceService`
  - `VisibilityService`
  - `PreviewService`
  - applet-facing service protocols
- define `PlatformCapabilities`

Do not:

- move `WindowTracker`
- change app startup
- add Wayland code
- change menu or preview behavior

Exit criteria:

- pure unit tests for dataclasses/capabilities pass
- no runtime behavior changes

#### PR 2: X11 Window Adapter Facade + Neutral Running IDs

Wrap the current `WindowTracker` behavior behind an X11 `WindowService` while
keeping compatibility methods alive. This PR also absorbs the former neutral
running-ID PR because it is still non-behavioral terrain prep: it only publishes
`WindowId` beside existing XIDs, without moving any UI caller yet.

Scope:

- add `docking/platform/backends/x11/windows.py`
- make it delegate to or contain the current Wnck logic
- keep `docking/platform/window_tracker.py` as a compatibility shim if needed
- add `list_windows(desktop_id) -> WindowSnapshot`
- add `WindowId` mapping from XID to live Wnck window internally
- preserve `get_xids_for()`, `get_windows_for()`, `activate_xid()`, and
  `close_xid()` temporarily
- add `window_id` to `RunningWindowInfo`
- add `window_ids` to `RunningAppInfo`
- keep `xid` and `xids` as the active compatibility path for current X11 UI
  code
- update the X11 scan path so every valid XID snapshot also carries
  `WindowId.x11(xid)`
- make `docking/platform/backends/base.py` avoid runtime imports from
  `docking.platform.running` so `running.py` can import `WindowId` without a
  circular import

Do not:

- convert preview/menu yet
- remove XID fields
- change `DockModel.update_running()` semantics
- change `docking.app` startup wiring
- add Wayland or reduced-backend selection

Exit criteria:

- current window-tracker tests pass
- new adapter tests prove snapshots preserve title, active, urgent, geometry,
  workspace, capabilities, and XID identity
- running-state tests prove `xids` and `window_ids` are populated in the same
  order
- import smoke tests prove `docking.platform.running` and
  `docking.platform.backends.base` do not create a circular import
- current X11 callers still use the old compatibility methods, so user-visible
  behavior is unchanged

Implementation notes:

- Prefer an additive adapter over a rewrite. `WindowTracker` remains the
  current runtime object until the session-backend PR.
- Keep live Wnck objects inside the X11 implementation. Only
  `WindowSnapshot`, `RunningAppInfo`, and `WindowId` should cross the new
  backend-facing API.
- When a Wnck property read races with a disappearing X11 window, preserve the
  existing failure policy: skip only the unstable read/window and keep the scan
  convergent.
- `WindowId.value` for X11 is the integer XID. Future Wayland backends must not
  expose protocol handles there; they should use backend-owned opaque IDs.

Validation commands:

- `.venv/bin/ruff check docking/platform/backends/base.py docking/platform/running.py docking/platform/window_tracker.py docking/platform/backends/x11 tests/platform/test_backend_contracts.py tests/platform/test_window_tracker_integration.py tests/platform/test_x11_window_service.py`
- `python3 -m compileall -q docking/platform/backends/base.py docking/platform/running.py docking/platform/window_tracker.py docking/platform/backends/x11 tests/platform/test_backend_contracts.py tests/platform/test_window_tracker_integration.py tests/platform/test_x11_window_service.py`
- `python3 -c "from docking.platform.running import RunningAppInfo, RunningWindowInfo; from docking.platform.backends.base import WindowId; print(WindowId.x11(1))"`
- When the local environment has pytest installed:
  `.venv/bin/pytest tests/platform/test_backend_contracts.py tests/platform/test_window_tracker.py tests/platform/test_window_tracker_integration.py tests/platform/test_x11_window_service.py`

#### PR 3: Menu Window Rows Use Snapshots

Remove Wnck/XID assumptions from open-window menu rows. This is the first
planned consumer migration and therefore the first PR after PR 2 that changes a
runtime call path, even though X11 behavior should remain visually identical.

Start here:

- inspect `docking/ui/menu.py`, especially open-window row creation, row
  activation, and close-button handlers
- inject or pass the window service that already has `list_windows()`,
  `activate(WindowId)`, and `close(WindowId)`
- keep the compatibility tracker available until all callers have moved; do not
  delete `get_windows_for()`, `activate_xid()`, or `close_xid()`
- use `WindowSnapshot.title` for labels and `WindowSnapshot.id` for actions
- preserve current sorting, empty-state behavior, close-button visibility, and
  menu teardown behavior
- update tests in `tests/ui/test_menu_integration.py` so they assert
  `WindowSnapshot` and `WindowId` usage instead of fake Wnck objects where
  possible
- add a focused regression test that an X11 snapshot with XID 7 still calls the
  X11 adapter close/activate path for `WindowId.x11(7)`

Do not:

- touch preview popup behavior
- remove XID fields from running state
- change app startup/backend selection
- add native Wayland-specific menu behavior

Exit criteria:

- menu tests no longer need live/fake Wnck windows for open-window rows
- close/activate menu behavior remains unchanged on X11
- unsupported or empty `list_windows()` produces the current no-window menu
  behavior rather than a crash

#### PR 4: Preview Popup Uses `PreviewService`

Remove direct `GdkX11`/`Wnck` usage from backend-neutral preview UI while
keeping the X11 capture path internally unchanged.

Start here:

- inspect `docking/ui/preview.py` and identify every XID, Wnck, and GdkX11
  boundary
- add `docking/platform/backends/x11/previews.py`
- move current X11 capture logic behind `PreviewService.capture(WindowId, ...)`
- keep any low-level X11 pixbuf/window lookup helpers private to the X11
  preview service
- make `PreviewPopup` consume `WindowSnapshot` or `WindowId` instead of raw XID
  lists
- preserve icon/title fallback behavior for windows where capture returns
  `None`
- keep X11 screenshot/capture error handling defensive; disappearing windows
  should drop a preview, not crash hover UI
- update `tests/ui/test_preview_popup_integration.py` and visual preview cases
  to use snapshots/service fakes

Do not:

- add native Wayland capture portals yet
- change thumbnail sizing or timing policy unless required by the service
  boundary
- delete `get_xids_for()` until no callers remain

Exit criteria:

- `docking/ui/preview.py` no longer imports `GdkX11` or `Wnck`
- preview behavior remains the same on X11
- tests cover stale/not-found `WindowId` handling

#### PR 5: Session Backend Selection With X11 Only

Introduce `SessionBackend` and a backend factory, but still select only the X11
backend in production. This PR changes startup wiring, so it should happen only
after menu and preview can consume services.

Start here:

- add `docking/platform/backends/x11/session.py`
- add `docking/platform/backends/selection.py`
- create `X11SessionBackend` with:
  - `name == "x11"`
  - `display_server == DisplayServer.X11`
  - X11 `WindowService`
  - X11 `PreviewService` once PR 4 exists
  - placeholder `None` or transitional services for surface/visibility until
    their PRs land
- make `docking.app` construct the session backend and pass services into
  `build_dock_window`
- keep imports lazy enough that native Wayland startup does not import X11-only
  modules before backend selection in later PRs
- update `tests/test_app.py`, `tests/ui/test_factory.py`, and smoke tests to
  build/fake a session backend

Do not:

- add native Wayland backend
- change applets yet
- make `GDK_BACKEND=wayland` claim support

Exit criteria:

- startup behavior is unchanged on X11
- tests can construct a fake/null session backend for UI wiring
- `docking.app` no longer directly decides individual X11 services

#### PR 6: Visibility Service

Move dodge monitor creation behind the session backend.

Start here:

- inspect `docking/platform/dodge.py`, `docking/platform/dodge_monitor.py`, and
  `docking/ui/factory.py`
- add `docking/platform/backends/x11/visibility.py`
- wrap current `WindowDodgeMonitor` construction behind `VisibilityService`
- teach the factory to request a visibility monitor from
  `backend.visibility.create_monitor(...)`
- support `None` cleanly for unsupported backends or unsupported hide modes
- map hide-mode support to `PlatformCapabilities` rather than probing X11 in UI
- preserve all current X11 hide-mode semantics and signal timing

Do not:

- change dodge math
- add Wayland overlap protocols
- change surface placement or struts

Exit criteria:

- `docking.ui.factory` no longer imports `WindowDodgeMonitor`
- X11 dodge tests still pass
- unsupported visibility service can run without crashing

#### PR 7: Surface Service

Move struts, barriers, blur hints, and platform surface hooks behind
`SurfaceService`.

Start here:

- inspect `docking/platform/struts.py`, pointer barriers, blur helpers, and
  `DockPlacementController`
- add `docking/platform/backends/x11/surface.py`
- move X11 strut/barrier/blur calls behind service methods while keeping
  placement math in the existing placement controller
- make input-region support capability-driven
- keep X11-specific imports under the X11 backend or explicit transitional
  shims
- add tests that unsupported reservation/input-region operations are no-ops,
  not crashes

Do not:

- add layer-shell yet
- rewrite placement math unless required to preserve behavior
- change always-visible/autohide semantics

Exit criteria:

- raw `GdkX11` checks are confined to X11 backend code or transitional shims
- X11 placement, strut, barrier, and blur tests still pass

#### PR 8: Applet Service Extraction

Move Wnck/X11 applet dependencies behind applet-facing services.

Start here:

- inventory X11/Wnck usage in Desktop, Workspaces, Window Killer,
  Desk Presence, Color Picker, Screenshot, Caffeine, and any applet that shells
  out to X11 tools
- add X11 implementations for `WorkspaceService`, `DesktopActionService`,
  `WindowPickService`, `IdleService`, and `ScreenCaptureService` only where the
  current applet really needs them
- migrate one applet family at a time, starting with the smallest one
- add explicit unsupported states so applets can hide actions, disable buttons,
  or show a concise unavailable state instead of crashing
- keep service APIs narrow; do not expose raw Wnck windows to applets

Do not:

- attempt full native Wayland applet parity
- make applet UI depend on compositor names directly
- remove X11 helper paths until each applet has tests

Exit criteria:

- applets keep current X11 behavior
- backend-neutral applet loading can skip or disable unsupported actions
- tests cover at least one unsupported-service path

#### PR 9: Null / Reduced Backend

Add a backend with no taskbar powers to validate that X11 assumptions are no
longer leaking through normal UI.

Start here:

- add `docking/platform/backends/null/session.py` or
  `docking/platform/backends/reduced/session.py`
- provide no-op services for windows, previews, visibility, workspaces, and
  applet-specific platform actions
- add a test/dev selection path, preferably an environment variable such as
  `DOCKING_BACKEND=null`, but keep production auto-detection unchanged
- verify pinned launchers, rendering, menus without window rows, no-preview
  hover behavior, and backend-neutral applets
- assert `PlatformCapabilities` is honest: false for taskbar powers, true only
  for what the reduced backend actually supports

Do not:

- ship it as user-facing native Wayland support yet unless explicitly labeled
  reduced/experimental
- import X11 modules from the null backend
- silently pretend taskbar/window actions succeeded

Exit criteria:

- Docking can start without Wnck task powers
- unsupported features degrade intentionally
- reduced-backend tests prove no accidental X11 imports

#### PR 10: Native Wayland Detection Stub

Only after the reduced backend works, add native Wayland detection that selects
a reduced/no-op backend when unsupported.

Scope:

- detect GTK Wayland display
- avoid importing X11-only backend modules for native Wayland
- log protocol/dependency limitations clearly
- select reduced backend on GNOME/Mutter native Wayland
- select X11 backend for X11 and XWayland sessions exactly as today

Do not:

- add layer-shell or foreign-toplevel yet
- run X11 fallback code in native Wayland unless explicitly under XWayland
- claim current-open-app support on compositors without a toplevel protocol

Exit criteria:

- Docking does not crash on native Wayland startup
- X11 remains unchanged
- logs and capability flags explain reduced mode

#### PR 11: Layer-Shell Surface Backend

Add native Wayland dock-surface placement for compositors that support
layer-shell.

Start here:

- optional `gtk-layer-shell` import inside Wayland surface backend
- configure layer-shell before first map
- implement anchors, monitor targeting, layer choice, and exclusive zones
- keep fallback when layer-shell is unavailable
- map the existing Docking position/config concepts onto layer-shell anchors
  without changing X11 placement code
- keep GNOME/Mutter native Wayland in reduced mode because it does not expose
  the required third-party layer-shell path
- add capability flags for layer-shell and exclusive-zone reservation

Do not:

- add taskbar/window tracking yet
- make layer-shell a hard dependency
- import layer-shell modules in X11 sessions

Exit criteria:

- native Wayland dock can reserve edge space on a supported layer-shell
  compositor
- GNOME native Wayland remains reduced/unsupported with a clear log
- X11 placement tests remain unchanged

#### PR 12: Generic Foreign-Toplevel Window Service

Add wlroots-style opened-app context.

Start here:

- bind `zwlr_foreign_toplevel_manager_v1`
- track app ID, title, active/minimized/maximized/fullscreen, parent, and close
  events
- implement activate/close/minimize where supported
- publish `RunningAppInfo` through neutral window IDs
- use Wayland-aware app ID matching
- store protocol handles only inside the Wayland backend; expose only
  `WindowId`/`WindowSnapshot` to UI
- handle protocol absence by keeping the reduced backend active
- keep geometry, stacking, and workspace capability flags false unless the
  compositor protocol actually provides them

Do not:

- claim geometry/dodge/workspace parity
- add compositor-specific Plasma or COSMIC code here
- change X11 matching behavior

Exit criteria:

- running indicators and basic window actions work on at least one supported
  wlroots compositor
- unsupported compositors continue reduced mode with clear logs

#### PR 13: KWin / Plasma Backend

Add the richest parity backend.

Start here:

- bind Plasma window-management and virtual desktop protocols
- track UUIDs, app IDs, titles, state, attention, skip-taskbar, geometry, PID,
  virtual desktops, and stacking order where available
- implement richer actions and show-desktop/workspace services
- enable more hide modes when geometry/workspace capabilities are present
- add Plasma-specific `WindowService`, `WorkspaceService`, and
  `DesktopActionService` pieces behind capability flags
- prefer Plasma protocol data over generic foreign-toplevel data when both are
  present
- keep protocol selection deterministic and logged

Exit criteria:

- KWin Wayland reaches closest behavior to current X11 for taskbar, actions,
  workspace, and dodge features
- non-Plasma Wayland backends are unaffected

#### PR 14: COSMIC / Optional Compositor Extras

Add compositor-specific backends after the generic and KWin paths are stable.

Start here:

- COSMIC toplevel/workspace/overlap protocols
- optional Wayfire IPC extensions
- optional Niri overview integration
- add each compositor integration behind explicit detection and capability
  flags
- keep each optional backend isolated so a missing dependency or unsupported
  compositor cannot affect X11, generic wlroots, or Plasma

Exit criteria:

- each compositor extension is capability-gated and does not affect X11 or
  other Wayland backends

#### PR 15: Cleanup Transitional X11 APIs

Remove compatibility methods only after all UI callers and tests use neutral
backend APIs.

Start here:

- remove or deprecate `get_xids_for`, `activate_xid`, `close_xid` from
  backend-neutral call paths
- keep XID internals inside X11 backend where still useful
- update documentation and support language
- search the repo for `get_xids_for`, `get_windows_for`, `activate_xid`,
  `close_xid`, raw `Wnck.Window`, and raw XID usage before deleting anything
- preserve any X11-only internals that the X11 backend still needs for previews
  or diagnostics

Exit criteria:

- no backend-neutral UI module depends on Wnck windows or XIDs
- X11 backend remains fully supported

### Cairo-Dock Parity Checklist

The detailed staged refactor plan earlier in this document remains the
implementation order. To specifically reach Cairo-Dock's Wayland class of
support, the backend work eventually needs the following native pieces.

Dock surface support:

- `gtk-layer-shell` / `wlr-layer-shell` initialization before the main dock
  window is first mapped
- edge anchors for all four dock positions
- layer-shell exclusive zones as the native replacement for X11 struts
- monitor targeting through layer-shell monitor assignment
- layer switching for keep-above / keep-below where the compositor supports it
- clear fallback when layer-shell is missing, especially on GNOME/Mutter

Taskbar/current-open-app context:

- generic wlroots backend using `zwlr_foreign_toplevel_manager_v1`
- KWin backend using `org_kde_plasma_window_management`
- COSMIC backend using `ext_foreign_toplevel_list_v1`,
  `zcosmic_toplevel_info_v1`, and `zcosmic_toplevel_manager_v1`
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

- KWin geometry/workspace/stacking-order support for the richest parity target
- COSMIC overlap notification support for native overlap-based hiding
- optional Wayfire IPC for overlap, scale/expo, sticky/above, and other extras
- reduced behavior on generic foreign-toplevel compositors where geometry and
  workspaces are unavailable

Preview and menu behavior:

- X11 preview capture remains the X11 preview backend
- native Wayland starts with icon/title preview cards unless a compositor
  provides a real capture path
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
