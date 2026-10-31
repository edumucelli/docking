"""Browser process framing, actual pipe failures and GTK lifetime ownership."""

import io
import queue
import sys
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from docking.applets.webapp.profile import BrowserProfile
from docking.applets.webapp.state import BrowserPhase
from docking.applets.webapp.windows_companion import browser as mod
from docking.applets.webapp.windows_companion import process as transport
from docking.applets.webapp.windows_companion.protocol import (
    MAX_PACKET_BYTES,
    Configuration,
    encode,
    read,
)
from docking.applets.webapp.windows_companion.text_input import PacketText
from docking.applets.webapp.windows_companion.user_agent import chromium_user_agent
from tests.applets.deferred_worker import DeferredWorker

PROFILE = BrowserProfile(
    "WhatsApp", "whatsapp", "https://web.whatsapp.com/", "status", "fixture"
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) QtWebEngine/6.12.0 Chrome/140.0.7339.218 Safari/537.36"
            ),
            (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140.0.7339.218 Safari/537.36"
            ),
        ),
        ("QtWebEngine/6.12.0 Chrome/140.0.7339.218", "Chrome/140.0.7339.218"),
        ("Chrome/140.0 QtWebEngine/6.12.0", "Chrome/140.0"),
        (
            "Chrome/155.0 Safari/537.36 Edg/155.0",
            "Chrome/155.0 Safari/537.36 Edg/155.0",
        ),
        ("custom QtWebEngine/6.12.0", "custom QtWebEngine/6.12.0"),
    ],
)
def test_chromium_identity_preserves_actual_engine_and_platform(value, expected):
    assert chromium_user_agent(value) == expected


def test_windows_utf16_packet_order_and_focus_reset():
    text = PacketText()
    assert (
        "".join(text.feed(unit) for unit in (ord("A"), 0xD83C, 0xDF31, ord("é")))
        == "A🌱é"
    )
    assert text.feed(0xD83C) == ""
    text.reset()
    assert text.feed(ord("B")) == "B"
    assert text.feed(0xDF31) == "\ufffd"
    assert text.feed(0xD83C) == ""
    assert text.feed(ord("C")) == "\ufffdC"


def test_private_protocol_preserves_unicode_and_rejects_partial_frames():
    packet = {"event": "title", "value": "résumé 🌱"}
    assert read(io.BytesIO(encode(packet))) == {**packet, "version": 1}
    assert read(io.BytesIO()) is None
    for data in (
        b"{}",
        b"{}\n",
        b"[]\n",
        b'{"version":true}\n',
        b'{"version":2}\n',
        b"\xff\n",
        b"x" * (MAX_PACKET_BYTES + 1),
    ):
        with pytest.raises(ValueError):
            read(io.BytesIO(data))
    with pytest.raises(ValueError):
        encode({"value": "x" * MAX_PACKET_BYTES})


@pytest.mark.parametrize(
    "uri",
    [
        "http://web.whatsapp.com/",
        "https://web.whatsapp.com.evil.test/",
        "https://web.whatsapp.com:444/",
        "https://u:p@web.whatsapp.com/",
        "data:text/html,fixture",
    ],
)
def test_companion_configuration_requires_the_real_https_service_origin(tmp_path, uri):
    configuration = Configuration(replace(PROFILE, url=uri), tmp_path)
    with pytest.raises(ValueError):
        Configuration.parse(configuration.packet())


def test_configuration_round_trip_keeps_profiles_isolated(tmp_path):
    first = Configuration(PROFILE, tmp_path / "whatsapp")
    second = Configuration(
        replace(
            PROFILE,
            name="Telegram",
            session_name="telegram",
            url="https://web.telegram.org/",
        ),
        tmp_path / "telegram",
    )
    assert Configuration.parse(first.packet()) == first
    assert Configuration.parse(second.packet()) == second


def child(tmp_path, source):
    entrypoint = tmp_path / "browser_child.py"
    entrypoint.write_text(
        "import json,sys,time\nconfiguration=json.loads(sys.stdin.buffer.readline())\n"
        + source
    )
    packets, closed = queue.Queue(), threading.Event()
    process = transport.BrowserProcess(
        Path(sys.executable),
        Configuration(PROFILE, tmp_path / "profile"),
        entrypoint=entrypoint,
        on_packet=packets.put,
        on_closed=closed.set,
    )
    process.start()
    return process, packets, closed


