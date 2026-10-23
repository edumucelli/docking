"""Activation from a portal/D-Bus callback has no current GTK event."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.platform.backends.x11.impl import window_tracker as tracker


def test_activation_without_a_gtk_event_uses_a_real_server_timestamp(monkeypatch):
    if not os.environ.get("DISPLAY"):
        pytest.skip("requires a private X11 display")
    monkeypatch.setattr(tracker.Gtk, "get_current_event_time", lambda: 0)
    window = SimpleNamespace(is_minimized=lambda: False, activate=MagicMock())
    tracker.WindowTracker.activate_window(window)
    assert window.activate.call_args.args[0] > 0
