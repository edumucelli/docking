"""Independent tray tools must preserve SDK shapes, identities and ownership."""

import ctypes as c
import hashlib
import io
import json
import struct
import subprocess
import sys
import zipfile
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.windows.visual.tray_acceptance import (
    TrayRuntime,
    runtime_origins,
    select_runtime,
    verify_installed_wheel,
)
from tools.windows.visual.tray_fixture import decode_callback, rich_popup_position
from tools.windows.visual.tray_interactions import (
    connect_hover_trace,
    disconnect_hover_trace,
    keyboard_focus_ready,
    visible_control_point,
)
from tools.windows.visual.tray_provider import (
    NativeProvider,
    ProviderCase,
    build_provider,
    provider_cases,
    validate_protocol,
)
from tools.windows.visual.tray_provider_smoke import OwnedWireTrace
from tools.windows.visual.tray_smoke import physical_dock_click


class HoverTarget:
    def __init__(self):
        self.handlers = {}
        self.disconnected = []

    def connect_after(self, signal, callback):
        handler = len(self.handlers) + 1
        self.handlers[handler] = signal, callback
        return handler

    def handler_is_connected(self, handler):
        return handler in self.handlers

    def disconnect(self, handler):
        self.disconnected.append(handler)
        del self.handlers[handler]


def test_hover_diagnostic_trace_is_bounded_and_never_consumes_real_events():
    target, events = HoverTarget(), []
    disconnect = connect_hover_trace(target, events)
    assert [signal for signal, _callback in target.handlers.values()] == [
        "enter-notify-event",
        "motion-notify-event",
        "leave-notify-event",
    ]
    for index in range(64):
        _signal, callback = target.handlers[index % 3 + 1]
        event = SimpleNamespace(x=index, y=-index, time=index, send_event=0)
        assert callback(target, event) is False
    assert len(events) == 32 and events[-1]["time"] == 31
    assert events[0] == {
        "signal": "enter-notify-event",
        "x": 0,
        "y": 0,
        "time": 0,
        "send_event": False,
    }
    disconnect()
    assert not target.handlers and target.disconnected == [1, 2, 3]


def test_hover_trace_cleanup_handles_destroyed_target_without_retargeting_new_row():
    captured, replacement, events = HoverTarget(), HoverTarget(), []
    disconnect = connect_hover_trace(captured, events)
    replacement_disconnect = connect_hover_trace(replacement, [])
    del captured.handlers[2]  # GTK may drop a signal while destroying old content.
    try:
        raise AssertionError("original hover acceptance failure")
    except AssertionError as error:
        assert str(error) == "original hover acceptance failure"
    finally:
        disconnect()
    assert not captured.handlers and len(replacement.handlers) == 3
    assert not events
    replacement_disconnect()


@pytest.mark.parametrize(
    "original", (AssertionError("motion missing"), KeyboardInterrupt())
)
def test_hover_diagnostic_cleanup_cannot_mask_acceptance_failure_or_cancellation(
    original,
):
    attempt = {}

    def disconnect():
        raise TimeoutError("GTK observation deadline")

    with pytest.raises(type(original)) as failed:
        try:
            raise original
        finally:
            disconnect_hover_trace(disconnect, attempt)
    assert failed.value is original and attempt["disconnect_error"] == "TimeoutError"


def test_hover_diagnostic_cleanup_failure_after_success_still_fails():
    attempt = {}

    def disconnect():
        raise TimeoutError("GTK observation deadline")

    with pytest.raises(TimeoutError):
        disconnect_hover_trace(disconnect, attempt)
    assert attempt["disconnect_error"] == "TimeoutError"


@pytest.mark.parametrize("bits", (32, 64))
@pytest.mark.parametrize("encoding", ("unicode", "ansi"))
def test_abi_cases_match_sdk_field_offsets_and_native_alignment(bits, encoding):
    pointer = c.c_uint64 if bits == 64 else c.c_uint32
    char = c.c_uint16 if encoding == "unicode" else c.c_uint8

    class Data(c.Structure):
        _fields_ = [
            ("size", c.c_uint32),
            ("window", pointer),
            ("uid", c.c_uint32),
            ("flags", c.c_uint32),
            ("message", c.c_uint32),
            ("icon", pointer),
            ("tip", char * 128),
            ("state", c.c_uint32),
            ("mask", c.c_uint32),
            ("info", char * 256),
            ("version", c.c_uint32),
            ("title", char * 64),
            ("info_flags", c.c_uint32),
            ("guid", c.c_uint32 * 4),
            ("balloon", pointer),
        ]

    expected = {
        "v1": Data.tip.offset + 64 * c.sizeof(char),
        "v2": Data.guid.offset,
        "v3": Data.balloon.offset,
        "full": c.sizeof(Data),
    }
    cases = [
        case
        for case in provider_cases()
        if case.bits == bits and case.encoding == encoding
    ]
    assert len(cases) == 10
    assert all(case.native_size == expected[case.size] for case in cases)


