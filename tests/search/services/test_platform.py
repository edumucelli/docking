"""Platform selection preserves Search transports without opening their services."""

import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from docking.search.services.implementations.unix import platform as unix_platform


def callbacks():
    return {
        "preferred_trigger": "CTRL+ALT+space",
        "trigger_description": "Ctrl+Alt+Space",
        "on_activated": MagicMock(),
        "on_status_changed": MagicMock(),
        "schedule_idle": MagicMock(),
        "remove_source": MagicMock(),
    }


@pytest.mark.parametrize("x11", [False, True])
def test_unix_retains_portal_with_only_an_x11_fallback(monkeypatch, x11):
    from docking.search.services.implementations.unix import (
        x11_shortcuts as x11_shortcuts,
    )

    portal, fallback = MagicMock(), MagicMock()
    integration = unix_platform.UnixSearchIntegration()
    assert integration.opaque_action_panel is False
    monkeypatch.setattr(unix_platform, "GlobalShortcutsService", portal)
    monkeypatch.setattr(unix_platform, "is_x11_session", lambda: x11)
    monkeypatch.setattr(unix_platform, "X11GlobalShortcutService", fallback)
    for module in ("windows_focus", "windows_shortcuts"):
        monkeypatch.setitem(sys.modules, "docking.search.services." + module, None)
    arguments = callbacks()
    assert (
        integration.primary(app_id="org.docking.fixture", **arguments)
        is portal.return_value
    )
    portal.assert_called_once_with(
        app_id="org.docking.fixture",
        preferred_trigger=arguments["preferred_trigger"],
        on_activated=arguments["on_activated"],
        on_status_changed=arguments["on_status_changed"],
    )
    assert integration.focus_factory() is None
    native_arguments = {
        "shortcut": arguments["preferred_trigger"],
        "on_activated": MagicMock(),
        "schedule_idle": arguments["schedule_idle"],
    }
    result = integration.fallback(**native_arguments)
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
                "'docking.search.services.implementations.windows.focus',"
                "'docking.search.services.implementations.windows.shortcuts',"
                "'docking.search.services.implementations.unix.x11_shortcuts'));"
                "assert 'gi.repository.Gtk' not in sys.modules"
            ),
        ],
        check=True,
        capture_output=True,
        env=dict(os.environ),
        timeout=30,
    )
