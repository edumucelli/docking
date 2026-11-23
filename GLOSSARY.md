# Docking

Docking is a desktop dock for launching applications, switching between open
windows, and keeping files, folders, and applet tools within reach.

## Language

- [Dock items and layout](#dock-items-and-layout)
- [Applications and windows](#applications-and-windows)
- [Appearance, placement, and input](#appearance-placement-and-input)
- [Dock visibility](#dock-visibility)
- [Menus, previews, and stacks](#menus-previews-and-stacks)
- [Applets and tray](#applets-and-tray)
- [Global Search](#global-search)
- [Desktop integration](#desktop-integration)

### Dock items and layout

**Dock**

The edge-mounted desktop interface that contains launchers, running and recent
applications, pinned files and folders, separators, and applets.

**Shelf**

The visible background behind the dock items. The dock surface can extend
beyond the shelf to accommodate zoom and animations.

**Dock item**

An individual entry in the dock, such as an application launcher, file, folder,
applet, or separator. Its icon represents the item and can reflect live state.

**Pinned entry**

A saved entry in the user's ordered dock layout. A pin can be saved even when
its application or target is unavailable. See also [Transient item](#transient-item).

**Dock layout**

The order and grouping of dock items. The saved layout records the ordered
pins and preferences that control how they are grouped.

**Item target**

The identifier of what a dock item refers to, such as an application's desktop
ID, a file or folder URI, or an applet instance ID.
See [Desktop ID](#desktop-id) and [Applet instance](#applet-instance).

**Pinned file**

A saved dock item that opens a specific file with its associated application
by default.

**Pinned folder**

A saved dock item that provides access to a folder and its contents, through a
folder stack or the folder's context menu.

<a name="transient-item"></a>

**Transient item**

An unpinned application item whose presence depends on current activity, such
as an open window or an app-reported launcher badge, progress, or urgency.

**Launcher-only item**

A transient application item shown for app-reported launcher status while the
application has no tracked window. See [LauncherEntry](#launcherentry).

**Recent app**

An unpinned application in Docking's recent history, typically recorded when
its last tracked window closes. When recent apps are enabled, the dock can
show it between pinned and transient items.

**Pinned Only**

The layout preference that shows only pinned entries on the shelf. Hidden
applications can still be tracked and included in recent history.

**Anchored section**

A group kept at the end of the dock by a layout preference. Pinned files and
folders can be anchored as one group, independently of applets.

**Separator**

An adjustable gap between dock items. Each separator can have its own saved
width.

**Edit Layout**

The Preferences editor for adding, removing, and reordering pins. Changes
can be previewed in the dock and are saved when applied.

**Layout import**

Importing compatible pins and layout preferences from a Docking
configuration, a Plank setup, or a Cairo-Dock setup.
[Import a dock layout](docs/CONFIGURATION.md#import-a-dock-layout)

### Applications and windows

**Application launcher**

A dock item that opens an application or, when it has tracked windows, offers
window interactions such as focusing or cycling.

**Application catalog**

The application listings discovered for browsing and searching.

**Application listing**

A launchable application entry for browsing or finding apps. Docking can pin
a listing when it has a stable desktop ID.

**Application registry**

Docking's shared reference for discovered application identities and metadata.
It can resolve entries that are hidden from the application catalog.

<a name="desktop-id"></a>

**Desktop ID**

The application identifier used in pins and to group matched windows. A Unix
example is `firefox.desktop`.

**Desktop entry**

A Unix `.desktop` description of an application, including its name, icon,
launch command, and optional actions.

**Generated launcher**

A saved application launcher created by Docking for a target such as an
AppImage or executable that needs a registered application identity.

**Runtime application**

An application identity derived from a running executable so Docking can
group its windows, including when the application has no saved launcher.

**Window tracking**

The session backend's view of open windows and their available state, such as
focus, urgency, and workspace membership.

**Window ID**

The session backend's identifier for a specific window. Windows grouped under
the same [desktop ID](#desktop-id) each have their own window ID.

**Window matching**

The association of a tracked window with a desktop ID so its launcher,
running indicator, and window actions refer to the same application.

**Match evidence**

Information that supports a window match, such as the window system's app ID,
X11 window class (`WM_CLASS`), executable identity, or launch provenance.

**Launch provenance**

The launcher identity Docking recorded when it started a process. A window
from that process can use the record as match evidence.

**Running app**

An application with at least one tracked window. Its dock item represents the
group of matching windows and can be pinned or transient.

**Application action**

A shortcut declared by an application, such as **New Private Window**.
Also called a desktop action or a quicklist action in application menus.

**File association**

The desktop's mapping from a file type to an application that can open it.
The default association is used for ordinary file opening.

**Recent documents**

Recently opened files associated with a particular application. They can
appear in that application's context menu and in Global Search.

**Workspace**

A virtual desktop within the active desktop session.

**Current Workspace Only**

The preference that limits running-app window lists and counts to windows on
the active workspace, where supported.

### Appearance, placement, and input

**Theme**

A named set of Docking appearance and spacing settings, including the shelf,
item layout, and running indicators. [Themes](docs/CONFIGURATION.md#themes)

**Icon theme**

The desktop's set of named images, used by Docking when an application or
applet specifies an icon by name.

**Custom icon**

A user-selected image for a pinned application, file, or folder.

**Running indicator**

The dots or dashes beside an application's icon that signal tracked windows.
The indicator can distinguish the focused application and optionally show a
window count.

**Badge**

A marker on an icon that shows a count or status, such as unread messages or
unchecked checklist items.

**Launcher progress**

Progress reported by an application for display on its dock item, such as
the progress of a download.

**Urgency**

A request for attention from a tracked window or an application integration,
reflected on the corresponding dock item.

**Click effect**

A visual response to launching an application from the dock. The available
effects are Bounce, Pulse, and Wobble.

**Dock surface**

Docking's desktop window, including space for the shelf, its items, and any
transparent margins.

**Workarea**

The usable portion of a monitor after the desktop accounts for reserved
space, such as a panel or taskbar.

**Dock position**

The screen edge the dock occupies on its selected monitor.

**Dock alignment**

The preferred placement of the shelf along its screen edge. The visible
position is constrained by the available space.

**Edge gap**

The spacing between the shelf and its screen edge. The effective gap
combines the theme's spacing and the user's additional distance setting.

**Active Display**

The placement preference that moves the dock to the monitor containing the
pointer. The selected monitor serves as a fallback.

**Monitor connector**

The desktop's stable output name for identifying a monitor across display
reordering, where the backend provides one.

**Main axis and cross axis**

The main axis runs along the dock edge and its row or column of items. The
cross axis runs perpendicular to the edge, along the shelf's thickness.

**Hover zoom**

The magnification of dock items near the pointer, tapering away from the
hovered position.

**Input region**

The part of the dock surface that accepts pointer interaction. Its bounds can
differ from the shelf and can shrink to an edge strip when the dock is hidden.

**Item bounds**

The draw rectangle locates the rendered icon, the hover rectangle determines
hover, and the hit rectangle determines click and context-menu targets.

**Popup anchor**

The position on or beside a dock item that locates its tooltip, preview, or
stack relative to the shelf.

**Drop insertion slot**

The position in the dock order where a dragged item will be inserted.

**Lock Positions**

The preference that prevents adding, removing, and reordering dock items by
dragging them.

### Dock visibility

**Hide mode**

The preference governing whether the dock reserves screen space, where it
stacks relative to application windows, and when it hides or reveals.

**Don't Hide**

The mode that keeps the dock visible and requests a screen reservation.

**Always on Top**

The mode that keeps the dock above application windows and leaves its edge
space available to them.

**Always behind**

The mode that places the dock above the desktop and below application
windows, leaving its edge space available to them.

**Auto-hide**

The mode that hides the dock when the pointer leaves it and reveals it
through the screen edge.

**Show Only with Shortcut**

The mode that reveals the dock through the **Dock Shortcut**. The dock can
hide on a second press or after the pointer enters and then leaves it.

**Dock Shortcut**

The system-wide key combination assigned to show or hide the dock in
**Show Only with Shortcut** mode.

**Dodge mode**

The family of hide modes that responds to overlap between the dock and
windows eligible under the selected mode.

**Intelligent**

The dodge mode that considers overlapping windows belonging to the focused
application.

**Dodge Active**

The dodge mode that considers overlap with the focused window itself.

**Dodge Windows**

The dodge mode that considers any eligible overlapping window on the active
workspace.

**Dodge Maximized**

The dodge mode that considers overlap with a maximized focused window or a
dialog belonging to the focused application. Some backends also consider
fullscreen windows.

**Trigger strip**

The narrow screen-edge area used to sense the pointer when an edge-reveal
mode has hidden the dock.

**Pressure reveal**

An edge-reveal option that requires the pointer to push against a pointer
barrier before showing the dock, where the backend supports it.

### Menus, previews, and stacks

**Dock menu**

The shelf background's context menu, with dock-wide commands such as
**Preferences**, **Add Applet**, **Add Separator**, and **Diagnostics**.

**Item context menu**

The context menu for one dock item. Its commands depend on the item,
such as **Keep in Dock** for an unpinned application or sort options for a
pinned folder.

**Tooltip**

The hover popup that shows an item's name or current status. Applets can
provide richer hover content with multiple fields or images.

<a name="window-preview"></a>

**Window preview**

The popup attached to a running application's dock item, with a card for
each eligible window and a thumbnail where supported. A selected card can
activate its window.

**Thumbnail highlight**

A border around the preview thumbnail under the pointer.

**Window outline**

A border around the actual desktop window represented by the hovered
preview thumbnail, where supported.

**Stack**

A popup attached to a dock item with a list of selectable entries. Small
stacks can fan out from the item, while overflowing stacks use a scrollable
view.

**Folder stack**

The stack for a pinned folder, showing its contents for quick opening.

**Folder browser**

The pinned folder's menu of contents and file actions, including nested
folders and per-entry context menus.

**Stack unfold**

The hover-or-click preference for opening folder and device stacks.

### Applets and tray

**Applet**

A dock-resident tool that displays information, controls a desktop feature,
or provides a quick action alongside other dock items.
[Applets guide](docs/APPLETS.md)

**Applet catalog**

The set of available applet types, grouped into categories for the **Add
Applet** menu and the **Applets** tab in Preferences.

**Applet ID**

The identifier of an applet type, such as `clock` or `separator`.

<a name="applet-instance"></a>

**Applet instance**

One occurrence of an applet in the dock, with its own identity and
preferences. Repeatable applets can use a suffix, as in
`applet://separator#2`.

**Applet preferences**

Saved settings owned by an applet or an individual instance, such as a
location, timer, or display mode.

**Item preferences**

Saved settings for a specific dock item, such as its custom icon or folder
browsing options.
[Per-applet and per-item preferences](docs/CONFIGURATION.md#per-applet-and-per-item-preferences)

**Applications applet**

The categorized launcher menu for browsing and filtering applications.

**Checklist**

The single reusable list shown by the Checklist applet.

**Checklist item**

A text entry on the checklist. An unchecked item is still open. A checked item
is completed and remains on the list until it is cleared.

**Clipboard history**

Clippy's session-local list of copied text and, when enabled, images. An entry
can be selected to copy its original content back to the system clipboard.

**Recent Files applet**

The applet for opening files from the desktop's recent file history. Global
Search can also find files from that history across applications.

**System Tray**

The applet that hosts status icons and actions registered by desktop
applications and services.

**Tray item**

A single application- or service-provided status icon in the System Tray,
with its own tooltip, activation, or menu when available.

**Notifications applet**

The notification-center control in the dock. It can expose the desktop's
Do Not Disturb setting, pending count, and notification history controls.

### Global Search

**Global Search**

Docking's shared search palette for finding applications, windows, dock
items, files, and utility results. The Search applet, dock menu, and global
shortcut all open the same palette.
[Global Search guide](docs/CONFIGURATION.md#global-search)

**Global Search shortcut**

The system-wide key combination that opens the search palette from anywhere
in the desktop session, where shortcut registration is available.

**Search provider**

A source of results and actions for one area of Global Search, such as
Applications, Windows, Recent Files, or Calculator.

**Query intent**

Global Search's interpretation of the entered text, such as an ordinary
search, direct path, URL, calculation, conversion, or date and time request.

**Search result**

A selectable match or computed answer returned by a provider. It can have a
primary action, additional actions, and a preview.

**Primary action**

The default action for a selected search result, such as opening a file,
focusing a window, or copying a calculated value.

**Action Panel**

The list of actions available for the selected result in Global Search.

**Application refinement**

The view of a selected application's own actions, individual windows, and
recent documents within Global Search.

**Result preview**

The optional preview pane in Global Search for the selected result, such as
a file excerpt, image, application summary, or available window capture.
See also [Window preview](#window-preview).

**Relevance learning**

The small ranking boost based on prior result and action selections,
including how often and how recently they were used.

### Desktop integration

**Host platform**

The operating-system environment Docking runs on. It provides filesystem,
process, storage, and other system facilities independently of the desktop
session.

**Desktop session**

The active graphical environment, including its display system and window
manager, compositor, or shell.

**Session backend**

Docking's integration with the active desktop session for services such as
placement, window tracking and actions, previews, and workspaces.

**Backend capability**

A feature the selected backend reports as available, such as window
activation, screen reservation, or workspace switching. Individual windows
can have their own action availability.

**Reduced mode**

The fallback integration for sessions without a supported desktop backend.
Basic launching remains available, with limited access to desktop features.

**Diagnostics**

The report of the active backend, its reported capabilities, and the current
environment and placement. [First use](docs/CONFIGURATION.md#first-use)

**Screen reservation**

A request to set aside edge space for the dock so ordinary maximized
windows leave room for it. Implementations include an X11 strut, a Wayland
layer-shell exclusive zone, and a Windows AppBar reservation.

**Layer-shell**

A Wayland protocol Docking can use to place a dock surface at a screen edge
and request reserved space on compatible compositors.
[Wayland support](docs/INSTALLATION.md#wayland-support)

**GNOME Shell bridge**

The companion GNOME Shell extension through which Docking gets access to
placement, windows, previews, workspaces, and Show Desktop on GNOME Wayland.
[GNOME Shell bridge](docs/INSTALLATION.md#gnome-shell-bridge)

**Toplevel**

A top-level application window exposed by the Wayland backend. The available
toplevel protocol determines which state and actions Docking can use.

<a name="launcherentry"></a>

**LauncherEntry**

The Unity launcher integration through which applications can publish
counts, progress, and urgency for their dock items.

**StatusNotifier Item (SNI)**

A tray entry published through the StatusNotifier interface on Linux and BSD.
The System Tray can also present native Windows tray icons.

**Show Desktop**

The desktop session's action for revealing the desktop and restoring the
previous window view. The Desktop applet exposes it where supported.