def test_grid_retains_each_architecture_encoding_identity_and_protocol():
    cases = provider_cases()
    assert len(cases) == len({case.name for case in cases}) == 40
    assert {
        (case.bits, case.encoding, case.identity, case.version)
        for case in cases
        if case.size == "full"
    } == {
        (bits, encoding, identity, version)
        for bits in (32, 64)
        for encoding in ("unicode", "ansi")
        for identity in ("guid", "hwnd")
        for version in (0, 3, 4)
    }


@pytest.mark.parametrize(
    "fields",
    [
        {"bits": 16},
        {"encoding": "utf8"},
        {"version": 1},
        {"size": "v1"},
        {"identity": "guid", "size": "v2"},
    ],
)
def test_invalid_native_abi_combinations_are_rejected(fields):
    with pytest.raises(ValueError):
        ProviderCase(**fields)


@pytest.mark.parametrize("version", (0, 3))
def test_fixture_retains_legacy_uid_for_unpacked_callbacks(version):
    assert decode_callback(500, 0x205, {"legacy": version, "modern": 4}) == (
        "legacy",
        500,
        0x205,
        version,
    )


def test_fixture_decodes_v4_keyboard_and_signed_anchor_without_guessing_legacy():
    versions = {"legacy": 0, "modern": 4}
    assert decode_callback(0xFFFF8000, (501 << 16) | 0x401, versions) == (
        "modern",
        501,
        0x401,
        4,
    )
    # An unknown packed UID with x=500 must not masquerade as legacy UID 500.
    assert decode_callback(500, (502 << 16) | 0x401, versions)[0] is None
    assert decode_callback(501, 0x401, versions)[0] is None


@pytest.mark.parametrize(
    "icon,work",
    [
        ((100, 950, 140, 990), (0, 0, 1920, 1080)),
        ((100, 0, 140, 40), (0, 0, 1920, 1080)),
        ((-1900, -200, -1860, -160), (-1920, -1080, 0, 0)),
    ],
)
def test_rich_hover_popup_stays_on_monitor_without_covering_its_icon(icon, work):
    x, y = rich_popup_position(icon, work)
    assert work[0] <= x < x + 230 <= work[2]
    assert work[1] <= y < y + 72 <= work[3]
    assert x + 230 <= icon[0] or x >= icon[2] or y + 72 <= icon[1] or y >= icon[3]


def test_hover_popup_prefers_above_icon_top_and_falls_below_at_top_edge():
    assert rich_popup_position((100, 950, 140, 990), (0, 0, 1920, 1080)) == (100, 874)
    assert rich_popup_position((100, 0, 140, 40), (0, 0, 1920, 1080)) == (100, 44)


def test_command_revision_follows_external_driver_without_overwriting(
    tmp_path, monkeypatch
):
    client = NativeProvider(tmp_path / "provider.exe", tmp_path, {})
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"stage": "ready", "command_revision": 7}))
    (tmp_path / "command-000007.json").write_text("immutable external command")

    def acknowledge(predicate, _message):
        value = {"command_revision": 8}
        assert predicate(value)
        return value

    monkeypatch.setattr(client, "wait", acknowledge)
    client.command("snapshot")
    assert json.loads((tmp_path / "command-000008.json").read_text()) == {
        "action": "snapshot",
    }
    assert (
        tmp_path / "command-000007.json"
    ).read_text() == "immutable external command"


def test_pending_external_command_collision_does_not_overwrite(tmp_path):
    client = NativeProvider(tmp_path / "provider.exe", tmp_path, {})
    (tmp_path / "state.json").write_text(json.dumps({"command_revision": 2}))
    pending = tmp_path / "command-000003.json"
    pending.write_text("unacknowledged external command")
    with pytest.raises(FileExistsError):
        client.command("snapshot")
    assert pending.read_text() == "unacknowledged external command"


def test_forced_provider_cleanup_is_never_reported_as_success(tmp_path):
    class Process:
        pid = 123
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, *, timeout):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("owned-provider", timeout)

        def terminate(self):
            self.returncode = -15

    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "stage": "ready",
                "registered": True,
            }
        )
    )
    client = NativeProvider(tmp_path / "provider.exe", tmp_path, {})
    client.process = Process()
    client._log = io.StringIO()
    with pytest.raises(RuntimeError, match="cleanup did not verify"):
        client.close()
    assert client.forced_cleanup and client.process.returncode == -15
    assert client._log.closed and (tmp_path / "stop").exists()
    lifecycle = json.loads((tmp_path / "provider-lifecycle.json").read_text())
    assert lifecycle["forced_cleanup"] and lifecycle["cleanup_stage"] == "failed"
    assert lifecycle["returncode"] == -15 and lifecycle["log_closed"]


