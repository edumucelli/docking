"""Immutable tray metadata; no transport handles or platform API operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TrayIconPixmap:
    width: int
    height: int
    rgba: bytes


@dataclass(frozen=True, slots=True)
class TrayFocusRequest:
    """A provider's one-use, revalidated request to return to its tray icon."""

    identifier: str
    claim: Callable[[], bool]
    validate: Callable[[], bool]


@dataclass(frozen=True, slots=True)
class TrayItem:
    identifier: str
    title: str
    status: str = "Active"
    category: str = ""
    icon_name: str = ""
    attention_icon_name: str = ""
    overlay_icon_name: str = ""
    icon_theme_path: str = ""
    icon_pixmap: TrayIconPixmap | None = None
    tooltip_title: str = ""
    tooltip_text: str = ""
    show_tooltip: bool = True
    item_is_menu: bool = False
    item_id: str = ""
    can_activate: bool = True
    can_context_menu: bool = True
    can_double_click: bool = False

    @property
    def display_title(self) -> str:
        return self.title or self.tooltip_title or self.identifier

    @property
    def effective_icon_name(self) -> str:
        if self.status.casefold() == "needsattention" and self.attention_icon_name:
            return self.attention_icon_name
        return self.icon_name or self.attention_icon_name or self.overlay_icon_name


@dataclass(frozen=True, slots=True)
class TrayState:
    available: bool
    mode: str
    items: tuple[TrayItem, ...]
    error: str = ""
    legacy_owner: str = ""


def unavailable_state(error: str = "") -> TrayState:
    return TrayState(False, "unavailable", (), error)
