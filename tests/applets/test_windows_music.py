"""Native Music targets are explicit and player volume never becomes master volume."""

import ctypes
import threading
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.music import windows as backend_module
from docking.applets.music import windows_audio as audio_module
from docking.applets.music import windows_media as media_module
from docking.applets.music.state import MusicState


def selected(**kwargs):
    return MusicState(
        available=True,
        player_id="one",
        player_application_id="Example.Player",
        **kwargs,
    )


def test_stopped_backend_cannot_construct_or_call_native_apis(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(backend_module, "WindowsMediaAPI", factory)
    backend = backend_module.WindowsMusicBackend()
    backend.stop()
    assert not backend.poll().available
    assert not backend.play_pause(selected())
    assert not backend.set_volume(selected(), 50)
    backend.close()
    factory.assert_not_called()


def test_unknown_output_attribution_preserves_metadata_without_a_fake_volume():
    backend = backend_module.WindowsMusicBackend()
    backend._api = api = Mock()
    backend._audio = audio = Mock()
    api.poll.return_value = selected(title="Track", can_play_pause=True)
    audio.read.side_effect = OSError("Endpoint removed")
    result = backend.poll()
    assert result.available and result.title == "Track" and not result.can_set_volume
    assert not backend.set_volume(result, 50)
    audio.set_volume.assert_not_called()


def test_volume_control_revalidates_the_media_session_before_writing_audio():
    backend = backend_module.WindowsMusicBackend()
    backend._api, backend._audio = Mock(), Mock()
    state = selected(can_set_volume=True, volume_id="group")
    backend._api.contains.return_value = False
    assert not backend.set_volume(state, 50)
    backend._audio.set_volume.assert_not_called()
    backend._api.contains.return_value = True
    backend._audio.set_volume.return_value = True
    assert backend.set_volume(state, 50)
    backend._audio.set_volume.assert_called_once_with("Example.Player", "group", 50)


def test_transport_cannot_fall_back_to_a_different_current_session(monkeypatch):
    api = media_module.WindowsMediaAPI.__new__(media_module.WindowsMediaAPI)
    live = media_module.MediaSession("two", ctypes.c_void_p(20), "Example.Other")
    api._refresh_sessions = lambda: ([live], "two")
    call = Mock(side_effect=AssertionError("Must not invoke another player's control"))
    monkeypatch.setattr(media_module, "method", call)
    assert not api.control(selected(), "next")
    assert not api.contains(selected())
    call.assert_not_called()


def test_session_source_replacement_invalidates_the_old_snapshot(monkeypatch):
    api = media_module.WindowsMediaAPI.__new__(media_module.WindowsMediaAPI)
    live = media_module.MediaSession("one", ctypes.c_void_p(20), "Example.Replaced")
    api._refresh_sessions = lambda: ([live], "one")
    monkeypatch.setattr(
        media_module, "method", Mock(side_effect=AssertionError("Stale source"))
    )
    assert not api.control(selected(), "play_pause")
    assert not api.contains(selected())


def test_selection_prefers_a_playing_session_over_the_systems_paused_current(
    monkeypatch,
):
    api = media_module.WindowsMediaAPI.__new__(media_module.WindowsMediaAPI)
    one = media_module.MediaSession("one", ctypes.c_void_p(10), "Example.Player")
    two = media_module.MediaSession("two", ctypes.c_void_p(20), "Example.Other")
    api._refresh_sessions = lambda: ([one, two], "one")
    api._playback = lambda session: (
        "Playing" if session == two else "Paused",
        True,
        True,
        True,
        20,
    )
    api._runtime = SimpleNamespace(wait=Mock())
    api._cancelled = threading.Event()
    api._last_token = "one"
    monkeypatch.setattr(media_module, "object_result", lambda *_args: ctypes.c_void_p())
    result = api.poll()
    assert result.player_id == "two" and result.playback_status == "Playing"


@contextmanager
def rows_scope(rows):
    yield rows


def audio_backend(rows, identities):
    api = audio_module.WindowsPlayerAudio.__new__(audio_module.WindowsPlayerAudio)
    api._scan = lambda: rows_scope(rows)
    api._process_identity = identities.__getitem__
    return api


def row(key, pid, level):
    return audio_module._Session(key, pid, level, ctypes.c_void_p(pid))


def test_player_volume_groups_only_matching_app_streams_and_exposes_the_highest():
    rows = (row("a", 10, 0.3), row("b", 11, 0.6), row("unrelated", 12, 0.9))
    api = audio_backend(
        rows,
        {
            10: ("Example.Player", "one.exe"),
            11: ("Example.Player", "two.exe"),
            12: ("Other", "other.exe"),
        },
    )
    result = api.read("Example.Player")
    assert result.percent == 60
    assert result.identifier == audio_module._identity(rows[:2])


def test_same_executable_name_at_different_paths_is_not_enough_attribution():
    rows = (row("a", 10, 0.3), row("b", 11, 0.6))
    api = audio_backend(
        rows, {10: ("", r"C:\One\player.exe"), 11: ("", r"C:\Two\player.exe")}
    )
    assert not api._match("player.exe", rows)


def test_changed_audio_group_is_rejected_before_any_volume_write(monkeypatch):
    rows = (row("new-instance", 10, 0.5),)
    api = audio_backend(rows, {10: ("Example.Player", "player.exe")})
    set_level = Mock()
    monkeypatch.setattr(audio_module, "method", set_level)
    old = audio_module._identity((replace(rows[0], key="old-instance"),))
    assert not api.set_volume("Example.Player", old, 75)
    set_level.assert_not_called()


def test_partial_volume_failure_rolls_back_only_the_selected_group(monkeypatch):
    rows = (row("first", 10, 0.3), row("second", 11, 0.6))
    api = audio_backend(
        rows, {10: ("Example.Player", "one.exe"), 11: ("Example.Player", "two.exe")}
    )
    levels, writes = {10: 0.3, 11: 0.6}, []

    def method(pointer, _index, *_types):
        def set_level(_pointer, level, _context):
            writes.append((pointer.value, level))
            if pointer.value == 11:
                raise OSError("Session disconnected")
            levels[pointer.value] = level
            return 0

        return set_level

    monkeypatch.setattr(audio_module, "method", method)
    monkeypatch.setattr(
        audio_module,
        "value",
        lambda pointer, _index, _kind: SimpleNamespace(value=levels[pointer.value]),
    )
    with pytest.raises(OSError, match="disconnected"):
        api.set_volume("Example.Player", audio_module._identity(rows), 75)
    assert levels == {10: 0.3, 11: 0.6}
    assert writes == [(10, 0.75), (11, 0.75), (10, 0.3)]


@pytest.mark.parametrize("status,transient", [(0x80070015, True), (0x80070005, False)])
def test_connecting_metadata_keeps_transport_available_but_access_errors_propagate(
    monkeypatch, status, transient
):
    api = media_module.WindowsMediaAPI.__new__(media_module.WindowsMediaAPI)
    session = media_module.MediaSession("one", ctypes.c_void_p(10), "Example.Player")
    api._refresh_sessions = lambda: ([session], "one")
    api._playback = lambda _session: ("Playing", True, True, True, 20)
    api._last_token = ""
    api._cancelled = threading.Event()
    api._runtime = SimpleNamespace(
        wait=Mock(side_effect=OSError(status, "Metadata unavailable"))
    )
    monkeypatch.setattr(
        media_module, "object_result", lambda *_args: ctypes.c_void_p(100)
    )
    if transient:
        result = api.poll()
        assert result.available and result.can_play_pause
        assert result.playback_status == "Playing" and result.title == ""
    else:
        with pytest.raises(OSError):
            api.poll()