def test_provider_exit_before_state_keeps_original_startup_error_and_cleanup_failure(
    tmp_path, monkeypatch
):
    from tools.windows.visual import tray_provider

    process = SimpleNamespace(pid=321, returncode=7, poll=lambda: 7)
    monkeypatch.setattr(
        tray_provider.subprocess, "Popen", lambda *_args, **_kw: process
    )
    client = NativeProvider(tmp_path / "provider.exe", tmp_path / "A", {})
    with pytest.raises(RuntimeError, match="exited before become ready"):
        client.__enter__()
    lifecycle = json.loads((client.directory / "provider-lifecycle.json").read_text())
    assert lifecycle["pid"] == 321 and lifecycle["returncode"] == 7
    assert (
        lifecycle["startup_stage"] == "failed"
        and lifecycle["startup_failed_at"] == "readiness"
    )
    assert lifecycle["startup_error"] == {"type": "RuntimeError"}
    assert lifecycle["cleanup_stage"] == "failed" and lifecycle["cleanup_error"] == {
        "type": "RuntimeError"
    }
    assert not lifecycle["state_present"] and lifecycle["state_stage"] is None
    assert lifecycle["log_closed"] and client._log.closed


def test_provider_spawn_failure_journal_keeps_numeric_codes_without_private_paths(
    tmp_path, monkeypatch
):
    from tools.windows.visual import tray_provider

    error = PermissionError(13, "private executable path")
    error.winerror = 5

    def denied(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(tray_provider.subprocess, "Popen", denied)
    client = NativeProvider(tmp_path / "provider.exe", tmp_path / "A", {})
    with pytest.raises(PermissionError) as failed:
        client.__enter__()
    assert failed.value is error and client._log.closed
    text = (client.directory / "provider-lifecycle.json").read_text()
    lifecycle = json.loads(text)
    assert lifecycle["pid"] is None and lifecycle["returncode"] is None
    assert lifecycle["startup_failed_at"] == "spawn"
    assert lifecycle["startup_error"] == {
        "type": "PermissionError",
        "errno": 13,
        "winerror": 5,
    }
    assert lifecycle["cleanup_stage"] == "no-owned-process" and "private" not in text


def test_readiness_timeout_is_not_masked_by_absent_state_cleanup(tmp_path, monkeypatch):
    from tools.windows.visual import tray_provider

    process = SimpleNamespace(pid=321, returncode=None)
    timeouts = []

    def wait(*, timeout):
        timeouts.append(timeout)
        process.returncode = 0

    process.poll, process.wait = lambda: process.returncode, wait
    monkeypatch.setattr(
        tray_provider.subprocess, "Popen", lambda *_args, **_kw: process
    )
    error = TimeoutError("readiness deadline")
    client = NativeProvider(tmp_path / "provider.exe", tmp_path / "A", {})

    def not_ready(*_args):
        raise error

    monkeypatch.setattr(client, "wait", not_ready)
    with pytest.raises(TimeoutError) as failed:
        client.__enter__()
    assert failed.value is error and timeouts == [10]
    lifecycle = json.loads((client.directory / "provider-lifecycle.json").read_text())
    assert (
        lifecycle["readiness_timeout_s"] == 15 and lifecycle["terminate_timeout_s"] == 5
    )
    assert lifecycle["startup_error"]["type"] == "TimeoutError"
    assert lifecycle["cleanup_error"]["type"] == "RuntimeError"
    assert client._log.closed


@pytest.mark.parametrize("registered", (False, True))
def test_provider_normal_exit_requires_closed_unregistered_state_and_closes_log(
    tmp_path, monkeypatch, registered
):
    from tools.windows.visual import tray_provider

    directory = tmp_path / "A"
    configuration = ProviderCase().configuration("A", "owned")
    process = SimpleNamespace(pid=321, returncode=None)
    timeouts = []

    def wait(*, timeout):
        timeouts.append(timeout)
        process.returncode = 0
        (directory / "state.json").write_text(
            json.dumps({"stage": "closed", "registered": registered})
        )

    process.poll, process.wait = lambda: process.returncode, wait

    def spawn(*_args, **_kwargs):
        (directory / "state.json").write_text(
            json.dumps({**configuration, "pid": 321, "stage": "ready"})
        )
        return process

    monkeypatch.setattr(tray_provider.subprocess, "Popen", spawn)
    client = NativeProvider(tmp_path / "provider.exe", directory, configuration)
    expected = (
        pytest.raises(RuntimeError, match="cleanup did not verify")
        if registered
        else nullcontext()
    )
    with expected, client:
        lifecycle = json.loads((directory / "provider-lifecycle.json").read_text())
        assert lifecycle["startup_stage"] == "ready" and lifecycle["pid"] == 321
        assert not lifecycle["log_closed"]
    lifecycle = json.loads((directory / "provider-lifecycle.json").read_text())
    assert lifecycle["cleanup_stage"] == ("failed" if registered else "verified")
    assert lifecycle["state_stage"] == "closed" and lifecycle["state_present"]
    assert lifecycle["returncode"] == 0 and lifecycle["log_closed"]
    assert lifecycle["elapsed_ms"] >= 0 and lifecycle["cleanup_elapsed_ms"] >= 0
    assert timeouts == [10] and not lifecycle["forced_cleanup"]


def test_provider_configuration_failure_retains_partial_lifecycle_without_spawning(
    tmp_path, monkeypatch
):
    from tools.windows.visual import tray_provider

    original_write = tray_provider.write_shared_json
    error = OSError(28, "private configuration path")

    def write(path, value):
        if path.name == "configuration.json":
            raise error
        original_write(path, value)

    monkeypatch.setattr(tray_provider, "write_shared_json", write)
    spawn = Mock()
    monkeypatch.setattr(tray_provider.subprocess, "Popen", spawn)
    client = NativeProvider(tmp_path / "provider.exe", tmp_path / "A", {})
    with pytest.raises(OSError) as failed:
        client.__enter__()
    assert failed.value is error
    spawn.assert_not_called()
    text = (client.directory / "provider-lifecycle.json").read_text()
    lifecycle = json.loads(text)
    assert lifecycle["startup_failed_at"] == "configuration"
    assert lifecycle["startup_error"]["errno"] == 28
    assert lifecycle["cleanup_stage"] == "no-owned-process" and lifecycle["log_closed"]
    assert "private" not in text


def protocol_evidence(case):
    first = {
        "bits": case.bits,
        "native_size": case.native_size,
        "pid": 100,
        "window": 200,
        "uid": 731,
        "callback_version": case.version,
        "tip": "Owned first provider",
        "calls": [
            {"operation": 0, "accepted": True},
            {"operation": 4, "accepted": True},
        ],
    }
    item = {"owner": 100, "window": 200, "uid": 731, "tip": first["tip"]}
    result = {"first-add": {"providers": {"A": first}, "native_wire_count": 0}}
    wire = []
    identity_flag = 0x20 if case.identity == "guid" else 0
    for name, flags in (
        ("showtip-only-on", identity_flag | 0x80),
        ("showtip-only-off", identity_flag),
    ):
        value = deepcopy(first)
        value["calls"].append({"operation": 1, "flags": flags, "accepted": True})
        delivered = flags if case.size == "full" else flags & ~0x80
        packet = notification_packet(flags=delivered, version=0)
        wire.append(
            {
                "operation": 1,
                "flags": delivered,
                "version": 0,
                "window": 200,
                "uid": 731,
                "native_cb_size": case.native_size,
                "wire_cb_size": 956,
                "raw_flags": delivered,
                "raw_prefix_hex": packet[:32].hex(),
                "payload_length": len(packet),
                "forwarded": True,
                "accepted": True,
                "projection": {
                    "version": case.version,
                    "show_tip": bool(delivered & 0x80),
                },
            }
        )
        projected = {**item, "show_tooltip": case.version < 4 or bool(delivered & 0x80)}
        result[name] = {
            "providers": {"A": value},
            "projection": {"guid:owned": projected},
            "native_wire_count": len(wire),
        }
    result["second-add"] = {
        "providers": {
            "B": {"calls": [{"operation": 0, "accepted": case.identity == "hwnd"}]}
        },
        "projection": {"guid:owned": deepcopy(item)},
    }
    item["tip"] = "Owned second-provider modification"
    result["second-provider-modify"] = {
        "providers": {
            "B": {"tip": item["tip"], "calls": [{"operation": 1, "accepted": True}]}
        },
        "projection": {"guid:owned": item},
    }
    return result, wire


@pytest.fixture
def observations():
    return protocol_evidence(ProviderCase())


def test_rejected_guid_add_keeps_first_owners_callback_identity(observations):
    observations, wire = observations
    validate_protocol(ProviderCase(), observations, native_wire=wire)
    observations["second-add"]["projection"]["guid:owned"]["owner"] = 999
    with pytest.raises(AssertionError):
        validate_protocol(ProviderCase(), observations, native_wire=wire)


def test_showtip_only_acceptance_cannot_silently_change_text(observations):
    observations, wire = observations
    observations["showtip-only-on"]["providers"]["A"]["tip"] = "changed without NIF_TIP"
    with pytest.raises(AssertionError):
        validate_protocol(ProviderCase(), observations, native_wire=wire)


def test_flag_only_policy_must_update_live_projection(observations):
    observations, wire = observations
    observations["showtip-only-on"]["projection"]["guid:owned"]["show_tooltip"] = False
    with pytest.raises(AssertionError):
        validate_protocol(ProviderCase(), observations, native_wire=wire)


@pytest.mark.parametrize("case", provider_cases(), ids=lambda case: case.name)
def test_source_size_normalization_accepts_the_complete_valid_abi_grid(case):
    observed, wire = protocol_evidence(case)
    validate_protocol(case, observed, native_wire=wire)


@pytest.mark.parametrize(
    "case",
    (
        ProviderCase(),
        ProviderCase(size="v3"),
        ProviderCase(identity="hwnd", size="v2"),
    ),
)
def test_oracle_rejects_full_size_flag_loss_and_legacy_flag_retention(case):
    observed, wire = protocol_evidence(case)
    row = wire[0]
    incorrect = row["flags"] ^ 0x80
    prefix = bytearray.fromhex(row["raw_prefix_hex"])
    struct.pack_into("<I", prefix, 20, incorrect)
    row.update(flags=incorrect, raw_flags=incorrect, raw_prefix_hex=prefix.hex())
    row["projection"]["show_tip"] = bool(incorrect & 0x80)
    observed["showtip-only-on"]["projection"]["guid:owned"]["show_tooltip"] = bool(
        incorrect & 0x80
    )
    with pytest.raises(AssertionError):
        validate_protocol(case, observed, native_wire=wire)


@pytest.mark.parametrize(
    "offset,value",
    (
        (0, 0),
        (4, 4),
        (8, 955),
        (12, 201),
        (16, 732),
        (20, 0),
    ),
)
def test_raw_canonical_header_must_match_decoded_flags_identity_and_operation(
    offset, value
):
    observed, wire = protocol_evidence(ProviderCase())
    prefix = bytearray.fromhex(wire[0]["raw_prefix_hex"])
    struct.pack_into("<I", prefix, offset, value)
    wire[0]["raw_prefix_hex"] = prefix.hex()
    with pytest.raises(AssertionError):
        validate_protocol(ProviderCase(), observed, native_wire=wire)


@pytest.mark.parametrize(
    "field,value",
    (
        ("payload_length", 963),
        ("payload_length", 8193),
        ("raw_prefix_hex", "00" * 31),
        ("native_cb_size", 968),
        ("wire_cb_size", 936),
        ("flags", 0),
    ),
)
def test_wire_evidence_requires_bounded_payload_and_exact_source_and_canonical_sizes(
    field, value
):
    observed, wire = protocol_evidence(ProviderCase())
    wire[0][field] = value
    with pytest.raises(AssertionError):
        validate_protocol(ProviderCase(), observed, native_wire=wire)


def test_shell_payload_padding_is_valid_but_missing_backend_wire_is_not():
    observed, wire = protocol_evidence(ProviderCase())
    # Real Shell32 sends additional private tail bytes beyond canonical cbSize.
    # Only the bounded prefix is retained; it must not require capturing the tail.
    wire[0]["payload_length"] = 1484
    validate_protocol(ProviderCase(), observed, native_wire=wire)
    with pytest.raises(AssertionError):
        validate_protocol(ProviderCase(), observed, native_wire=None)


def test_explorer_api_return_evidence_does_not_assert_unobserved_visible_policy():
    observed, _wire = protocol_evidence(ProviderCase(size="v3"))
    for name in (
        "showtip-only-on",
        "showtip-only-off",
        "second-add",
        "second-provider-modify",
    ):
        observed[name]["projection"] = None
    validate_protocol(ProviderCase(size="v3"), observed, native_wire=None)


def notification_packet(window=200, uid=731, flags=0, version=4, operation=1):
    data = bytearray(964)
    struct.pack_into(
        "<8I", data, 0, 0x34753423, operation, 956, window, uid, flags, 0x8001, 0
    )
    struct.pack_into("<I", data, 808, version)
    private = "Unrelated provider text must never be captured".encode("utf-16-le")
    data[32 : 32 + len(private)] = private
    return bytes(data)


class TraceHost:
    def __init__(self):
        self.forwarded = []
        self.result = 1
        self.local_focus = False

    def _forward(self, _window, _message, _first, _second, *, handled=False):
        self.forwarded.append(handled)
        return self.result

    def _dispatch_notification(self, window, first, second, _payload):
        if self.local_focus:
            return 1
        return self._forward(window, 0x4A, first, second, handled=False)


def test_wire_trace_has_no_startup_or_unrelated_provider_metadata():
    host, rows = TraceHost(), []
    trace = OwnedWireTrace(host)
    host._dispatch_notification(10, 0, 0, notification_packet())
    assert rows == []
    trace.begin(ProviderCase(identity="hwnd"), "", ((200, 731),), rows, lambda _m: None)
    for packet in (
        notification_packet(window=201),
        notification_packet(uid=732),
        b"invalid",
    ):
        host._dispatch_notification(10, 0, 0, packet)
    assert rows == [] and len(host.forwarded) == 4
    trace.close()


@pytest.mark.parametrize("size,flags", (("v2", 0), ("full", 0x80)))
def test_wire_trace_records_delivered_old_size_flags_without_adjusting_policy(
    size, flags
):
    host, rows = TraceHost(), []
    original_dispatch, original_forward = host._dispatch_notification, host._forward
    trace = OwnedWireTrace(host)
    case = ProviderCase(identity="hwnd", size=size)
    trace.begin(
        case, "", ((200, 731),), rows, lambda _m: {"version": 4, "show_tip": False}
    )
    packet = notification_packet(flags=flags)
    assert host._dispatch_notification(10, 0, 0, packet) == 1
    row = rows[0]
    assert row["flags"] == row["raw_flags"] == flags
    assert row["version"] == 4 and row["native_cb_size"] == case.native_size
    assert row["wire_cb_size"] == 956 and row["payload_length"] == len(packet)
    assert bytes.fromhex(row["raw_prefix_hex"]) == packet[:32]
    assert len(row["raw_prefix_hex"]) == 64
    assert row["forwarded"] and row["accepted"]
    assert row["projection"] == {"version": 4, "show_tip": False}
    assert "Unrelated" not in json.dumps(rows)
    trace.close()
    assert host._dispatch_notification == original_dispatch
    assert host._forward == original_forward


def test_wire_trace_filters_exact_generated_guid_including_zero_hwnd_updates():
    import uuid

    guid = str(uuid.uuid4())
    packet = bytearray(notification_packet(window=0, uid=0, flags=0x20))
    packet[944:960] = uuid.UUID(guid).bytes_le
    host, rows = TraceHost(), []
    trace = OwnedWireTrace(host)
    trace.begin(ProviderCase(), guid, ((200, 731),), rows, lambda _m: None)
    host._dispatch_notification(10, 0, 0, bytes(packet))
    assert len(rows) == 1 and rows[0]["window"] == rows[0]["uid"] == 0
    packet[944:960] = uuid.uuid4().bytes_le
    host._dispatch_notification(10, 0, 0, bytes(packet))
    assert len(rows) == 1
    trace.close()


def test_wire_trace_bounds_owned_packets_and_tracks_rejection_and_local_focus():
    host, rows = TraceHost(), []
    trace = OwnedWireTrace(host)
    trace.begin(ProviderCase(identity="hwnd"), "", ((200, 731),), rows, lambda _m: None)
    host.result = 0
    host._dispatch_notification(10, 0, 0, notification_packet())
    assert rows[0]["forwarded"] and not rows[0]["accepted"]
    host.local_focus = True
    for _ in range(513):
        host._dispatch_notification(10, 0, 0, notification_packet(operation=3))
    assert len(rows) == 512 and rows[-1]["trace_truncated"]
    assert not rows[1]["forwarded"] and rows[1]["accepted"]
    trace.clear()
    host._dispatch_notification(10, 0, 0, notification_packet())
    assert len(rows) == 512
    trace.close()


def test_selected_wheel_precedes_source_before_first_docking_import(tmp_path):
    installed = tmp_path / "installed"
    package = installed / "docking"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("SENTINEL = 'owned-wheel'\n")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json,sys;from pathlib import Path;"
                "from tools.windows.visual.tray_acceptance import select_runtime,runtime_origins;"
                "runtime=select_runtime(Path(sys.argv[1]),None);import docking;"
                "assert docking.SENTINEL=='owned-wheel';"
                "print(json.dumps(runtime_origins(runtime.root,{'docking':docking.__file__})))"
            ),
            str(installed),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == {"docking": "docking/__init__.py"}


