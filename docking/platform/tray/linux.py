"""StatusNotifier/AppIndicator transport behind the shared tray contract."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from docking.platform.backends.base import Rect
from docking.platform.status_notifier.backend import StatusNotifierBackend
from docking.platform.status_notifier.dbusmenu import DBusMenuClient, DBusMenuItem
from docking.platform.tray.menu import TrayMenuItem, TrayMenuLayout
from docking.platform.tray.state import (
    TrayFocusRequest,
    TrayIconPixmap,
    TrayItem,
    TrayState,
)


def _menu(item: DBusMenuItem) -> TrayMenuItem:
    return TrayMenuItem(
        item.item_id,
        item.label,
        item.enabled,
        item.visible,
        item.item_type,
        item.icon_name,
        item.icon_data,
        item.toggle_type,
        item.toggle_state,
        tuple(_menu(child) for child in item.children),
    )


class LinuxTrayMenu:
    def __init__(self, client: DBusMenuClient) -> None:
        self._client = client

    def about_to_show(self, item_id: int = 0) -> bool:
        return self._client.about_to_show(item_id)

    def get_layout(self) -> TrayMenuLayout | None:
        layout = self._client.get_layout()
        return TrayMenuLayout(layout.revision, _menu(layout.root)) if layout else None

    def event(self, item_id: int) -> bool:
        return self._client.event(item_id)


class LinuxTrayBackend:
    def __init__(self, *, backend: StatusNotifierBackend | None = None) -> None:
        self._backend = backend if backend is not None else StatusNotifierBackend()
        self._clients: dict[str, LinuxTrayMenu] = {}

    def get_state(self) -> TrayState:
        state = self._backend.get_state()
        items = []
        for item in state.items:
            image = item.icon_pixmap
            items.append(
                TrayItem(
                    identifier=item.identifier,
                    title=item.display_title,
                    status=item.status,
                    category=item.category,
                    icon_name=item.icon_name,
                    attention_icon_name=item.attention_icon_name,
                    overlay_icon_name=item.overlay_icon_name,
                    icon_theme_path=item.icon_theme_path,
                    icon_pixmap=TrayIconPixmap(image.width, image.height, image.rgba)
                    if image
                    else None,
                    tooltip_title=item.tooltip_title,
                    tooltip_text=item.tooltip_text,
                    item_is_menu=item.item_is_menu,
                    item_id=item.item_id,
                )
            )
        clients = {}
        for item in state.items:
            client = self._backend.menu_client(item.identifier)
            if client is not None:
                clients[item.identifier] = LinuxTrayMenu(client)
        self._clients = clients
        return TrayState(
            state.available,
            state.watcher_mode,
            tuple(items),
            state.error,
            state.legacy_tray_owner,
        )

    def set_geometry(self, bounds: Mapping[str, Rect]) -> None:
        # SNI receives the invocation point directly in each action.
        pass

    def pointer(
        self,
        identifier: str | None,
        *,
        point: tuple[int, int] | None = None,
        anchor: Rect | None = None,
    ) -> bool:
        # SNI has no provider hover protocol; GTK owns its standard tooltip.
        return False

    def set_focus_handler(
        self, handler: Callable[[TrayFocusRequest], None] | None
    ) -> None:
        # SNI menus do not send native focus-return requests.
        pass

    def activate(
        self, identifier: str, *, anchor: Rect | None = None, keyboard: bool = False
    ) -> bool:
        return (
            self._backend.activate(identifier, x=anchor.x, y=anchor.bottom)
            if anchor
            else self._backend.activate(identifier)
        )

    def context_menu(
        self, identifier: str, *, anchor: Rect | None = None, keyboard: bool = False
    ) -> bool:
        return (
            self._backend.context_menu(identifier, x=anchor.x, y=anchor.bottom)
            if anchor
            else self._backend.context_menu(identifier)
        )

    def double_click(self, identifier: str, *, anchor: Rect | None = None) -> bool:
        # SNI exposes semantic activation rather than a native double-click.
        return False

    def menu_client(self, identifier: str) -> LinuxTrayMenu | None:
        return self._clients.get(identifier)

    def close(self) -> None:
        self._clients.clear()
        self._backend.close()
