# Accessibility

Docking exposes applications, files, folders, and applets as native GTK buttons
through AT-SPI, the Linux accessibility service used by Orca and other assistive
technology. Each item has a name, an activation action, a context-menu action,
and screen bounds matching its dock slot. Descriptions include running, active,
pinned, attention, notification-count, and progress information when applicable.
Names and descriptions update when the model or an applet changes. Decorative
separators and items that are only animating out are excluded.

## Keyboard access

With Docking running, execute:

```sh
docking --focus
```

Assign this command to a shortcut in your desktop's keyboard settings. Docking
does not reserve a global key combination. The command opens **Dock navigation**,
a normal GTK window with named item buttons and a **Dock options** button. This
provides a focusable entry point without changing the dock surface's compositor
role or stealing focus during normal mouse use.

- Arrow keys move between items; Home and End select the first and last item.
- Enter or Space activates the focused button.
- Shift+F10 or the Menu key opens the selected item's context menu.
- Page Up and Page Down perform an applet's scroll actions, such as adjusting volume.
- Tab reaches Dock options and cycles through native controls.
- Escape closes a menu, then closes navigation.

Navigation holds an autohidden dock visible until the navigation window closes.
If an item disappears, navigation selects a surviving neighbor. Reordering
preserves selection by item identity.

Screen-reader users can also request focus on a dock item through AT-SPI's
Component interface to open navigation at that item. The Action interface offers
`activate` and `menu`, plus `scroll-up` and `scroll-down` for applets that handle
wheel input. Folder stacks and applet-provided stacks have native GTK
menu alternatives with the same entries and actions. Ordinary application and
applet activation uses the existing action handlers.

## Scope and verification

This support covers the dock's items, keyboard navigation, item/dock menus, and
folder/applet stack alternatives. It does not redesign every applet-owned custom
popup or guarantee that third-party applications are accessible. Applet status
text is exposed when the applet updates its item name; no widget tooltips are
scraped or converted into a second source of state.

The automated GTK tests can be run headlessly:

```sh
xvfb-run -a env PYTHONPATH=. .venv/bin/python -m pytest -q tests/ui/test_accessibility.py
```

They exercise real GTK/ATK interfaces, identity changes, stale actions, focus,
geometry, and cleanup. A separate AT-SPI client and native input have also been
exercised against the actual dock on X11. Wayland and spoken Orca output still
need desktop-specific manual validation; the navigation window uses ordinary
GTK focus requests, subject to the compositor's focus policy.