def test_real_companion_pipes_preserve_order_and_reap_normal_exit(tmp_path):
    process, packets, closed = child(
        tmp_path,
        """
assert configuration['profile']['url']=='https://web.whatsapp.com/'
print(json.dumps({'version':1,'event':'started'}),flush=True)
for line in sys.stdin.buffer:
    command=json.loads(line)
    if command['op']=='stop':break
    print(json.dumps({'version':1,'event':'echo','value':command['value']}),flush=True)
""",
    )
    try:
        assert packets.get(timeout=5)["event"] == "started"
        for value in ("résumé", "🌱", "last"):
            process.send({"op": "echo", "value": value})
        assert [packets.get(timeout=5)["value"] for _ in range(3)] == [
            "résumé",
            "🌱",
            "last",
        ]
    finally:
        process.stop()
        assert closed.wait(5)
    assert process._process.poll() is not None
    assert process._process.stdin.closed and process._process.stdout.closed


@pytest.mark.parametrize(
    "response",
    ["not json", "x" * (MAX_PACKET_BYTES + 1)],
    ids=["invalid-json", "oversized-packet"],
)
def test_malformed_real_child_output_closes_owned_process(tmp_path, response):
    process, _packets, closed = child(
        tmp_path, f"print({response!r},flush=True)\ntime.sleep(60)\n"
    )
    try:
        assert closed.wait(5)
        assert process._process.poll() is not None
    finally:
        process.stop()


def test_real_hung_child_has_bounded_shutdown(tmp_path, monkeypatch):
    monkeypatch.setattr(transport, "SHUTDOWN_GRACE_SECONDS", 0.2)
    process, packets, closed = child(
        tmp_path,
        "print(json.dumps({'version':1,'event':'started'}),flush=True)\ntime.sleep(60)\n",
    )
    assert packets.get(timeout=5)["event"] == "started"
    process.stop()
    assert closed.wait(5)
    assert process._process.poll() is not None


def test_shutdown_can_reap_child_before_it_reads_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(transport, "SHUTDOWN_GRACE_SECONDS", 0.2)
    entrypoint = tmp_path / "cold_child.py"
    entrypoint.write_text("import time\ntime.sleep(60)\n")
    process = transport.BrowserProcess(
        Path(sys.executable),
        Configuration(
            replace(PROFILE, bridge_script="x" * 60000), tmp_path / "profile"
        ),
        entrypoint=entrypoint,
        on_packet=lambda _packet: None,
        on_closed=lambda: None,
    )
    process.start()
    assert process._launched.wait(5)
    process.stop()
    assert process.closed.wait(5)
    assert process._process.poll() is not None


def test_disconnect_reaps_real_child_before_removing_profile_and_restarting(tmp_path):
    process, packets, closed = child(
        tmp_path,
        """
import os
print(json.dumps({'version':1,'event':'started','pid':os.getpid()}),flush=True)
for line in sys.stdin.buffer:
    command=json.loads(line)
    if command['op']=='clear':
        print(json.dumps({'version':1,'event':'restart','id':command['id']}),flush=True)
        break
    if command['op']=='stop':break
""",
    )
    try:
        first = packets.get(timeout=5)
        marker = process._configuration.directory / "owned-session"
        marker.write_text("generated session data")
        process.send({"op": "clear", "id": 7})
        assert packets.get(timeout=5) == {
            "version": 1,
            "event": "cleared",
            "id": 7,
            "ok": True,
        }
        replacement = packets.get(timeout=5)
        assert replacement["event"] == "started" and replacement["pid"] != first["pid"]
        assert not marker.exists() and not closed.is_set()
    finally:
        process.stop()
        assert closed.wait(5)


@pytest.fixture
def setup(monkeypatch, tmp_path):
    delivery = DeferredWorker()
    monkeypatch.setattr(mod.GLib, "idle_add", delivery._idle)
    monkeypatch.setattr(mod.GLib, "source_remove", Mock())
    monkeypatch.setattr(mod, "docking_data_dir", lambda: tmp_path)
    processes = []

    class Process:
        def __init__(
            self, executable, configuration, *, on_packet, on_closed, entrypoint
        ):
            self.configuration, self.commands = configuration, []
            self.on_packet, self.on_closed = on_packet, on_closed
            self.closed = self.started = self.foreground_allowed = False
            processes.append(self)

        def start(self):
            self.started = True

        def send(self, packet):
            self.commands.append(packet)

        def allow_foreground(self):
            self.foreground_allowed = True

        def stop(self):
            self.closed = True
            self.on_closed()

    monkeypatch.setattr(mod, "BrowserProcess", Process)
    callbacks = {
        name: Mock()
        for name in (
            "on_phase",
            "on_title",
            "on_badge",
            "on_notification",
            "on_attention_cleared",
        )
    }
    browser = mod.WindowsCompanionBrowser(
        profile=PROFILE, executable=tmp_path / "python.exe", **callbacks
    )
    yield browser, processes, delivery, callbacks
    browser.stop()
    delivery.drain()


