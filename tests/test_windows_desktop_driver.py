"""Native input helpers distinguish a drag from a double click."""

from unittest.mock import Mock

from tools.windows.visual import desktop_driver as mod


def test_drag_emits_one_press_and_release_without_a_trailing_double_click(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)
    desktop = mod.WindowsDesktop.__new__(mod.WindowsDesktop)
    desktop.user = Mock()
    events = []
    desktop._send = lambda values: events.extend(v.data.mouse.flags for v in values)
    desktop.drag((10, 20), (100, 200))
    assert events == [2, 4]
    assert desktop.user.SetCursorPos.call_args.args == (100, 200)
    events.clear()
    desktop.double_click(50, 60)
    assert events == [2, 4, 2, 4]
