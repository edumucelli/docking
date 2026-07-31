# Configuration

Docking can be configured from its right-click menu or by editing its JSON
configuration file. The preferences window is the recommended way to change
normal settings because it validates values and applies most changes
immediately.

## Configuration Surfaces

- **Preferences** controls appearance, placement, monitor behavior, layout,
  mouse actions, hiding, stacks, recent items, applets, and update checks.
- **Display** moves the dock to another monitor without opening Preferences.
- **Add Applet** and **Add Separator** add dock items.
- Item context menus pin or remove entries, choose custom icons, and configure
  folder-stack display options.
- **Diagnostics** reports backend and desktop capabilities. It does not change
  configuration.

All normal preferences are stored in:

```text
~/.config/docking/dock.json
```

Docking creates this file on first run. A new dock starts with the Applications
applet, launchers for common applications found on the system, and the Clock,
Calendar, Weather, System Monitor, Hydration, Notifications, and Session
applets.

A configuration file has this basic shape. Docking writes all supported
top-level settings when it saves the file; shortened examples can omit settings
that should use their defaults.

```json
{
  "icon_size": 48,
  "position": "bottom",
  "hide_mode": "none",
  "theme": "default",
  "pinned": [
    {"kind": "applet", "target": "applet://applications"},
    {"kind": "app", "target": "firefox.desktop"},
    {"kind": "folder", "target": "file:///home/user/Downloads"},
    {"kind": "applet", "target": "applet://clock"}
  ],
  "applet_prefs": {},
  "item_prefs": {}
}
```

## Settings Reference

Boolean defaults are shown as `true` or `false`, matching their JSON values.
Fields described as internal state are maintained by Docking and generally
should not be edited by hand.

### Appearance

| Setting | Default | Values | Description |
|---|---:|---|---|
| `theme` | `"default"` | theme name | Active built-in or user theme. User themes take priority over built-in themes with the same name. |
| `icon_size` | `48` | `32` to `128` px | Base icon size before zoom. Theme proportions scale from this value. |
| `transparency` | `1.0` | `0.15` to `1.0` | Multiplier applied to the theme's dock-background alpha. |
| `zoom_enabled` | `true` | boolean | Enlarges icons near the pointer. |
| `zoom_percent` | `1.5` | `1.0` to `4.0` | Maximum zoom multiplier. `1.5` is 150 percent. |
| `zoom_range` | `3` | integer, minimum `0` | Number of icon widths over which parabolic zoom tapers off. This is currently file-only. |
| `tooltips_enabled` | `true` | boolean | Shows item names and dynamic details on hover. |
| `previews_enabled` | `true` | boolean | Shows window thumbnails when hovering over running applications. |
| `show_window_count_numbers` | `false` | boolean | Adds a number to a running indicator when an application has multiple windows. |
| `show_launcher_badges` | `true` | boolean | Shows numeric counts reported through launcher integration. |
| `show_launcher_progress` | `true` | boolean | Shows progress reported through launcher integration. |

See the [Themes guide](THEMES.md) for theme locations, built-in themes, and the
custom theme format.

### Placement and Monitors

| Setting | Default | Values | Description |
|---|---:|---|---|
| `position` | `"bottom"` | `bottom`, `top`, `left`, `right` | Screen edge used by the dock. |
| `additional_distance_from_edge` | `0` | `0` to `100` px | Extra gap added to the active theme's own distance from the screen edge. |
| `current_workspace_only` | `false` | boolean | Shows running windows only from the active workspace when the backend supports it. |
| `active_display` | `false` | boolean | Moves the dock to the monitor containing the pointer. |
| `monitor_index` | `-1` | `-1` or non-negative integer | Monitor fallback. `-1` selects the primary monitor. |
| `monitor_connector` | `null` | output name or `null` | Stable monitor connector selected by Docking when the backend exposes one. It is preferred over the numeric index so monitor selection survives display reordering. |