def test_hidden_start_and_show_wait_for_actual_companion_readiness(setup):
    browser, processes, delivery, _callbacks = setup
    browser.start()
    process = processes[0]
    assert process.started and not browser.visible and not process.commands
    browser.show()
    assert len(processes) == 1 and not process.commands
    process.on_packet({"event": "started"})
    delivery.drain()
    assert process.commands == [{"op": "show"}]
    process.on_packet({"event": "visible", "value": True})
    delivery.drain()
    assert browser.visible
    browser.hide()
    assert process.commands[-1] == {"op": "hide"}


def test_stopped_or_replaced_process_cannot_publish_page_or_clear_results(setup):
    browser, processes, delivery, callbacks = setup
    browser.start()
    old = processes[0]
    browser.stop()
    browser.start()
    callbacks["on_title"].reset_mock()
    old.on_packet({"event": "title", "value": "stale"})
    old.on_packet({"event": "visible", "value": True})
    old.on_packet({"event": "notification"})
    delivery.drain()
    assert not browser.visible and browser.phase is BrowserPhase.STARTING
    callbacks["on_title"].assert_not_called()
    callbacks["on_notification"].assert_not_called()


def test_disconnect_is_serialized_and_completion_cannot_survive_stop(setup):
    browser, processes, delivery, _callbacks = setup
    complete = Mock()
    browser.clear_session(complete)
    process = processes[0]
    process.on_packet({"event": "started"})
    delivery.drain()
    assert process.commands == [{"op": "clear", "id": 1}]
    browser.clear_session(Mock())
    process.on_packet({"event": "cleared", "id": 99, "ok": True})
    delivery.drain()
    complete.assert_not_called()
    process.on_packet({"event": "cleared", "id": 1, "ok": True})
    delivery.publish()
    complete.assert_not_called()
    browser.stop()
    delivery.drain()
    complete.assert_not_called()


def test_failed_companion_start_finishes_disconnect_as_unavailable(setup):
    browser, processes, delivery, _callbacks = setup
    complete = Mock()
    browser.clear_session(complete)
    processes[0].on_closed()
    delivery.drain()
    complete.assert_called_once_with(False, "Could not disconnect WhatsApp")
    assert browser.phase is BrowserPhase.UNAVAILABLE


def test_packets_from_confirmed_dead_process_cannot_revive_ui(setup, monkeypatch):
    browser, processes, delivery, callbacks = setup
    monkeypatch.setattr(mod.GLib, "timeout_add", lambda *_args: 42)
    browser.start()
    process = processes[0]
    process.on_packet({"event": "started"})
    delivery.drain()
    callbacks["on_title"].reset_mock()
    process.on_closed()
    delivery.publish()
    process.on_packet({"event": "visible", "value": True})
    process.on_packet({"event": "title", "value": "late"})
    delivery.drain()
    assert not browser.visible and browser.phase is BrowserPhase.ERROR
    callbacks["on_title"].assert_not_called()


def test_successful_disconnect_completes_once_after_ui_delivery(setup):
    browser, processes, delivery, _callbacks = setup
    completed = Mock()
    browser.clear_session(completed)
    process = processes[0]
    process.on_packet({"event": "started"})
    delivery.drain()
    process.on_packet({"event": "cleared", "id": 1, "ok": True})
    delivery.drain()
    completed.assert_called_once_with(True, "")
    process.on_packet({"event": "cleared", "id": 1, "ok": True})
    delivery.drain()
    completed.assert_called_once_with(True, "")


def test_clear_result_remains_deliverable_when_the_process_then_exits(
    setup, monkeypatch
):
    browser, processes, delivery, _callbacks = setup
    monkeypatch.setattr(mod.GLib, "timeout_add", lambda *_args: 42)
    completed = Mock()
    browser.clear_session(completed)
    process = processes[0]
    process.on_packet({"event": "started"})
    delivery.drain()
    process.on_packet({"event": "cleared", "id": 1, "ok": False})
    process.on_closed()
    delivery.drain()
    completed.assert_called_once_with(False, "Could not disconnect WhatsApp")