def test_runtime_origin_assertion_rejects_source_and_symlink_escape(tmp_path):
    installed = tmp_path / "installed"
    installed.mkdir()
    source = tmp_path / "source.py"
    source.write_text("owned unrelated source\n")
    with pytest.raises(AssertionError, match="outside the selected runtime"):
        runtime_origins(installed, {"docking": str(source)})
    link = installed / "module.py"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("Host does not permit owned symlink creation")
    with pytest.raises(AssertionError, match="outside the selected runtime"):
        runtime_origins(installed, {"docking": str(link)})


@pytest.fixture
def installed_wheel(tmp_path):
    installed = tmp_path / "installed"
    wheel = tmp_path / "docking-test.whl"
    files = {
        "docking/__init__.py": b"VERSION='owned-test'\n",
        "docking/app.py": b"APP='owned-test'\n",
        "docking/data/icon.png": b"generated-test-pixels",
    }
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
            target = installed / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        archive.writestr("docking_test.dist-info/METADATA", "Owned fixture metadata")
    return installed, wheel


def test_acceptance_proves_installed_bytes_and_exact_provided_wheel(
    installed_wheel, monkeypatch
):
    installed, wheel = installed_wheel
    monkeypatch.setattr(sys, "path", list(sys.path))
    runtime = select_runtime(installed, wheel)
    assert runtime.installed and runtime.root == installed
    assert runtime.acceptance("gtk-v0-v4") == {
        "schema": 1,
        "implementation": "installed-wheel",
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "profile": "gtk-v0-v4",
    }
    assert sys.path[0] == str(installed)
    assert runtime.acceptance("api-matrix")["profile"] == "api-matrix"
    assert runtime.acceptance("gtk-v3-v4-guid")["profile"] == "gtk-v3-v4-guid"


