# Wayland Support

[Back to Docking](../README.md)

For packages and source dependencies, see the [installation guide](INSTALLATION.md).
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

## GNOME Shell Bridge

On GNOME, Docking uses a companion GNOME Shell extension
(`docking-bridge@docking.org`) that provides window management, previews,
workspace switching, and Show Desktop over a private session D-Bus interface.

**How to enable:**

```bash
# Install and enable the extension (one-time)
tools/gnome_bridge.sh install

# Run the dock with the GNOME Shell bridge backend
DOCKING_BACKEND=gnome-shell docking
```

System packages include the extension. AppImage and Nix users should run
`tools/gnome_bridge.sh install` once, or copy
`docking/platform/backends/gnome/extension/` into the GNOME Shell user
extensions directory.

## KWin / KDE Plasma 6

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

## Native layer-shell

On Cinnamon Wayland, Docking prefers native layer-shell when available. Older
Muffin uses an XWayland dock surface so the dock stays out of Cinnamon's panel
application list and Alt+Tab. Window tracking and applet services still use
Cinnamon's native API, including for native Wayland applications. This fallback
requires a working XWayland display; explicit `GDK_BACKEND` and unrelated
`DOCKING_BACKEND` choices are preserved.

Native layer-shell mode needs a compositor with `zwlr_layer_shell_v1`.
See [Source Requirements](INSTALLATION.md#source-requirements) for the
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

## Jay client capabilities

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

## Miriway shell component

Miriway deliberately limits layer-shell, foreign-toplevel management, and
workspace management to trusted shell components. Add Docking to
`~/.config/miriway-shell.config`:

```ini
shell-component=docking
```

Restart Miriway after changing this file. Miriway then launches Docking with
the privileged Wayland socket. Starting Docking as an ordinary application
leaves these protocols hidden and results in reduced launcher-only behavior.
