"""Native input helpers distinguish a drag from a double click."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from tools.windows.visual import desktop_driver as mod


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_shared_native_json_uses_unicode_regardless_of_windows_ansi_codepage(
    tmp_path, encoding
):
    path = tmp_path / "facts.json"
    path.write_text('{"name": "Résumé 日本語"}', encoding=encoding)

    assert mod.read_shared_json(path) == {"name": "Résumé 日本語"}


def test_shared_report_retries_observer_sharing_violation(tmp_path, monkeypatch):
    path = tmp_path / "result.json"
    path.write_text('{"old": true}', encoding="utf-8")
    replace = Path.replace
    attempts = []

    def transient(source, target):
        attempts.append(target)
        if len(attempts) < 3:
            assert mod.read_shared_json(target) == {"old": True}
            raise PermissionError("observer holds report")
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", transient)
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)
    mod.write_shared_json(path, {"text": "Résumé 日本語"})
    assert len(attempts) == 3
    assert mod.read_shared_json(path) == {"text": "Résumé 日本語"}
    assert not path.with_suffix(".tmp").exists()


def test_shared_report_persistent_denial_propagates(tmp_path, monkeypatch):
    replace = Mock(side_effect=PermissionError("denied"))
    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)
    with pytest.raises(PermissionError, match="denied"):
        mod.write_shared_json(tmp_path / "result.json", {})
    assert replace.call_count == 20


def test_click_guard_runs_after_movement_settles_before_any_button_or_modifier(
    monkeypatch,
):
    desktop = mod.WindowsDesktop.__new__(mod.WindowsDesktop)
    events = []
    desktop.user = Mock()
    desktop.user.SetCursorPos.side_effect = lambda *_: events.append("move") or 1
    monkeypatch.setattr(mod.time, "sleep", lambda _: events.append("settled"))
    desktop._send = Mock()

    def changed():
        assert events == ["move", "settled"]
        raise AssertionError("Target was covered during movement")

    with pytest.raises(AssertionError, match="covered during movement"):
        desktop.click(100, 200, control=True, before_press=changed)
    desktop._send.assert_not_called()


def test_pointer_move_failure_never_injects_click(monkeypatch):
    desktop = mod.WindowsDesktop.__new__(mod.WindowsDesktop)
    desktop.user = Mock()
    desktop.user.SetCursorPos.return_value = 0
    desktop._send = Mock()
    monkeypatch.setattr(mod.time, "sleep", Mock())
    with pytest.raises(AssertionError, match="Pointer did not move"):
        desktop.click(100, 200)
    desktop._send.assert_not_called()


@pytest.mark.parametrize("right,flags", [(False, [2, 4]), (True, [8, 16])])
def test_default_click_keeps_one_button_press_and_release(monkeypatch, right, flags):
    desktop = mod.WindowsDesktop.__new__(mod.WindowsDesktop)
    desktop.user = Mock()
    events = []
    desktop._send = lambda values: events.extend(v.data.mouse.flags for v in values)
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    desktop.click(100, 200, right=right)
    assert events == flags


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


@pytest.fixture
def key_desktop(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)
    desktop = mod.WindowsDesktop.__new__(mod.WindowsDesktop)
    desktop.user = Mock()
    packets = []
    monkeypatch.setattr(
        desktop,
        "_send",
        lambda events: packets.extend(
            (
                event.data.keyboard.key,
                event.data.keyboard.scan,
                event.data.keyboard.flags,
            )
            for event in events
        ),
    )
    return desktop, packets


@pytest.mark.parametrize("extended", [False, True])
@pytest.mark.parametrize("altgr", [False, True])
def test_scan_default_keeps_zero_vk_and_physical_flags(key_desktop, extended, altgr):
    desktop, packets = key_desktop
    desktop.scan(0x1E, extended=extended, altgr=altgr)
    flags = 8 | int(extended)
    expected = [(0, 0x1E, flags), (0, 0x1E, flags | 2)]
    if altgr:
        expected = [(0, 0x38, 9), *expected, (0, 0x38, 11)]
    assert packets == expected
    desktop.user.MapVirtualKeyExW.assert_not_called()


@pytest.mark.parametrize(
    ("scan", "extended", "virtual", "mapped_scan"),
    [(0x1E, False, 0x41, 0x1E), (0x47, True, 0x24, 0xE047)],
)
def test_mapped_vk_uses_target_layout_and_preserves_scan_without_scancode_flag(
    key_desktop, scan, extended, virtual, mapped_scan
):
    desktop, packets = key_desktop
    # A pointer-sized HKL must reach the mapper unchanged.
    layout = 0x12345678042A042A
    desktop.user.MapVirtualKeyExW.return_value = virtual
    desktop.scan(scan, extended=extended, mapped_vk=True, layout=layout)
    desktop.user.MapVirtualKeyExW.assert_called_once_with(mapped_scan, 3, layout)
    assert packets == [
        (virtual, scan, int(extended)),
        (virtual, scan, int(extended) | 2),
    ]


def test_mapped_altgr_keeps_right_modifier_identity_and_release_order(key_desktop):
    desktop, packets = key_desktop
    desktop.user.MapVirtualKeyExW.side_effect = [0x45, 0xA5]
    desktop.scan(0x12, altgr=True, mapped_vk=True, layout=0x04070407)
    assert [call.args for call in desktop.user.MapVirtualKeyExW.call_args_list] == [
        (0x12, 3, 0x04070407),
        (0xE038, 3, 0x04070407),
    ]
    assert packets == [
        (0xA5, 0x38, 1),
        (0x45, 0x12, 0),
        (0x45, 0x12, 2),
        (0xA5, 0x38, 3),
    ]


@pytest.mark.parametrize("layout", [None, 0])
def test_mapped_vk_requires_known_target_layout_without_sending_keys(
    key_desktop, layout
):
    desktop, packets = key_desktop
    with pytest.raises(ValueError, match="target layout"):
        desktop.scan(0x1E, mapped_vk=True, layout=layout)
    assert not packets
    desktop.user.MapVirtualKeyExW.assert_not_called()


@pytest.mark.parametrize("invalid", [0, 255, 0x10000])
@pytest.mark.parametrize("altgr", [False, True])
def test_unmapped_key_or_modifier_aborts_before_any_injection(
    key_desktop, altgr, invalid
):
    desktop, packets = key_desktop
    desktop.user.MapVirtualKeyExW.side_effect = [0x41, invalid] if altgr else [invalid]
    with pytest.raises(ValueError, match="did not map"):
        desktop.scan(0x1E, altgr=altgr, mapped_vk=True, layout=0x042A042A)
    assert not packets