@pytest.mark.parametrize("change", ("modified", "missing", "extra"))
def test_wheel_label_cannot_hide_mixed_or_unshipped_installed_code(
    installed_wheel, change
):
    installed, wheel = installed_wheel
    app = installed / "docking/app.py"
    if change == "modified":
        app.write_bytes(b"different implementation")
    elif change == "missing":
        app.unlink()
    else:
        (installed / "docking/obsolete.py").write_bytes(b"unshipped code")
    with pytest.raises(AssertionError):
        verify_installed_wheel(installed, wheel)


def test_generated_python_cache_does_not_change_wheel_provenance(installed_wheel):
    installed, wheel = installed_wheel
    cache = installed / "docking/__pycache__"
    cache.mkdir()
    (cache / "app.cpython-310.pyc").write_bytes(b"generated owned bytecode")
    assert (
        verify_installed_wheel(installed, wheel)
        == hashlib.sha256(wheel.read_bytes()).hexdigest()
    )


def test_source_or_unverified_installation_cannot_emit_installed_acceptance(
    installed_wheel, monkeypatch
):
    installed, wheel = installed_wheel
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert select_runtime(None, None).acceptance("api-matrix") is None
    assert select_runtime(installed, None).acceptance("gtk-v0-v4") is None
    with pytest.raises(ValueError, match="requires --installed-root"):
        select_runtime(None, wheel)
    with pytest.raises(AssertionError):
        TrayRuntime(installed, False, "unverified").acceptance("api-matrix")


