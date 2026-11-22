"""The on-screen interactions and captions for the Docking tours."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterable
from contextlib import ExitStack, nullcontext, suppress
from pathlib import Path
from typing import TYPE_CHECKING

from docking.applets import get_applet_catalog
from docking.applets.identity import (
    APPLET_CATEGORY_ORDER,
    AppletCategory,
    AppletMeta,
    applet_desktop_id,
)
from tests.visual.backends.x11.run import process, wait_for
from tools.linux.tours.recorder import FOLDER_NAME, NOTES_FOLDER_NAME, OVERVIEW_TRACKS

if TYPE_CHECKING:
    from tools.linux.tours.recorder import Desktop

OVERVIEW_APPLETS = (
    "applications",
    "systemmonitor",
    "music",
    "screenshot",
    "weather",
    "clock",
    "trash",
    "network",
    "bluetooth",
    "hackernews",
    "certwatch",
    "keyboardlayout",
    "volume",
    "whatsapp",
    "telegram",
)
OVERVIEW_ITEMS = (
    applet_desktop_id("applications"),
    "google-chrome.desktop",
    "firefox-stable.desktop",
    "sublime_text.desktop",
    "terminator.desktop",
    applet_desktop_id("aiusage"),
    "pycharm.desktop",
    "caja.desktop",
    applet_desktop_id("systemmonitor"),
    applet_desktop_id("music"),
    None,  # The demo folder stack.
    applet_desktop_id("devices"),
    applet_desktop_id("hydration"),
    applet_desktop_id("screenshot"),
    applet_desktop_id("weather"),
    applet_desktop_id("clock"),
    applet_desktop_id("session"),
    applet_desktop_id("currencyfx"),
    applet_desktop_id("trash"),
    applet_desktop_id("network"),
    applet_desktop_id("bluetooth"),
    applet_desktop_id("hackernews"),
    applet_desktop_id("battery"),
    applet_desktop_id("certwatch"),
    applet_desktop_id("keyboardlayout"),
    applet_desktop_id("desktop"),
    applet_desktop_id("volume"),
    applet_desktop_id("whatsapp"),
    applet_desktop_id("telegram"),
)
OVERVIEW_LAUNCHERS = tuple(
    item for item in OVERVIEW_ITEMS if item and item.endswith(".desktop")
)
UNAVAILABLE = re.compile(
    r"Unavailable|Loading\.\.\.|Not connected|Offline fallback|Error: .+|"
    r"No (?:data yet|battery|city selected|mounted USB devices|"
    r"microphone source found|camera devices found|active player|"
    r"domains configured|plants configured\.?|mounted devices|networks found)|"
    r"No paired devices; open KDE Connect to pair your phone|"
    r"Bluetooth: No adapter/service|"
    r"(?:Bluetooth|Camera state|Camera locking|Keyboard lock state|Microphone state|"
    r"Power Profiles|System Tray|Thermal readings) unavailable|"
    r"Docker unavailable: .+|Docker: (?:unavailable|Docker is unavailable)|"
    r"(?:Hot|Fan): unavailable|"
    r"(?:Telegram|WhatsApp): (?:browser is unavailable|offline|loading\.\.\.)",
    re.IGNORECASE,
)
APPLET_UNAVAILABLE = {
    "certwatch": re.compile(r"\S+: unavailable", re.IGNORECASE),
    "kdeconnect": re.compile(r".+: offline", re.IGNORECASE),
}
HACKER_NEWS_STATS = r"\d+ points, \d+ comments\b"
LIVE_EVIDENCE = {
    "battery": r"\d+%",
    "bluetooth": r"\b[1-9]\d* (?:paired|connected)\b|(?:Paired|Connected): [1-9]",
    "certwatch": r"\b\d+d (?:left|ago)\b",
    "hackernews": HACKER_NEWS_STATS,
    "network": r"\b(?:IP|WiFi|Ethernet):",
    "systemmonitor": r"\bCPU: [\d.]+% \| Mem: [\d.]+%",
    "telegram": r"\bTelegram: scan the QR code\b",
    "thermals": r"\bHot: .*\d+°[CF]",
    "volume": r"\bVolume: \d+%|\bMuted\b",
    "weather": r"\d+°[CF]",
    "whatsapp": r"\bWhatsApp: scan the QR code\b",
}
NO_LEFT_ACTION = {
    "aiusage",
    "battery",
    "camshield",
    "deskpresence",
    "network",
    "separator",
    "systemmonitor",
    "thermals",
    "usbwatch",
}
HOST_LEFT_ACTIONS = {
    "bluetooth": "Toggle the Bluetooth adapter",
    "brightness": "Set the display to full brightness",
    "micshield": "Toggle microphone mute",
    "powerprofiles": "Switch to the next power profile",
    "session": "Lock the screen",
    "volume": "Toggle output mute",
}
BROWSER_LAUNCHERS = {
    "apod",
    "bookmarks",
    "hackernews",
    "lastfm",
    "news",
}
WINDOW_LAUNCHERS = BROWSER_LAUNCHERS | {
    "kdeconnect",
    "music",
    "recentfiles",
    "trash",
}
PICKER_ACTIONS = {
    "colorpicker": "Pick a color",
    "windowkiller": "Choose a window to close",
}
CLICK_TO_CLOSE = {"calculator", "calendar", "clock", "systemtray", "unitconverter"}


def applet_chapters() -> list[tuple[str, list[AppletMeta]]]:
    catalog = get_applet_catalog()
    return [
        (category.value, entries)
        for category in APPLET_CATEGORY_ORDER
        if (
            entries := sorted(
                (meta for meta in catalog.values() if meta.category == category),
                key=lambda meta: meta.name.casefold(),
            )
        )
    ]


def overview_items(folder: str, installed_ids: set[str]) -> list[str]:
    return [
        item or folder
        for item in OVERVIEW_ITEMS
        if item not in OVERVIEW_LAUNCHERS
        or item == "caja.desktop"
        or item in installed_ids
    ]


def applet_menu(desktop: Desktop, applet: str, *, require_live_data: bool = True):
    desktop.point(applet_desktop_id(applet), 3)
    if require_live_data:
        menu = desktop.wait_role("menu")
        verify_status(desktop.menu_labels(), applet=applet)
        return menu
    with suppress(AssertionError):
        return desktop.wait_role("menu", timeout=4)
    return None


def music_menu(desktop: Desktop, action: str) -> None:
    def show_expected_action() -> bool:
        applet_menu(desktop, "music")
        if action in desktop.menu_labels():
            return True
        # The menu captures the applet's state when it opens.
        desktop.dismiss(recenter=False)
        return False

    wait_for(show_expected_action, f"Music menu did not offer {action}")


def unavailable_status(labels: list[str], *, applet: str) -> str | None:
    lines = [line.strip() for label in labels for line in label.splitlines()]
    if applet == "hackernews":
        # Both the tooltip and the menu put the headline immediately before its stats.
        for index, line in enumerate(lines[1:], start=1):
            if re.match(HACKER_NEWS_STATS, line):
                del lines[index - 1]
                break
    specific = APPLET_UNAVAILABLE.get(applet)
    return next(
        (
            line
            for line in lines
            if UNAVAILABLE.fullmatch(line) or (specific and specific.fullmatch(line))
        ),
        None,
    )


def verify_status(labels: list[str], *, applet: str = "") -> None:
    if status := unavailable_status(labels, applet=applet):
        raise RuntimeError(f"Docking reports an unavailable state: {status}")


def live_hover(desktop: Desktop, applet: str, *, contains: str = "") -> None:
    pattern = LIVE_EVIDENCE.get(applet)
    desktop.point(applet_desktop_id(applet))

    def populated():
        labels = desktop.popup_labels()
        if unavailable_status(labels, applet=applet):
            return False
        return any(contains in label for label in labels) and (
            pattern is None or any(re.search(pattern, label, re.I) for label in labels)
        )

    timeout = 60 if applet in {"telegram", "whatsapp"} else 25
    expected = f" for {contains}" if contains else ""
    wait_for(populated, f"Live {applet} data{expected} did not appear", timeout=timeout)


def prepare_live_applets(desktop: Desktop, applet_ids: Iterable[str]) -> None:
    # Wait for live data before recording the shelf or its tooltips.
    for applet in applet_ids:
        if applet in LIVE_EVIDENCE:
            desktop.scene_name = f"Preparing {applet}"
            live_hover(desktop, applet)
    desktop.dismiss()


def input_then_hover(
    desktop: Desktop, applet: str, *, contains: str, button: int = 5
) -> None:
    desktop_id = applet_desktop_id(applet)
    desktop.point(desktop_id, button)
    time.sleep(0.35)
    # Reposition the pointer so the tooltip picks up the result of the input.
    x, y = desktop.item(desktop_id)
    desktop.move(x, y - 2)
    live_hover(desktop, applet, contains=contains)


def overview_shelf(desktop: Desktop, folder: str) -> None:
    with desktop.scene("Docking", "Your apps, files and tools on one shelf."):
        desktop.point(applet_desktop_id("applications"))
        desktop.move(*desktop.item(applet_desktop_id("telegram")), duration=6)
        desktop.hold()

    # Start the walkthrough at the first item after the opening sweep.
    desktop.cursor = desktop.item(applet_desktop_id("applications"))
    desktop.pointer.move(*desktop.cursor)
    with desktop.scene("Applications", "Find installed apps by category or by name."):
        desktop.point(applet_desktop_id("applications"), 1)
        desktop.wait_role("menu")
        for key in "caja":
            desktop.key(key)
        desktop.find("Caja", contains=True)
        desktop.hold()
        desktop.dismiss(recenter=False)

    with desktop.scene(
        "Application menus", "Open actions for an application from the shelf."
    ):
        desktop.point("caja.desktop", 3)
        desktop.wait_role("menu")
        desktop.hold()
        desktop.key("Escape")

    with (
        desktop.scene(
            "Window previews", "Hover a thumbnail, then click to switch windows."
        ),
        ExitStack() as apps,
    ):
        target, starting_window = FOLDER_NAME, NOTES_FOLDER_NAME
        for title, position in ((target, "330+240"), (starting_window, "960+340")):
            path = Path.home() / FOLDER_NAME
            if title == starting_window:
                path /= title
            apps.enter_context(
                process(
                    [
                        "caja",
                        "--no-desktop",
                        "--browser",
                        f"--geometry=530x360+{position}",
                        str(path),
                    ],
                    desktop.observer.output / f"caja-{title}.log",
                    dict(os.environ),
                )
            )
            desktop.window(title)
        x, y, width, height = desktop.window(starting_window).get_geometry()
        desktop.move(x + width // 2, y + height // 2)
        desktop.press()
        desktop.wait_active(starting_window)
        desktop.hold(0.4)
        desktop.point("caja.desktop")
        thumbnail = desktop.find(target)
        desktop.move(*thumbnail.center)
        desktop.hold()
        desktop.press()
        desktop.wait_active(target)
        desktop.hold()
        for title in (target, starting_window):
            desktop.window(title).close(0)

    with desktop.scene("System Monitor", "Hover to see live CPU and memory usage."):
        live_hover(desktop, "systemmonitor")
        desktop.hold()

    with desktop.scene(
        "Music", "Control Spotify, VLC, Rhythmbox, and other MPRIS players."
    ):
        live_hover(desktop, "music", contains=OVERVIEW_TRACKS[0])
        desktop.hold()
        desktop.point(applet_desktop_id("music"), 1)
        desktop.hold(0.6)
        music_menu(desktop, "Play")
        desktop.hold()
        desktop.click("Play")
        desktop.hold(0.6)
        music_menu(desktop, "Pause")
        desktop.hold(0.7)
        desktop.click("Next")
        live_hover(desktop, "music", contains=OVERVIEW_TRACKS[1])
        desktop.hold()

    with desktop.scene("Folder stacks", "Open a pinned folder and browse its files."):
        desktop.point(folder, 1)
        desktop.wait_role("drawing area", popup=True)
        desktop.hold()
        desktop.dismiss(recenter=False)


def web_login(
    desktop: Desktop,
    applet: str,
    title: str,
    *,
    recenter: bool = True,
    require_live_data: bool = True,
) -> None:
    desktop.dismiss(recenter=recenter)
    if require_live_data:
        live_hover(desktop, applet)
    desktop.point(applet_desktop_id(applet), 1)
    try:
        window = desktop.window(title)
    except AssertionError:
        if require_live_data:
            raise
        return
    desktop.move_below_bar(window)
    desktop.hold(3)
    window.close(0)
    desktop.dismiss(recenter=recenter)


def overview(desktop: Desktop, folder: str) -> None:
    overview_shelf(desktop, folder)

    with desktop.scene("Screenshot", "Capture the whole desktop, a window or an area."):
        applet_menu(desktop, "screenshot")
        desktop.hold()
        desktop.click("Region")
        desktop.hold()
        desktop.key("Escape")
        desktop.dismiss(recenter=False)

    with desktop.scene(
        "Weather", "Scroll to switch the forecast from Paris to Belo Horizonte."
    ):
        live_hover(desktop, "weather", contains="Paris")
        desktop.hold()
        input_then_hover(desktop, "weather", contains="Belo Horizonte")
        desktop.hold()

    with desktop.scene("Clock", "Left-click the clock to open or close the calendar."):
        desktop.point(applet_desktop_id("clock"), 1)
        calendar = desktop.wait_role("calendar")
        desktop.hold()
        desktop.point(applet_desktop_id("clock"), 1)
        wait_for(
            lambda: (
                not calendar.get_state_set().contains(desktop.atspi.StateType.SHOWING)
            ),
            "Clock calendar did not close on the second click",
        )
        desktop.hold(0.8)

    with desktop.scene("Trash", "Open the trash directly from the shelf."):
        desktop.hold(0.25)
        applet_menu(desktop, "trash")
        desktop.hold(0.6)
        desktop.click("Open Trash")
        window = desktop.wait_active("Trash", contains=True)
        desktop.move_below_bar(window)
        desktop.hold()
        window.close(0)

    with desktop.scene("Network", "See connection details and live traffic."):
        live_hover(desktop, "network")
        desktop.hold()
        applet_menu(desktop, "network")
        desktop.hold()
        desktop.key("Escape")

    with desktop.scene("Bluetooth", "See paired devices and their available actions."):
        live_hover(desktop, "bluetooth")
        applet_menu(desktop, "bluetooth")

        def paired_device():
            for node, box in desktop.controls():
                with suppress(desktop.observer.GLib.Error):
                    label = node.get_name()
                    if label and re.search(
                        r"\((?:Connected, )?Paired(?:, \d+%)?\)$", label
                    ):
                        return box
            return None

        device = wait_for(paired_device, "No paired Bluetooth device appeared")
        desktop.move(*device.center)
        wait_for(
            lambda: {"Connect", "Disconnect", "Remove Pairing"}.intersection(
                desktop.menu_labels()
            ),
            "Paired Bluetooth device submenu did not open",
        )
        desktop.hold(0.9)
        desktop.key("Escape")
        desktop.dismiss(recenter=False)

    with desktop.scene("Hacker News", "Scroll to browse live headlines from the dock."):
        live_hover(desktop, "hackernews")
        desktop.hold()
        desktop.press(5)
        desktop.hold()

    with desktop.scene(
        "Cert Watch", "Hover to check when the site's TLS certificate expires."
    ):
        live_hover(desktop, "certwatch")
        desktop.hold(2.3)

    with desktop.scene(
        "Keyboard Layout", "Scroll to switch between English and Brazilian Portuguese."
    ):
        live_hover(desktop, "keyboardlayout", contains="English (US)")
        desktop.hold()
        input_then_hover(desktop, "keyboardlayout", contains="Portuguese (BR)")
        desktop.hold()
        applet_menu(desktop, "keyboardlayout")
        wait_for(
            lambda: any(label.startswith("BR - ") for label in desktop.menu_labels()),
            "Brazilian Portuguese did not appear in the keyboard layout menu",
        )
        desktop.hold()
        desktop.dismiss(recenter=False)

    with desktop.scene(
        "Volume", "Click to mute or unmute. Scroll to change the level."
    ):
        live_hover(desktop, "volume", contains="Volume: 50%")
        desktop.hold(0.7)
        input_then_hover(desktop, "volume", contains="Muted", button=1)
        desktop.hold(0.85)
        input_then_hover(desktop, "volume", contains="Volume: 50%", button=1)
        desktop.hold(0.6)
        input_then_hover(desktop, "volume", contains="Volume: 55%", button=4)
        desktop.hold(0.85)
        input_then_hover(desktop, "volume", contains="Volume: 50%")
        desktop.hold(0.85)

    with desktop.scene("WhatsApp", "Open WhatsApp and scan the QR code to connect."):
        web_login(desktop, "whatsapp", "WhatsApp", recenter=False)

    with desktop.scene("Telegram", "Open Telegram and scan the QR code to connect."):
        web_login(desktop, "telegram", "Telegram", recenter=False)

    with desktop.scene(
        "Add News", "Choose Add Applet, then Information and Environment."
    ):
        desktop.shelf_menu(applet_desktop_id("telegram"))
        desktop.move(*desktop.find("Add Applet").center)
        desktop.move(*desktop.find(AppletCategory.INFORMATION.value).center)
        desktop.hold(0.6)
        desktop.click("News")
        desktop.dismiss(recenter=False)
        desktop.item(applet_desktop_id("news"))
        desktop.hold()

    with desktop.scene(
        "More applets in action",
        "Watch the Docking Applets Tour for a chapter-by-chapter walkthrough.",
    ):
        desktop.dismiss(recenter=False)
        desktop.hold(4)


def applet_left_click(
    desktop: Desktop, meta: AppletMeta, *, require_live_data: bool
) -> None:
    desktop_id = applet_desktop_id(meta.id)
    action = HOST_LEFT_ACTIONS.get(meta.id)
    private_audio = (
        meta.id in {"micshield", "volume"}
        and not os.environ.get("PULSE_SERVER")
        and (Path(os.environ["XDG_RUNTIME_DIR"]) / "pulse/native").is_socket()
    )
    if action and not private_audio:
        # The X11 session isolates the dock, but these controls change the host.
        with desktop.scene(meta.name, f"Left click · {action}", button=1):
            desktop.point(desktop_id)
            desktop.hold()
            desktop.dismiss(recenter=False)
        return

    detail = "Left click"
    if action:
        detail += f" · {action}"
    elif meta.id in NO_LEFT_ACTION:
        detail += " · No action assigned"
    elif meta.id == "desktop":
        detail += " · Show the desktop"
    elif meta.id == "devices":
        detail += " · Browse the devices stack"
    elif meta.id == "search":
        detail += " · Open Docking Search"
    elif meta.id in PICKER_ACTIONS:
        detail += f" · {PICKER_ACTIONS[meta.id]}"
    elif meta.id in {"telegram", "whatsapp"}:
        detail += (
            " · Show the QR sign-in screen"
            if require_live_data
            else f" · Open {meta.name}"
        )
    with desktop.scene(meta.name, detail, button=1):
        if meta.id in {"telegram", "whatsapp"}:
            web_login(
                desktop,
                meta.id,
                meta.name,
                recenter=False,
                require_live_data=require_live_data,
            )
            return
        if meta.id in PICKER_ACTIONS:
            desktop.point(desktop_id, 1)
            # The picker covers the desktop and draws its controls at the pointer.
            time.sleep(1.7)
            desktop.dismiss(recenter=False)
            return
        if meta.id == "desktop":
            with process(
                [
                    "caja",
                    "--no-desktop",
                    "--browser",
                    "--geometry=530x360+310+245",
                    str(Path.home() / FOLDER_NAME),
                ],
                desktop.observer.output / "caja-show-desktop.log",
                dict(os.environ),
            ):
                desktop.wait_active(FOLDER_NAME)
                desktop.hold(0.6)
                desktop.point(desktop_id, 1)
                desktop.hold()
                desktop.point(desktop_id, 1)
            desktop.dismiss(recenter=False)
            return

        existing = {window.get_xid() for window in desktop.observer.windows()}
        window_type = desktop.observer.Wnck.WindowType

        def opened_windows():
            return [
                window
                for window in desktop.observer.windows()
                if window.get_xid() not in existing
                and window.get_window_type() in {window_type.NORMAL, window_type.DIALOG}
            ]

        desktop.point(desktop_id, 1)
        if meta.id == "clock":
            desktop.wait_role("calendar")
        elif meta.id == "quicknote":
            desktop.window("Quick Note")
        elif meta.id == "search":
            desktop.wait_active("Docking Search")
        if meta.id in WINDOW_LAUNCHERS:
            timeout = 30 if meta.id in BROWSER_LAUNCHERS else 6
            with suppress(AssertionError):
                wait_for(
                    opened_windows, f"{meta.name} opened no window", timeout=timeout
                )
        else:
            time.sleep(0.55)
        for window in opened_windows():
            desktop.move_below_bar(window)
        desktop.hold()
        if meta.id in CLICK_TO_CLOSE:
            desktop.point(desktop_id, 1)
        desktop.dismiss(recenter=False)
        for window in opened_windows():
            window.close(0)
        wait_for(lambda: not opened_windows(), f"{meta.name} windows did not close")


def applets(
    desktop: Desktop, entries: list[AppletMeta], *, require_live_data: bool = True
) -> None:
    category = entries[0].category.value
    with desktop.scene(category, "Explore the applets in this category."):
        desktop.hold()
    for meta in entries:
        applet_left_click(desktop, meta, require_live_data=require_live_data)
        with desktop.scene(meta.name, "Right click · Open the context menu", button=3):
            if require_live_data and meta.id != "separator":
                live_hover(desktop, meta.id)
                desktop.hold(0.6)
            menu = applet_menu(desktop, meta.id, require_live_data=require_live_data)
            desktop.hold()
            if menu is not None:
                with (
                    nullcontext()
                    if require_live_data
                    else suppress(AssertionError, desktop.observer.GLib.Error)
                ):
                    desktop.show_submenus(menu)
                    if meta.id == "clock":
                        desktop.click("Set Alarm...")
                        desktop.window("Set Alarm")
                        desktop.hold()
            desktop.key("Escape")
            desktop.dismiss(recenter=False)


def preferences_dock_menu(desktop: Desktop) -> None:
    with desktop.scene(
        "Dock menu",
        "Hold Ctrl and right-click an item on the dock.",
        button=3,
        shortcut="Ctrl +",
    ):
        desktop.shelf_menu()
        desktop.wait_role("menu")
        desktop.hold(1)


def preferences(desktop: Desktop) -> None:
    preferences_dock_menu(desktop)
    with desktop.scene("Preferences", "Open Preferences from the shelf menu."):
        desktop.click("Preferences")
        window = desktop.window("Preferences")
        desktop.move_below_bar(window)
        desktop.hold()

    tabs = (
        ("Appearance", "Change the theme, placement and window previews."),
        ("Behavior", "Configure mouse actions, stacks and recent items."),
        ("Applets", "Browse the built-in applets and add them to the shelf."),
        ("Updates", "Choose when Docking checks for updates."),
        ("Import", "Review options for importing a dock configuration."),
    )
    for name, detail in tabs:
        with desktop.scene(name, detail):
            desktop.click(name)
            desktop.hold()
            if name in {"Appearance", "Behavior", "Applets"}:
                scrollbar = desktop.wait_role("scroll bar")
                x, y, width, height = window.get_geometry()
                desktop.move(x + width // 2, y + height - 100)
                for _ in range(100):
                    desktop.pointer.click(5)
                    time.sleep(0.14)
                    if scrollbar.get_current_value() >= scrollbar.get_maximum_value():
                        break
                else:
                    raise RuntimeError(f"Could not scroll to the bottom of {name}")
                desktop.hold()
    window.close(0)

    preferences_dock_menu(desktop)
    with desktop.scene(
        "Diagnostics", "Review the desktop session and available features."
    ):
        desktop.click("Diagnostics")
        window = desktop.window("Diagnostics")
        desktop.move_below_bar(window)
        desktop.find("Overview")
        desktop.hold()
        desktop.click("Features")
        desktop.hold()

    with desktop.scene("Save Report", "Export a diagnostics report when you need it."):
        desktop.click("Save Report...")
        desktop.move_below_bar(desktop.window("Save Diagnostics Report"))
        desktop.hold()
        desktop.key("Escape")


def search(desktop: Desktop) -> None:
    with desktop.scene(
        "Docking Search",
        "Open Search from anywhere on the desktop.",
        shortcut="Ctrl+Alt+Space",
    ):
        desktop.hold(0.6)
        desktop.key("space", modifiers=("Control_L", "Alt_L"))
        desktop.wait_active("Docking Search")
        desktop.hold()

    examples = (
        ("Applications", "Type an application name to find it.", "caja", "Caja"),
        (
            "World clock",
            "Look up the current time in another city.",
            "time in paris",
            "Europe/Paris",
        ),
        ("Calculator", "Evaluate an expression while you type.", "252/6", "42"),
        (
            "Unit conversion",
            "Convert a distance between units.",
            "10 km to miles",
            "6.213",
        ),
    )
    key_names = {" ": "space", "/": "slash"}
    for title, detail, query, result in examples:
        with desktop.scene(title, detail):
            desktop.key("a", modifiers=("Control_L",))
            for char in query:
                desktop.key(key_names.get(char, char))
                time.sleep(0.09)
            desktop.find(result, contains=True)
            desktop.hold(2)
    desktop.key("Escape")
