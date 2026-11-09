"""Typed companion messages retain the version-one framing and ignore policy."""

import io
import json

import pytest

from docking.applets.webapp.implementations.windows.companion import protocol as p
from docking.applets.webapp.state import BrowserPhase
from tests.applets.test_windows_webapp_companion import PROFILE
from tools.windows.visual import webengine_protocol as fixture


@pytest.mark.parametrize(
    "message",
    [p.Show(), p.Hide(), p.Reload(), p.ClearSession(7), p.Stop()],
)
def test_command_wire_round_trip(message):
    assert p.read_command(io.BytesIO(p.encode(message))) == message


@pytest.mark.parametrize(
    "message",
    [
        p.Started(),
        p.VisibilityChanged(True),
        p.VisibilityChanged(False),
        *[p.PhaseChanged(phase) for phase in BrowserPhase],
        p.BridgeMessage('{"badge":3}'),
        p.TitleChanged("résumé 🌱"),
        p.Notification(),
        p.AttentionCleared(),
        p.OpenExternal("https://example.test/"),
        p.RestartRequested(1),
        p.SessionCleared(2**31 - 1, True),
        p.SessionCleared(7, False),
    ],
)
def test_event_wire_round_trip(message):
    assert p.read_event(io.BytesIO(p.encode(message))) == message


@pytest.mark.parametrize("identifier", [True, False, 0, -1, 2**31, 1.0, "1", None])
def test_request_ids_are_positive_bounded_integers(identifier):
    assert p.decode_command({"op": "clear", "id": identifier}) is None
    assert p.decode_event({"event": "restart", "id": identifier}) is None
    assert p.decode_event({"event": "cleared", "id": identifier, "ok": True}) is None


@pytest.mark.parametrize(
    "packet",
    [
        {"event": "future-event"},
        {"event": "visible", "value": 1},
        {"event": "visible", "value": "true"},
        {"event": "phase", "value": "future-phase"},
        {"event": "phase", "value": 1},
        {"event": "bridge", "value": None},
        {"event": "title", "value": 3},
        {"event": "title", "value": "x" * 4097},
        {"event": "external", "uri": 3},
        {"event": "external", "uri": "x" * 32769},
        {"event": "cleared", "id": 1, "ok": 1},
        {"event": "cleared", "id": 1, "ok": "true"},
    ],
)
def test_invalid_event_does_not_become_eof(packet):
    assert p.decode_event(packet) is None
    stream = io.BytesIO(
        json.dumps({**packet, "version": 1}).encode() + b"\n" + p.encode(p.Started())
    )
    assert p.read_event(stream) == p.Started()
    assert p.read_event(stream) is None


def test_invalid_and_unknown_commands_do_not_become_eof():
    stream = io.BytesIO(
        b'{"version":1,"op":"future-command"}\n'
        b'{"version":1,"op":"clear","id":true}\n' + p.encode(p.Reload())
    )
    assert p.read_command(stream) == p.Reload()
    assert p.read_command(stream) is None


def test_frame_limit_includes_version_and_newline():
    overhead = len(p.encode(p.BridgeMessage("")))
    message = p.BridgeMessage("x" * (p.MAX_PACKET_BYTES - overhead))
    data = p.encode(message)
    assert len(data) == p.MAX_PACKET_BYTES
    assert p.read_event(io.BytesIO(data)) == message
    with pytest.raises(ValueError, match="too large"):
        p.encode(p.BridgeMessage(message.raw + "x"))


@pytest.mark.parametrize(
    "message",
    [p.ClearSession(True), p.RestartRequested(0), p.TitleChanged("x" * 4097)],
)
def test_outbound_records_also_validate_fields(message):
    with pytest.raises(ValueError, match="fields"):
        p.encode(message)


@pytest.mark.parametrize("uri", [None, 1, "https://example.test/" + "x" * 32768])
def test_external_uri_producers_drop_invalid_fields_before_enqueueing(uri):
    assert p.OpenExternal.for_uri(uri) is None


def test_external_uri_at_field_limit_can_still_be_written():
    uri = "https://example.test/".ljust(32768, "x")
    event = p.OpenExternal.for_uri(uri)
    assert event == p.OpenExternal(uri)
    assert p.read_event(io.BytesIO(p.encode(event))) == event


@pytest.mark.parametrize(
    "change",
    [
        {"directory": "relative"},
        {"directory": ""},
        {"directory": "bad\0directory"},
        {"profile": {}},
        {"profile": {"name": "WhatsApp"}},
        {"op": "show"},
    ],
)
def test_configuration_remains_strict(tmp_path, change):
    packet = json.loads(p.encode(p.Configuration(PROFILE, tmp_path)))
    packet.update(change)
    with pytest.raises(ValueError):
        p.read_configuration(io.BytesIO(json.dumps(packet).encode() + b"\n"))


def test_native_probe_codec_is_separate_from_production_messages():
    command = fixture.FixtureMessage({"op": "probe", "id": 1, "source": "true"})
    event = fixture.fixture_event("probe", id=1, result=True)
    assert p.read_command(io.BytesIO(fixture.encode(command))) is None
    assert p.read_event(io.BytesIO(fixture.encode(event))) is None
    assert fixture.read_command(io.BytesIO(fixture.encode(command))) == command
    assert fixture.read_event(io.BytesIO(fixture.encode(event))) == event
    assert fixture.read_command(io.BytesIO(fixture.encode(p.Reload()))) == p.Reload()
    assert fixture.read_event(io.BytesIO(fixture.encode(p.Started()))) == p.Started()
