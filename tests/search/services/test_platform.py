"""Platform selection preserves Search transports without opening their services."""

import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from docking.search.services import global_shortcuts, platform


def callbacks():
    return {
        "preferred_trigger": "CTRL+ALT+space",
        "trigger_description": "Ctrl+Alt+Space",
        "on_activated": MagicMock(),
        "on_status_changed": MagicMock(),
        "schedule_idle": MagicMock(),
        "remove_source": MagicMock(),
    }


def test_windows_uses_native_services_without_importing_x11(monkeypatch):
    from docking.search.services import windows_focus, windows_shortcuts

    native, focus = MagicMock(), MagicMock()
    monkeypatch.setattr(platform, "is_windows", lambda: True)
    monkeypatch.setattr(windows_shortcuts, "WindowsGlobalShortcutsService", native)
    monkeypatch.setattr(windows_focus, "WindowsSearchFocus", focus)
    monkeypatch.setitem(sys.modules, "docking.search.services.x11_shortcuts", None)
    portal = MagicMock(side_effect=AssertionError("Unexpected portal construction"))
    monkeypatch.setattr(global_shortcuts, "GlobalShortcutsService", portal)
    arguments = callbacks()
    assert platform.create_shortcut_service(**arguments) is native.return_value
    native.assert_called_once_with(**arguments)
    assert platform.create_focus_factory() is focus
    focus.assert_not_called()
    assert (
        platform.create_shortcut_fallback(
            shortcut=arguments["preferred_trigger"],
            on_activated=MagicMock(),
            schedule_idle=arguments["schedule_idle"],
        )
        is None
    )
    portal.assert_not_called()


@pytest.mark.parametrize("x11", [False, True])
def test_unix_retains_portal_with_only_an_x11_fallback(monkeypatch, x11):
    from docking.search.services import x11_shortcuts

    portal, fallback = MagicMock(), MagicMock()
    monkeypatch.setattr(platform, "is_windows", lambda: False)
    monkeypatch.setattr(global_shortcuts, "GlobalShortcutsService", portal)
    monkeypatch.setattr(x11_shortcuts, "is_x11_session", lambda: x11)
    monkeypatch.setattr(x11_shortcuts, "X11GlobalShortcutService", fallback)
    for module in ("windows_focus", "windows_shortcuts"):
        monkeypatch.setitem(sys.modules, "docking.search.services." + module, None)
    monkeypatch.setattr(platform, "application_id", lambda: "org.docking.fixture")
    arguments = callbacks()
    assert platform.create_shortcut_service(**arguments) is portal.return_value
    portal.assert_called_once_with(
        app_id="org.docking.fixture",
        preferred_trigger=arguments["preferred_trigger"],
        on_activated=arguments["on_activated"],
        on_status_changed=arguments["on_status_changed"],
    )
    assert platform.create_focus_factory() is None
    native_arguments = {
        "shortcut": arguments["preferred_trigger"],
        "on_activated": MagicMock(),
        "schedule_idle": arguments["schedule_idle"],
    }
    result = platform.create_shortcut_fallback(**native_arguments)
    if x11:
        assert result is fallback.return_value
        fallback.assert_called_once_with(**native_arguments)
    else:
        assert result is None
        fallback.assert_not_called()


def test_loading_search_service_factory_does_not_import_native_backends():
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys;import docking.search.services.platform;"
                "assert not any(name in sys.modules for name in ("
                "'docking.search.services.windows_focus',"
                "'docking.search.services.windows_shortcuts',"
                "'docking.search.services.x11_shortcuts'));"
                "assert 'gi.repository.Gtk' not in sys.modules"
            ),
        ],
        check=True,
        capture_output=True,
        env=dict(os.environ),
        timeout=30,
    )
