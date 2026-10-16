"""The keyboard entry point must contact the running process, not launch another."""

from unittest.mock import MagicMock

import pytest
from gi.repository import Gio, GLib

from docking import launcher


def test_focus_cli_does_not_initialize_display(monkeypatch):
    monkeypatch.setattr(launcher.sys, "argv", ["docking", "--focus"])
    prepare = MagicMock()
    monkeypatch.setattr(launcher, "prepare_display", prepare)
    monkeypatch.setattr(launcher, "focus_running_dock", lambda: True)
    with pytest.raises(SystemExit) as result:
        launcher.main()
    assert result.value.code == 0
    prepare.assert_not_called()


def test_focus_request_has_timeout_and_does_not_autostart(monkeypatch):
    connection = MagicMock()
    connection.call_sync.return_value = GLib.Variant("(b)", (True,))
    monkeypatch.setattr(Gio, "bus_get_sync", lambda *args: connection)
    assert launcher.focus_running_dock()
    args = connection.call_sync.call_args.args
    assert args[3] == "Focus"
    assert args[6] == Gio.DBusCallFlags.NO_AUTO_START
    assert args[7] == 3000


def test_focus_failure_returns_failure_without_starting_dock(monkeypatch, capsys):
    def fail(*args):
        raise GLib.Error("No running dock")

    monkeypatch.setattr(Gio, "bus_get_sync", fail)
    assert not launcher.focus_running_dock()
    assert "No running dock" in capsys.readouterr().err
