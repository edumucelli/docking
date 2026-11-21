"""The on-screen interactions and captions for the three Docking tours."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterable
from contextlib import ExitStack
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
from tools.linux.tours.recorder import FOLDER_NAME, NOTES_FOLDER_NAME

if TYPE_CHECKING:
    from tools.linux.tours.recorder import Desktop

OVERVIEW_APPLETS = (
    "applications",
    "clock",
    "weather",
    "hackernews",
    "whatsapp",
    "telegram",
    "network",
    "bluetooth",
    "volume",
    "screenshot",
    "trash",
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
    applet_desktop_id("thermals"),
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
    applet_desktop_id("reddit"),
    applet_desktop_id("battery"),
    applet_desktop_id("certwatch"),
    applet_desktop_id("keyboardlayout"),
    applet_desktop_id("desktop"),
    "eom.desktop",
    applet_desktop_id("whatsapp"),
    applet_desktop_id("telegram"),
)
OVERVIEW_LAUNCHERS = tuple(
    item for item in OVERVIEW_ITEMS if item and item.endswith(".desktop")
)
UNAVAILABLE = re.compile(
    r"^(?:.*\bunavailable\b|loading\b|error:|not connected$|"
    r"no (?:data|battery|city selected|paired devices|mounted usb devices|"
    r"microphone source|camera devices|active player|hacker news stories|"
    r"domains configured|plants configured|mounted devices|networks found)\b|"
    r".*\b(?:sign in required|offline|waiting for sign.in)\b)",
    re.IGNORECASE | re.MULTILINE,
)
LIVE_EVIDENCE = {
    "battery": r"\d+%",
    "bluetooth": r"\b[1-9]\d* (?:paired|connected)\b|(?:Paired|Connected): [1-9]",
    "hackernews": r"\b(?:points?|comments?)\b",
    "network": r"\b(?:IP|WiFi|Ethernet):",
    "telegram": r"\bTelegram: scan the QR code\b",
    "volume": r"\bVolume: \d+%|\bMuted\b",
    "weather": r"\d+°[CF]",
    "whatsapp": r"\bWhatsApp: scan the QR code\b",
}


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


def applet_menu(desktop: Desktop, applet: str) -> None:
    desktop.point(applet_desktop_id(applet), 3)
    desktop.wait_role("menu")
    verify_status(desktop.menu_labels())


def verify_status(labels: list[str]) -> None:
    for label in labels:
        if UNAVAILABLE.search(label):
            raise RuntimeError(f"Docking reports an unavailable state: {label}")


def live_hover(desktop: Desktop, applet: str) -> None:
    pattern = LIVE_EVIDENCE.get(applet)
    desktop.point(applet_desktop_id(applet))

    def populated():
        labels = desktop.popup_labels()
        if any(UNAVAILABLE.search(label) for label in labels):
            return False
        return bool(labels) and (
            pattern is None or any(re.search(pattern, label, re.I) for label in labels)
        )

    wait_for(populated, f"Live {applet} data did not appear", timeout=25)


def prepare_live_applets(desktop: Desktop, applet_ids: Iterable[str]) -> None:
    # Wait for live data before recording the shelf or its tooltips.
    for applet in applet_ids:
        if applet in LIVE_EVIDENCE:
            desktop.scene_name = f"Preparing {applet}"
            live_hover(desktop, applet)
    desktop.dismiss()


def overview_shelf(desktop: Desktop, folder: str) -> None:
    with desktop.scene("Docking", "Your apps, files and tools on one shelf."):
        items = [item or folder for item in OVERVIEW_ITEMS]
        for item in (*items[::3], items[-1]):
            desktop.point(item)
        desktop.hold()

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
        desktop.pointer.click()
        desktop.wait_active(starting_window)
        desktop.hold(0.4)
        desktop.point("caja.desktop")
        thumbnail = desktop.find(target)
        desktop.move(*thumbnail.center)
        desktop.hold()
        desktop.pointer.click()
        desktop.wait_active(target)
        desktop.hold()
        for title in (target, starting_window):
            desktop.window(title).close(0)

    with desktop.scene("Folder stacks", "Open a pinned folder and browse its files."):
        desktop.point(folder, 1)
        desktop.wait_role("drawing area", popup=True)
        desktop.hold()
        desktop.dismiss()

    with desktop.scene("Applications", "Find installed apps by category or by name."):
        desktop.point(applet_desktop_id("applications"), 1)
        desktop.wait_role("menu")
        for key in "caja":
            desktop.key(key)
        desktop.find("Caja", contains=True)
        desktop.hold()
        desktop.dismiss()

    with desktop.scene("Clock", "Left-click the clock to open the calendar."):
        desktop.point(applet_desktop_id("clock"), 1)
        desktop.wait_role("calendar")
        desktop.hold()
        desktop.point(applet_desktop_id("clock"), 1)
        desktop.dismiss()


def web_login(desktop: Desktop, applet: str, title: str) -> None:
    desktop.dismiss()
    live_hover(desktop, applet)
    desktop.point(applet_desktop_id(applet), 1)
    window = desktop.window(title)
    desktop.move_below_bar(window)
    desktop.hold(3)
    window.close(0)
    desktop.dismiss()


def overview(desktop: Desktop, folder: str) -> None:
    overview_shelf(desktop, folder)

    with desktop.scene(
        "Weather", "Hover for current conditions and the city forecast."
    ):
        live_hover(desktop, "weather")
        desktop.hold()

    with desktop.scene("Hacker News", "Scroll to browse live headlines from the dock."):
        live_hover(desktop, "hackernews")
        desktop.hold()
        desktop.pointer.click(5)
        desktop.hold()

    with desktop.scene("WhatsApp", "Open WhatsApp and scan the QR code to connect."):
        web_login(desktop, "whatsapp", "WhatsApp")

    with desktop.scene("Telegram", "Open Telegram and scan the QR code to connect."):
        web_login(desktop, "telegram", "Telegram")

    with desktop.scene("Network", "See connection details and live traffic."):
        live_hover(desktop, "network")
        desktop.hold()
        applet_menu(desktop, "network")
        desktop.hold()
        desktop.key("Escape")

    with desktop.scene("Bluetooth", "See paired devices and their available actions."):
        live_hover(desktop, "bluetooth")
        applet_menu(desktop, "bluetooth")
        if not desktop.show_submenus():
            raise RuntimeError("No paired Bluetooth device submenu appeared")
        desktop.key("Escape")

    with desktop.scene("Volume", "Add Volume to see the current level and mute state."):
        desktop.shelf_menu(applet_desktop_id("clock"))
        desktop.move(*desktop.find("Add Applet").center)
        desktop.move(*desktop.find(AppletCategory.SYSTEM.value).center)
        desktop.hold(0.6)
        desktop.click("Volume")
        desktop.dismiss()
        desktop.hold(0.3)
        live_hover(desktop, "volume")
        desktop.hold()
        applet_menu(desktop, "volume")
        desktop.click("Show Level")
        desktop.dismiss()
        desktop.point(applet_desktop_id("volume"))
        desktop.hold()
        applet_menu(desktop, "volume")
        desktop.click("Remove from Dock")
        desktop.dismiss()

    with desktop.scene("Screenshot", "Capture the whole desktop, a window or an area."):
        applet_menu(desktop, "screenshot")
        desktop.hold()
        desktop.click("Region")
        desktop.hold()
        desktop.key("Escape")
        desktop.dismiss()

    with desktop.scene("Trash", "Open the trash directly from the shelf."):
        desktop.hold(0.25)
        applet_menu(desktop, "trash")
        desktop.hold(0.6)
        desktop.click("Open Trash")
        window = desktop.wait_active("Trash", contains=True)
        desktop.move_below_bar(window)
        desktop.hold()
        window.close(0)

    with desktop.scene(
        "More applets in action",
        "Watch the Docking Applets Tour for a chapter-by-chapter walkthrough.",
    ):
        desktop.dismiss()
        desktop.hold(4)


def applets(desktop: Desktop, entries: list[AppletMeta]) -> None:
    category = entries[0].category.value
    with desktop.scene(category, "Explore the applets in this category."):
        desktop.hold()
    for meta in entries:
        with desktop.scene(meta.name, f"{category} · Applet menu and controls"):
            if meta.id != "separator":
                live_hover(desktop, meta.id)
                desktop.hold(0.6)
            applet_menu(desktop, meta.id)
            desktop.hold()
            desktop.show_submenus()
            if meta.id == "clock":
                desktop.click("Set Alarm...")
                desktop.window("Set Alarm")
                desktop.hold()
                desktop.key("Escape")
            elif meta.id == "quicknote":
                desktop.key("Escape")
                desktop.point(applet_desktop_id(meta.id), 1)
                desktop.window("Quick Note")
                desktop.hold()
                desktop.key("Escape")
            else:
                desktop.key("Escape")
            desktop.dismiss()
        if meta.id in {"whatsapp", "telegram"}:
            with desktop.scene(meta.name, "Scan the QR code to connect."):
                web_login(desktop, meta.id, meta.name)


def preferences(desktop: Desktop) -> None:
    with desktop.scene("Preferences", "Open Preferences from the shelf menu."):
        desktop.shelf_menu()
        desktop.wait_role("menu")
        desktop.hold(0.55)
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
            if name in {"Appearance", "Behavior"}:
                x, y, width, height = window.get_geometry()
                desktop.move(x + width // 2, y + height - 100)
                for _ in range(11):
                    desktop.pointer.click(5)
                    time.sleep(0.14)
                desktop.hold()
    window.close(0)

    with desktop.scene(
        "Diagnostics", "Review the desktop session and available features."
    ):
        desktop.shelf_menu()
        desktop.wait_role("menu")
        desktop.hold(0.55)
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