Enabling `active_display` makes the saved monitor selection a fallback because
the dock follows the pointer at runtime.

### Layout

| Setting | Default | Values | Description |
|---|---:|---|---|
| `lock_icons` | `false` | boolean | Prevents reordering, drag-in, and drag-off removal. |
| `anchor_applets` | `false` | boolean | Keeps applets grouped at the end of the dock. |
| `anchor_files` | `false` | boolean | Keeps pinned files and folders grouped at the end independently of applets. |

### Mouse, Menus, and Stacks

| Setting | Default | Values | Description |
|---|---:|---|---|
| `left_click_action` | `"toggle"` | `toggle`, `cycle`, `most-recent` | Left-click behavior for a running application. |
| `middle_click_action` | `"new-window"` | `new-window`, `minimize`, `close-focused` | Middle-click behavior for application items. |
| `window_list_sort` | `"default"` | `default`, `alphabetical` | Ordering of open windows in application context menus. |
| `stack_unfold` | `"hover"` | `hover`, `click` | Controls whether folder and device stacks open on hover or click. |

Left-click actions behave as follows:

- `toggle` focuses the application, or minimizes its windows when it is already
  focused.
- `cycle` moves focus through the application's open windows.
- `most-recent` focuses the most recently used window, or minimizes when the
  application is already active.

Middle-click actions behave as follows:

- `new-window` asks the application to open a new window when supported.
- `minimize` minimizes all open windows belonging to the application.
- `close-focused` closes the application's focused window.

### Hiding and Revealing

| Setting | Default | Values | Description |
|---|---:|---|---|
| `hide_mode` | `"none"` | see below | Controls when the dock hides and whether it reserves screen space. |
| `hide_delay_ms` | `0` | integer, minimum `0` | Wait before hiding after the pointer leaves. |
| `unhide_delay_ms` | `0` | integer, minimum `0` | Wait before showing after the pointer returns. |
| `hide_time_ms` | `250` | integer, minimum `0` | Hide and show slide-animation duration. This is currently file-only. |
| `pressure_reveal_enabled` | `false` | boolean | Requires pointer pressure at the screen edge before revealing a hidden dock. This is available when the X11 pointer-barrier backend supports it. |
| `pressure_threshold` | `50` | `5` to `500` px | Amount of resisted pointer movement required to reveal the dock. Higher values reduce accidental reveals. |

Hide modes:

- `none` keeps the dock visible and reserves screen space for it.
- `always-on-top` keeps the dock visible above windows without reserving screen
  space.
- `autohide` hides the dock whenever the pointer leaves it.
- `intelligent` hides when a window belonging to the focused application
  overlaps the dock.
- `dodge-active` hides when the focused window overlaps the dock.
- `window-dodge` hides when any window on the active workspace overlaps the
  dock.
- `dodge-maximized` hides when the focused window is maximized or a dialog
  overlaps the dock.

Some hide modes depend on the current desktop backend's window tracking. Use
**Diagnostics** to see which integration is active in the current session.

### Startup and Updates

| Setting | Default | Values | Description |
|---|---:|---|---|
| `startup_tips_enabled` | `true` | boolean | Allows one usage tip after startup when no higher-priority startup notice is shown. |
| `update_check_enabled` | `true` | boolean | Checks GitHub for newer Docking releases. |
| `update_check_interval_hours` | `24` | integer, minimum `1` | Minimum time between automatic checks. Preferences offers daily (`24`) and weekly (`168`). |

Update-check timestamps, ignored releases, and reminder state are runtime state
stored separately in:

```text
~/.local/state/docking/updates.json
```

### Global Search

