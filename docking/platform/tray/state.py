"""Immutable tray state; no native toolkit or transport imports."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TrayIconPixmap:
    """Raw StatusNotifier icon pixmap converted to RGBA bytes."""

    width: int
    height: int
    rgba: bytes


@dataclass(frozen=True, slots=True)
class TrayItem:
    """Snapshot of one StatusNotifier item."""

    identifier: str
    service: str
    path: str
    title: str
    status: str
    category: str
    icon_name: str
    attention_icon_name: str
    overlay_icon_name: str
    icon_theme_path: str
    icon_pixmap: TrayIconPixmap | None
    menu_path: str
    tooltip_title: str
    tooltip_text: str
    item_is_menu: bool
    item_id: str = ""

    @property
    def display_title(self) -> str:
        return self.title or self.tooltip_title or self.service

    @property
    def effective_icon_name(self) -> str:
        if self.status.casefold() == "needsattention" and self.attention_icon_name:
            return self.attention_icon_name
        return self.icon_name or self.attention_icon_name or self.overlay_icon_name


@dataclass(frozen=True, slots=True)
class StatusTrayState:
    """Current StatusNotifier state."""

    available: bool
    watcher_mode: str
    items: tuple[TrayItem, ...]
    error: str = ""
    legacy_tray_owner: str = ""


def unavailable_state(error: str = "") -> StatusTrayState:
    return StatusTrayState(
        available=False,
        watcher_mode="unavailable",
        items=(),
        error=error,
        legacy_tray_owner="",
    )
