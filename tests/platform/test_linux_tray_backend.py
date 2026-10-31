"""Neutral tray and menu projection preserves the Linux provider contract."""

from unittest.mock import Mock

from docking.platform.backends.base import Rect
from docking.platform.status_notifier.backend import (
    RegisteredItemAddress,
    StatusNotifierBackend,
    StatusTrayState,
    tray_item_from_properties,
)
from docking.platform.status_notifier.dbusmenu import DBusMenuItem, DBusMenuLayout
from docking.platform.tray.linux import LinuxTrayBackend
from docking.platform.tray.state import TrayState


def test_linux_actions_send_the_presented_icon_anchor_without_ui_bus_calls():
    provider = Mock(spec=StatusNotifierBackend)
    adapter = LinuxTrayBackend(backend=provider)
    anchor = Rect(-200, 400, 24, 24)
    adapter.set_geometry({"item": anchor})
    assert not provider.mock_calls
    adapter.activate("item", anchor=anchor, keyboard=True)
    provider.activate.assert_called_once_with("item", x=-200, y=424)
    adapter.context_menu("item", anchor=anchor, keyboard=True)
    provider.context_menu.assert_called_once_with("item", x=-200, y=424)


def test_linux_hover_and_native_focus_capabilities_do_not_contact_sni():
    provider = Mock(spec=StatusNotifierBackend)
    adapter = LinuxTrayBackend(backend=provider)
    adapter.set_focus_handler(lambda _request: None)
    assert not adapter.pointer("item", point=(12, 15), anchor=Rect(10, 10, 24, 24))
    assert not adapter.pointer(None)
    adapter.set_focus_handler(None)
    assert not provider.mock_calls


def test_linux_projection_preserves_metadata_and_prepares_menu_clients_off_ui():
    item = tray_item_from_properties(
        address=RegisteredItemAddress("org.example.Tray", "/Item"),
        properties={
            "Id": "example",
            "Title": "Example",
            "Status": "NeedsAttention",
            "IconName": "normal",
            "AttentionIconName": "attention",
            "IconPixmap": [(1, 1, [255, 10, 20, 30])],
            "ToolTip": ("", [], "Tooltip", "Detail"),
            "Menu": "/Menu",
        },
    )
    provider = Mock(spec=StatusNotifierBackend)
    provider.get_state.return_value = StatusTrayState(
        True, "watcher", (item,), legacy_tray_owner="Other tray"
    )
    client = provider.menu_client.return_value
    client.get_layout.return_value = DBusMenuLayout(
        12,
        DBusMenuItem(
            0,
            "",
            children=(
                DBusMenuItem(7, "Toggle", toggle_type="checkmark", toggle_state=1),
            ),
        ),
    )
    client.event.return_value = True
    adapter = LinuxTrayBackend(backend=provider)
    state = adapter.get_state()
    assert (
        isinstance(state, TrayState)
        and state.mode == "watcher"
        and state.legacy_owner == "Other tray"
    )
    projected = state.items[0]
    assert projected.identifier == item.identifier and projected.item_id == "example"
    assert projected.effective_icon_name == "attention"
    assert projected.icon_pixmap.rgba == bytes((10, 20, 30, 255))
    assert projected.tooltip_title == "Tooltip" and projected.tooltip_text == "Detail"
    assert projected.show_tooltip
    assert not hasattr(projected, "service") and not hasattr(projected, "menu_path")
    provider.menu_client.reset_mock()
    menu = adapter.menu_client(item.identifier)
    provider.menu_client.assert_not_called()  # The GTK path is a cache lookup.
    layout = menu.get_layout()
    assert layout.revision == 12 and layout.root.children[0].toggle_state == 1
    assert menu.event(7)
    client.event.assert_called_once_with(7)
    assert adapter.activate(item.identifier) and adapter.context_menu(item.identifier)
    assert not adapter.double_click(item.identifier)
    adapter.close()
    assert adapter.menu_client(item.identifier) is None
    provider.close.assert_called_once()