@pytest.mark.parametrize("bits,platform", ((64, "x64"), (32, "x86")))
def test_csharp_compiler_boundary_uses_native_unc_not_slash_options(
    tmp_path, monkeypatch, bits, platform
):
    from tools.windows.visual import tray_provider

    monkeypatch.setattr(
        tray_provider,
        "__file__",
        "//owned-fixture/share/tools/windows/visual/tray_provider.py",
    )
    monkeypatch.setenv("SYSTEMROOT", "C:/Windows")
    commands = []

    def compile_native(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tray_provider.subprocess, "run", compile_native)
    directory = tmp_path / "owned build"
    target = build_provider(directory, bits)
    (command,) = commands
    assert command[0] == r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"
    assert command[-1] == r"\\owned-fixture\share\tools\windows\visual\tray_provider.cs"
    assert "/platform:" + platform in command
    assert "/out:" + str(target).replace("/", "\\") in command
    assert not command[-1].startswith("//")


@pytest.mark.parametrize(
    "change",
    (
        {"target_focus": False},
        {"logical_selection": False},
        {"popup_visible": False},
        {"popup_window": 0},
        {"foreground": 901},
        {"native_focus_root": 901},
        {"foreground_pid": 456},
        {"native_focus_pid": 456},
        {"native_query_ok": False},
    ),
)
def test_keyboard_selection_requires_actual_native_focus_in_its_own_popup(change):
    snapshot = {
        "popup_window": 900,
        "native_query_ok": True,
        "popup_visible": True,
        "target_focus": True,
        "logical_selection": True,
        "foreground_pid": 123,
        "native_focus_pid": 123,
        "foreground": 900,
        "native_focus_root": 900,
    }
    assert keyboard_focus_ready(snapshot, 123)
    assert not keyboard_focus_ready({**snapshot, **change}, 123)