| Setting | Default | Values | Description |
|---|---:|---|---|
| `global_search_enabled` | `true` | boolean | Enables the shared search palette, D-Bus activation, and XDG GlobalShortcuts registration. |
| `global_search_shortcut` | `CTRL+ALT+space` | captured shortcut | Preferred portal trigger and the active X11 fallback sequence. The desktop portal may retain or assign a different trigger. |
| `global_search_web_engine` | `duckduckgo` | `duckduckgo`, `google`, `brave`, or `bing` | Engine used by fallback searches. |

Click the shortcut button in **Preferences -> Behavior -> Global Search**, then
press the desired sequence. On Wayland, the assignment belongs to the desktop
portal, which may retain or assign a different trigger. On X11, Docking uses
the captured sequence directly when the portal is unavailable.

Relevance privacy, previews, and provider behavior are documented in
[Global Search](SEARCH.md).

### Recent Applications and Documents

| Setting | Default | Values | Description |
|---|---:|---|---|
| `show_recent_apps` | `true` | boolean | Shows recently closed applications between pinned and currently running items. Disabling it clears the saved recent-app history. |
| `recent_apps_max` | `5` | `1` to `15` | Maximum recent application icons to retain and display. |
| `recent_apps_retention_days` | `14` | `1` to `90` days | Removes applications after this many days without use. Preferences offers 3, 7, 14, and 30 days. |
| `recent_apps` | `[]` | list | Internal recent-app history maintained by Docking. Each entry contains a desktop ID and a Unix `last_closed` timestamp. |
| `show_recent_docs_in_menu` | `true` | boolean | Adds a Recent Documents submenu to application context menus. |
| `recent_docs_max` | `10` | `1` to `25` | Maximum recent documents shown for each application. |

## Pinned Items

`pinned` is the ordered list of permanent dock entries. Docking normally
updates it through pin, remove, add-applet, and drag-and-drop actions.

```json
{
  "pinned": [
    {"kind": "applet", "target": "applet://applications"},
    {"kind": "app", "target": "firefox.desktop"},
    {"kind": "folder", "target": "file:///home/user/Downloads"},
    {"kind": "file", "target": "file:///home/user/Documents/notes.txt"},
    {"kind": "applet", "target": "applet://clock"}
  ]
}
```

Each entry has two fields:

| `kind` | `target` format | Meaning |
|---|---|---|
| `app` | desktop file ID such as `firefox.desktop` | Application launcher. |
| `applet` | `applet://<id>` | Built-in applet. Some repeatable applets use an instance suffix such as `#2`. |
| `file` | absolute `file://` URI | Pinned file. |
| `folder` | absolute `file://` URI | Pinned folder displayed as a stack. |

Older configuration files that store pinned entries as plain strings are still
accepted and converted to typed entries when loaded.

## Per-Applet and Per-Item Preferences

`applet_prefs` is a map keyed by applet ID or applet instance. Its contents are
owned by each applet and can include locations, units, timers, display modes,
and other applet-specific values. Configure these through the applet's context
menu or the **Applets** preferences tab whenever that applet exposes controls.

`item_prefs` is keyed by a stable item target. Docking currently uses it for
settings such as custom icon source/path and folder-stack sorting or hidden-file
visibility. These maps are intentionally open-ended so new applet or item
preferences do not require a new top-level configuration field.

## Manual Editing and Recovery

Quit Docking before manually editing `dock.json`; otherwise a setting change or
runtime update can overwrite the file while the application is running. Keep
the file as valid JSON and restart Docking after saving it.

Docking normalizes known values when loading:

- out-of-range numeric settings are clamped where limits exist;
- invalid choices fall back to their defaults;
- malformed pinned and recent-app entries are discarded;
- unknown top-level keys are ignored.

Configuration saves are atomic. Before replacing an existing valid file,
Docking keeps the previous version at:

```text
~/.config/docking/dock.json.bak
```

If the main file cannot be loaded, Docking attempts to recover from that backup
and falls back to defaults only when neither file is usable. The legacy
`folder_stack_unfold` key is also recognized when `stack_unfold` is absent, so
older configuration files keep their stack behavior.
