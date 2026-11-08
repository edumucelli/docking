"""KDE Connect capabilities, file targeting, owner changes, and applet lifetime."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import Mock

import pytest
from gi.repository import GLib

from docking.applets import get_applet_catalog, load_applet_class
from docking.applets.kdeconnect.applet import KdeconnectApplet
from docking.applets.kdeconnect.backend import (
    DAEMON_IFACE,
    DEVICE_IFACE,
    PROPERTIES_IFACE,
    ROOT_PATH,
    ConnectError,
    KdeConnectBackend,
)
from docking.applets.kdeconnect.render import render_icon
from docking.applets.kdeconnect.state import (
    BATTERY_PLUGIN,
    RING_PLUGIN,
    SHARE_PLUGIN,
    ConnectState,
    Device,
    local_files,
    selected_device,
    selected_from_prefs,
    tooltip,
)
from docking.core.config import Config

PHONE = Device(
    "phone_1",
    "Personal Phone",
    reachable=True,
    plugins=frozenset({BATTERY_PLUGIN, RING_PLUGIN, SHARE_PLUGIN}),
    battery=76,
)
OTHER = Device(
    "phone_2",
    "Work Phone",
    reachable=True,
    plugins=frozenset({SHARE_PLUGIN}),
    battery=0,
    charging=True,
)


class QueueWorker:
    def __init__(self):
        self.jobs = []

    def run(self, **job):
        self.jobs.append(job)

    def finish(self):
        job = self.jobs.pop(0)
        try:
            result = job["fn"]()
        except Exception as error:
            job.get("on_error", lambda error: None)(error)
        else:
            job.get("on_result", lambda result: None)(result)


@pytest.fixture
def applet(monkeypatch):
    backend = Mock()
    backend.snapshot.return_value = ConnectState(True, (PHONE, OTHER))
    monkeypatch.setattr(KdeconnectApplet, "backend_factory", lambda _self: backend)
    instance = KdeconnectApplet(48, Config())
    instance._worker = QueueWorker()
    instance.start(lambda: None)
    yield instance, backend
    instance.stop()


def populated_backend(
    monkeypatch, *, paired=True, reachable=True, plugins=None, battery=None
):
    backend = KdeConnectBackend()
    backend._owner = ":1.17"
    calls = []
    plugins = (
        [BATTERY_PLUGIN, RING_PLUGIN, SHARE_PLUGIN] if plugins is None else plugins
    )
    battery = (
        {"charge": 61, "isCharging": True, "hasBattery": True}
        if battery is None
        else battery
    )

    def call(path, interface, method, parameters=None):
        calls.append(
            (path, interface, method, parameters.unpack() if parameters else None)
        )
        if method == "devices":
            assert parameters.unpack() == (False, True)
            return (["phone_1"],)
        if method == "loadedPlugins":
            return (plugins,)
        if interface == PROPERTIES_IFACE and method == "GetAll":
            if path.endswith("/battery"):
                return (battery,)
            return (
                {
                    "name": "Phone",
                    "type": "phone",
                    "isPaired": paired,
                    "isReachable": reachable,
                },
            )
        return ()

    monkeypatch.setattr(backend, "_call", call)
    return backend, calls


def test_catalog_loads_only_optional_applet():
    assert "kdeconnect" in get_applet_catalog()
    assert load_applet_class("kdeconnect") is KdeconnectApplet


def test_paired_phone_snapshot_reads_battery_and_charging(monkeypatch):
    backend, _calls = populated_backend(monkeypatch)
    state = backend.snapshot()
    assert state.available
    assert state.devices[0].battery == 61
    assert state.devices[0].charging
    assert state.devices[0].supports(RING_PLUGIN)


@pytest.mark.parametrize(
    "battery",
    [
        {"charge": -1},
        {"charge": 101},
        {"charge": True},
        {"charge": 0, "hasBattery": False},
    ],
)
def test_invalid_or_absent_battery_does_not_hide_a_phone(monkeypatch, battery):
    backend, _calls = populated_backend(monkeypatch, battery=battery)
    state = backend.snapshot()
    assert state.available and state.devices[0].battery is None
    assert state.devices[0].supports(SHARE_PLUGIN)


def test_zero_battery_is_a_valid_percentage(monkeypatch):
    backend, _calls = populated_backend(
        monkeypatch, battery={"charge": 0, "hasBattery": True}
    )
    assert backend.snapshot().devices[0].battery == 0
    assert "0%" in tooltip(ConnectState(True, (OTHER,)), OTHER.device_id)


def test_offline_paired_device_remains_visible_without_actions(monkeypatch):
    backend, calls = populated_backend(monkeypatch, reachable=False)
    device = backend.snapshot().devices[0]
    assert not device.reachable
    assert not device.supports(SHARE_PLUGIN)
    assert not any(call[2] == "loadedPlugins" for call in calls)


def test_unpaired_device_cannot_be_used_even_if_the_daemon_lists_it(monkeypatch):
    backend, _calls = populated_backend(monkeypatch, paired=False)
    assert backend.snapshot().devices == ()
    with pytest.raises(ConnectError, match="no longer paired"):
        backend.ring("phone_1")


def test_ring_and_share_recheck_enabled_capabilities(monkeypatch, tmp_path):
    backend, calls = populated_backend(monkeypatch)
    backend.ring("phone_1")
    paths = (tmp_path / "file with spaces.txt", tmp_path / "second.txt")
    for path in paths:
        path.write_text("synthetic test content")
    backend.send_files("phone_1", paths)
    ring = [call for call in calls if call[2] == "ring"]
    shares = [call for call in calls if call[2] == "shareUrl"]
    assert ring == [
        (
            ROOT_PATH + "/devices/phone_1/findmyphone",
            DEVICE_IFACE + ".findmyphone",
            "ring",
            None,
        )
    ]
    assert [call[3] for call in shares] == [(path.as_uri(),) for path in paths]
    backend, calls = populated_backend(monkeypatch, plugins=[])
    with pytest.raises(ConnectError, match="does not support"):
        backend.send_files("phone_1", paths)
    assert not any(call[2] == "shareUrl" for call in calls)


def test_stale_online_snapshot_cannot_ring_an_offline_device(monkeypatch):
    backend, calls = populated_backend(monkeypatch, reachable=False)
    with pytest.raises(ConnectError, match="offline"):
        backend.ring("phone_1")
    assert not any(call[2] == "ring" for call in calls)


@pytest.mark.parametrize(
    "device_id", ["../phone", "phone/other", "phone-with-hyphens", "", "phone.1"]
)
def test_device_identifiers_cannot_change_dbus_paths(device_id):
    with pytest.raises(ConnectError, match="invalid device"):
        KdeConnectBackend._path(device_id)


def test_selected_offline_or_removed_phone_never_falls_back_to_another_target():
    offline = replace(PHONE, reachable=False)
    assert (
        selected_device(ConnectState(True, (offline, OTHER)), PHONE.device_id)
        == offline
    )
    assert selected_device(ConnectState(True, (OTHER,)), PHONE.device_id) is None
    assert selected_device(ConnectState(True, (offline, OTHER)), "") == OTHER
    assert selected_from_prefs({"selected_device": []}) == ""


def test_only_explicit_local_regular_files_are_accepted(tmp_path):
    file = tmp_path / "résumé with spaces.txt"
    file.write_text("synthetic content")
    assert local_files([file.as_uri(), file.as_uri()]) == (file,)
    for uris in (
        [],
        [tmp_path.as_uri()],
        ["https://example.test/a"],
        ["file://remote/path"],
        [str(tmp_path / "missing")],
    ):
        with pytest.raises(ValueError):
            local_files(uris)


def test_unavailable_and_unpaired_states_give_setup_instructions():
    assert "Install and start" in tooltip(ConnectState(), "")
    assert "No paired devices" in tooltip(ConnectState(True), "")


def test_refresh_bursts_are_serialized_and_selection_is_persisted(applet):
    instance, _backend = applet
    instance._refresh()
    instance._refresh()
    assert len(instance._worker.jobs) == 1
    instance._worker.finish()
    assert instance._selected == PHONE.device_id
    assert instance.load_prefs()["selected_device"] == PHONE.device_id
    assert "76%" in instance.item.name


def test_file_drop_keeps_selected_target_and_does_not_redirect_when_offline(
    applet, tmp_path
):
    instance, backend = applet
    file = tmp_path / "send.txt"
    file.write_text("synthetic content")
    instance._state = ConnectState(True, (PHONE, OTHER))
    instance._select(PHONE.device_id)
    assert instance.on_drop_uris([file.as_uri()])
    instance._select(OTHER.device_id)
    instance._worker.finish()
    backend.send_files.assert_called_once_with(PHONE.device_id, (file,))
    instance._state = ConnectState(True, (replace(PHONE, reachable=False), OTHER))
    instance._select(PHONE.device_id)
    assert not instance.accepts_drop_uris()
    instance.on_drop_uris([file.as_uri()])
    assert not instance._worker.jobs
    assert "online paired device" in instance.item.name


def test_menus_disable_unsupported_actions(applet):
    instance, _backend = applet
    instance._state = ConnectState(True, (OTHER,))
    instance._select(OTHER.device_id)
    rows = {item.get_label(): item for item in instance.get_menu_items()}
    assert not rows["Ring Phone"].get_sensitive()
    assert rows["Send Files..."].get_sensitive()


def test_file_chooser_keeps_its_original_recipient(applet, monkeypatch, tmp_path):
    from docking.applets.kdeconnect import applet as applet_module

    instance, backend = applet
    instance._state = ConnectState(True, (PHONE, OTHER))
    instance._select(PHONE.device_id)
    file = tmp_path / "chosen.txt"
    file.write_text("synthetic content")
    chooser = Mock()

    def finish_dialog():
        instance._select(OTHER.device_id)
        return applet_module.Gtk.ResponseType.OK

    chooser.run.side_effect = finish_dialog
    chooser.get_uris.return_value = [file.as_uri()]
    monkeypatch.setattr(applet_module.Gtk, "FileChooserDialog", lambda **kw: chooser)
    instance._choose_files()
    instance._worker.finish()
    backend.send_files.assert_called_once_with(PHONE.device_id, (file,))
    chooser.destroy.assert_called_once()


def test_stop_cancels_backend_and_ignores_late_results(applet):
    instance, backend = applet
    instance._refresh()
    instance.stop()
    instance._worker.finish()
    assert instance._state == ConnectState()
    backend.close.assert_called_once()
    assert not instance._timer and not instance._refresh_source
    assert not instance.accepts_drop_uris()
    assert not instance.on_drop_uris([])


def test_backend_uses_unique_owner_and_bounded_cancellable_calls():
    backend = KdeConnectBackend()
    bus = Mock()
    bus.call_sync.return_value = GLib.Variant("(as)", (["phone_1"],))
    backend._bus, backend._owner = bus, ":1.71"
    assert backend._call(
        ROOT_PATH, DAEMON_IFACE, "devices", GLib.Variant("(bb)", (False, True))
    ) == (["phone_1"],)
    args = bus.call_sync.call_args.args
    assert args[0] == ":1.71" and args[-2] == 1500
    assert not backend._pending


def test_name_loss_unsubscribes_and_cancels_outstanding_calls():
    backend = KdeConnectBackend()
    backend._bus = Mock()
    bus = backend._bus
    backend._owner = ":1.71"
    backend._subscriptions = [17, 18]
    pending = Mock()
    backend._pending.add(pending)
    changed = Mock()
    backend._on_change = changed
    backend._vanished(bus, "org.kde.kdeconnect")
    assert backend._owner == "" and backend._bus is None
    assert [call.args[0] for call in bus.signal_unsubscribe.call_args_list] == [17, 18]
    pending.cancel.assert_called_once()
    changed.assert_called_once()
    assert not backend.snapshot().available


@pytest.mark.parametrize("charge", [None, 0, 1, 100])
def test_phone_icon_renders_all_battery_boundaries(charge):
    icon = render_icon(size=48, connected=True, charge=charge, charging=True)
    assert icon is not None and icon.get_width() == 48