def test_compiler_diagnostics_redact_normalized_unc_source(tmp_path, monkeypatch):
    from tools.windows.visual import tray_provider

    monkeypatch.setattr(
        tray_provider,
        "__file__",
        "//owned-fixture/share/tools/windows/visual/tray_provider.py",
    )
    monkeypatch.setenv("SYSTEMROOT", "C:/Windows")

    def fail_compiler(_command, **_kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=r"\\owned-fixture\share\tools\windows\visual\tray_provider.cs: fixture failure",
        )

    monkeypatch.setattr(tray_provider.subprocess, "run", fail_compiler)
    with pytest.raises(RuntimeError, match="compilation failed"):
        build_provider(tmp_path, 64)
    assert (
        tmp_path / "build-64.log"
    ).read_text() == "<tool-source>\\tray_provider.cs: fixture failure"


def test_foreground_control_click_uses_exposed_caption_when_center_is_occluded():
    control = (180, 100, 540, 240)

    def root_at(x, y):
        return 999 if 207 <= x < 613 and 150 <= y < 382 else 123

    assert root_at(360, 170) == 999  # Actual overlapping native provider shape.
    point = visible_control_point(123, control, root_at)
    assert root_at(*point) == 123 and point[1] < 150


def test_control_click_checks_other_visible_regions_when_caption_is_occluded():
    allowed = (-1888, -130)
    checked = []

    def root_at(x, y):
        checked.append((x, y))
        return 123 if (x, y) == allowed else 999

    assert visible_control_point(123, (-1900, -200, -1540, -60), root_at) == allowed
    assert len(checked) > 1


@pytest.mark.parametrize("bounds", ((180, 100, 540, 240), (10, 10, 10, 20)))
def test_unavailable_control_point_fails_without_fabricating_foreground(bounds):
    with pytest.raises(AssertionError, match="Owned foreground control"):
        visible_control_point(123, bounds, lambda _x, _y: 999)


@pytest.fixture
def dock_click():
    ready = {
        "point": [500, 700],
        "cursor": [500, 700],
        "selected": "applet://systemtray",
        "native_window": 123,
        "dock_window": 123,
        "rect": [450, 20, 100, 100],
        "item_count": 6,
    }
    trace = {"moves": [], "presses": 0, "now": 0.0, "record": {}}

    def run(frames, *, final=None, press_error=False):
        settled = False

        def observe():
            if settled and final is not None:
                return final
            return frames.pop(0) if len(frames) > 1 else frames[0]

        def move(*point):
            trace["moves"].append(point)
            return True

        def press():
            trace["presses"] += 1
            if press_error:
                raise RuntimeError("Physical input failed")

        def wait_for(predicate, message):
            nonlocal settled
            for _ in range(30):
                trace["now"] += 0.1
                value = predicate()
                if value:
                    settled = True
                    return value
            raise AssertionError(message)

        physical_dock_click(
            observe=observe,
            move=move,
            press=press,
            wait_for=wait_for,
            record=trace["record"],
            clock=lambda: trace["now"],
        )

    return ready, trace, run


def test_dock_click_tracks_hover_and_new_running_group_before_one_press(dock_click):
    ready, trace, run = dock_click
    run(
        [
            {**ready, "point": [450, 700], "cursor": [10, 10]},
            {**ready, "cursor": [450, 700], "selected": "applet://clock"},
            ready,
        ]
    )
    assert trace["moves"] == [(450, 700), (500, 700)]
    assert trace["now"] >= 0.6
    assert trace["record"]["before_press"] == ready
    assert trace["presses"] == 1


@pytest.mark.parametrize(
    "change", ({"selected": "applet://clock"}, {"native_window": 999})
)
def test_dock_click_never_presses_a_wrong_or_occluded_target(dock_click, change):
    ready, trace, run = dock_click
    with pytest.raises(AssertionError, match="did not settle"):
        run([{**ready, **change}])
    assert trace["presses"] == 0


def test_dock_click_rechecks_layout_after_settling_before_first_press(dock_click):
    ready, trace, run = dock_click
    with pytest.raises(AssertionError, match="changed before the physical press"):
        run([ready], final={**ready, "point": [550, 700], "item_count": 7})
    assert trace["presses"] == 0


def test_dock_click_does_not_retry_a_failed_physical_action(dock_click):
    ready, trace, run = dock_click
    with pytest.raises(RuntimeError, match="Physical input failed"):
        run([ready], press_error=True)
    assert trace["presses"] == 1
