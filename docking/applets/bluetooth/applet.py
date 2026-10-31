# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""GTK lifecycle and menu wiring for Bluetooth applet.

Design notes:
- BlueZ discovery sessions are owned per DBus client. If another client
  started discovery, adapter `Discovering` can be true while our own
  `StopDiscovery` call returns `org.bluez.Error.NotReady`.
- This applet therefore tracks whether *it* started discovery and only tries to
  stop discovery on shutdown/toggle-off when the local session is known active.
- Power transitions are serialized to avoid duplicate toggle races from UI
  events while BlueZ is still applying changes.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

from docking.applets.platform import AppletPlatformServices

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from docking.applets.base import PlatformServicesApplet
from docking.applets.bluetooth import meta
from docking.applets.menu import disabled_menu_item, menu_sections, radio_submenu
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

from .render import create_bluetooth_icon
from .state import (
    BluetoothAdapterState,
    BluetoothDeviceState,
    BluetoothState,
    adapter_from_state,
    build_tooltip,
    connected_count,
    device_menu_label,
    unavailable_state,
)

if TYPE_CHECKING:
    from docking.applets.bluetooth.backend import BluetoothBackend
    from docking.core.config import Config

log = with_context(get_logger(name="bluetooth"), applet_id=meta.id)

POLL_INTERVAL_S = 2
DISCOVERY_KEEPALIVE_S = 8
PAIR_TIMEOUT_S = 20
DISCOVERY_SUPPRESS_AFTER_POWER_OFF_S = 4.0
RECENT_CONNECTION_LIMIT = 6


class BluetoothApplet(PlatformServicesApplet):
    """Bluetooth quick manager with multi-adapter support."""

    id = meta.id
    name = _("Bluetooth")
    icon_name = "bluetooth-active-symbolic"

    def __init__(
        self,
        icon_size: int,
        config: Config,
        *,
        platform_services: AppletPlatformServices,
    ) -> None:
        self._platform = platform_services
        self._native = platform_services.backends.bluetooth_desktop()
        self._backend = self._platform.backends.bluetooth()
        self._state: BluetoothState = unavailable_state()
        self._poll_id: int = 0
        self._discovery_id: int = 0
        self._active_adapter_path: str = ""
        self._continuous_discovery = True
        self._suppress_discovery_until: float = 0.0
        # Keep each owned session until its matching StopDiscovery succeeds.
        self._local_discovery_paths: set[str] = set()
        self._stopped = False
        # Serializes power transitions. Prevents repeated calls when users click
        # quickly or when menu toggle events fire while the backend is busy.
        self._power_transition_in_progress = False
        self._power_transition_adapter_path = ""
        self._poll_generation = 0
        self._worker = BackgroundWorker(logger=log)
        # Optional user-facing line appended to tooltip when an action fails.
        self._action_error: str = ""
        self._recent_connections: dict[str, float] = {}
        self._prefs_dirty = False

        prefs = config.applet_prefs.get(meta.id, {})
        self._active_adapter_path = str(prefs.get("active_adapter_path", "") or "")
        self._continuous_discovery = _as_pref_bool(
            prefs.get("continuous_discovery", True),
            default=True,
        )
        raw_recent = prefs.get("recent_connections", {})
        if isinstance(raw_recent, dict):
            for address, value in raw_recent.items():
                try:
                    timestamp = float(value)
                except (TypeError, ValueError):
                    continue
                if address:
                    self._recent_connections[str(address)] = timestamp

        self._known_connected_addresses: set[str] = set()
        super().__init__(
            icon_size=icon_size, config=config, platform_services=platform_services
        )
        if self._prefs_dirty:
            self._save_prefs()
        self.present()

    def create_icon(self, size: int):
        adapter = self._active_adapter()
        powered = adapter.powered if adapter is not None else False
        discovering = adapter.discovering if adapter is not None else False
        return create_bluetooth_icon(
            size=size,
            available=self._state.available,
            powered=powered,
            discovering=discovering,
            connected_devices=connected_count(self._state),
        )

    def refresh_tooltip(self) -> None:
        text = build_tooltip(
            state=self._state,
            active_adapter_path=self._active_adapter_path,
        )
        if self._action_error:
            text = f"{text}\n{self._action_error}"
        self.item.name = text

    def start(self, notify: Callable[[], None]) -> None:
        if self._stopped:
            self._backend = self._platform.backends.bluetooth()
            self._local_discovery_paths = set()
            self._power_transition_in_progress = False
            self._power_transition_adapter_path = ""
        self._poll_generation += 1
        self._stopped = False
        super().start(notify=notify)
        self._refresh_now()
        self._poll_id = GLib.timeout_add_seconds(POLL_INTERVAL_S, self._tick)
        self._discovery_id = GLib.timeout_add_seconds(
            DISCOVERY_KEEPALIVE_S,
            self._discovery_tick,
        )

    def stop(self) -> None:
        self._stopped = True
        self._poll_generation += 1
        # Only try to stop discovery if we own a local discovery session.
        # This avoids noisy NotReady calls when scanning is externally owned.
        self._stop_local_discovery(quiet=True)
        self._backend.close()
        if self._poll_id:
            GLib.source_remove(self._poll_id)
            self._poll_id = 0
        if self._discovery_id:
            GLib.source_remove(self._discovery_id)
            self._discovery_id = 0
        super().stop()

    def on_clicked(self) -> None:
        adapter = self._active_adapter()
        if adapter is None:
            return
        target = not adapter.powered
        self._set_power_async(target=target)

    def on_scroll(self, direction_up: bool) -> None:
        _ = direction_up
        return

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        if not self._state.available:
            return [disabled_menu_item(_("Bluetooth unavailable"), gtk=Gtk)]

        adapter = self._active_adapter()
        if adapter is None:
            return [disabled_menu_item(_("No Bluetooth adapter"), gtk=Gtk)]

        powered_label = _("On") if adapter.powered else _("Off")
        connected_devices = [
            d
            for d in self._state.devices
            if d.adapter_path == adapter.path and d.connected
        ]

        status = [
            disabled_menu_item(
                _("{alias} ({powered}) - Connected {connected}").format(
                    alias=adapter.alias,
                    powered=powered_label,
                    connected=len(connected_devices),
                ),
                gtk=Gtk,
            )
        ]

        primary, manage, settings = self._build_action_sections(adapter=adapter)

        discovery_toggle = Gtk.CheckMenuItem(label=_("Continuous Discovery"))
        discovery_toggle.set_active(self._continuous_discovery)
        discovery_toggle.connect("toggled", self._on_continuous_discovery_toggled)
        display: list[Gtk.MenuItem] = [discovery_toggle]

        if len(self._state.adapters) > 1:
            display.append(self._build_adapter_submenu())

        manage.extend(self._build_devices_sections(adapter_path=adapter.path))

        refresh_item = Gtk.MenuItem(label=_("Refresh Now"))
        refresh_item.connect("activate", lambda _w: self._refresh_now())
        return menu_sections(
            status=status,
            primary=primary,
            refresh=[refresh_item],
            display=display,
            manage=manage,
            settings=settings,
            gtk=Gtk,
        )

    def _build_action_sections(
        self,
        *,
        adapter: BluetoothAdapterState,
    ) -> tuple[list[Gtk.MenuItem], list[Gtk.MenuItem], list[Gtk.MenuItem]]:
        primary: list[Gtk.MenuItem] = []
        manage: list[Gtk.MenuItem] = []
        settings: list[Gtk.MenuItem] = []

        power_label = (
            _("Turn Bluetooth Off") if adapter.powered else _("Turn Bluetooth On")
        )
        power_item = Gtk.MenuItem(label=power_label)
        power_item.connect(
            "activate",
            lambda _w: self._set_power_async(target=not adapter.powered),
        )
        primary.append(power_item)

        connected_devices = [
            d
            for d in self._state.devices
            if d.adapter_path == adapter.path and d.connected
        ]
        for device in connected_devices:
            disconnect_item = Gtk.MenuItem(
                label=_("Disconnect {device}").format(
                    device=device.alias or device.name or device.address
                )
            )
            disconnect_item.connect(
                "activate",
                lambda _w, p=device.path: self._run_async(
                    lambda: self._backend.disconnect_device(p)
                ),
            )
            primary.append(disconnect_item)

        send_cmd = self._native.send_files_command()
        if send_cmd is not None:
            send_item = Gtk.MenuItem(label=_("Send Files to Device..."))
            send_item.connect("activate", lambda _w: self._native.open_send_files())
            primary.append(send_item)

        manage.append(self._build_recent_connections_submenu(adapter_path=adapter.path))

        devices_cmd = self._native.devices_command()
        if devices_cmd is not None:
            devices_item = Gtk.MenuItem(label=_("Devices..."))
            devices_item.connect("activate", lambda _w: self._native.open_devices())
            settings.append(devices_item)

        adapters_cmd = self._native.adapters_command()
        if adapters_cmd is not None:
            adapters_item = Gtk.MenuItem(label=_("Adapters..."))
            adapters_item.connect("activate", lambda _w: self._native.open_adapters())
            settings.append(adapters_item)

        services_cmd = self._native.local_services_command()
        if services_cmd is not None:
            services_item = Gtk.MenuItem(label=_("Local Services..."))
            services_item.connect(
                "activate", lambda _w: self._native.open_local_services()
            )
            settings.append(services_item)

        return primary, manage, settings

    def _build_recent_connections_submenu(self, *, adapter_path: str) -> Gtk.MenuItem:
        item = Gtk.MenuItem(label=_("Recent Connections"))
        submenu = Gtk.Menu()
        recent_devices = self._recent_devices(adapter_path=adapter_path)

        if not recent_devices:
            empty = Gtk.MenuItem(label=_("No recent connections"))
            empty.set_sensitive(False)
            submenu.append(empty)
            item.set_submenu(submenu)
            return item

        for device in recent_devices:
            child = Gtk.MenuItem(label=device_menu_label(device))
            if device.connected:
                child.set_sensitive(False)
            else:
                child.connect(
                    "activate",
                    lambda _w, d=device: self._run_async(
                        lambda: self._connect_device(device=d)
                    ),
                )
            submenu.append(child)

        item.set_submenu(submenu)
        return item

    def _build_adapter_submenu(self) -> Gtk.MenuItem:
        return radio_submenu(
            label=_("Adapter"),
            choices=tuple(
                (adapter.alias or adapter.name, adapter.path)
                for adapter in self._state.adapters
            ),
            active_value=self._active_adapter_path,
            on_selected=lambda widget, value: self._on_select_adapter(widget, value),
            gtk=Gtk,
        )

    def _build_devices_sections(self, *, adapter_path: str) -> list[Gtk.MenuItem]:
        devices = [d for d in self._state.devices if d.adapter_path == adapter_path]
        connected = [d for d in devices if d.connected]
        paired = [d for d in devices if d.paired and not d.connected]
        discovered = [d for d in devices if not d.paired]

        items: list[Gtk.MenuItem] = []
        groups = [
            (_("Connected Devices"), connected),
            (_("Paired Devices"), paired),
            (_("Discovered Devices"), discovered),
        ]
        non_empty_groups = [(title, members) for title, members in groups if members]
        for index, (title, members) in enumerate(non_empty_groups):
            items.extend(self._device_group(title, members))
            if index < len(non_empty_groups) - 1:
                items.append(Gtk.SeparatorMenuItem())
        return items

    def _device_group(
        self,
        title: str,
        devices: list[BluetoothDeviceState],
    ) -> list[Gtk.MenuItem]:
        if not devices:
            return []

        items: list[Gtk.MenuItem] = [self._make_header(label=title)]

        for device in devices:
            label = device_menu_label(device)
            device_item = Gtk.MenuItem(label=label)
            submenu = Gtk.Menu()

            if device.connected:
                disconnect = Gtk.MenuItem(label=_("Disconnect"))
                disconnect.connect(
                    "activate",
                    lambda _w, p=device.path: self._run_async(
                        lambda: self._backend.disconnect_device(p)
                    ),
                )
                submenu.append(disconnect)
            else:
                connect = Gtk.MenuItem(label=_("Connect"))
                connect.connect(
                    "activate",
                    lambda _w, d=device: self._run_async(
                        lambda: self._connect_device(device=d)
                    ),
                )
                submenu.append(connect)

            if not device.paired:
                pair = Gtk.MenuItem(label=_("Pair"))
                pair.connect(
                    "activate",
                    lambda _w, d=device: self._run_async(
                        lambda: self._backend.pair_device(
                            d.path,
                            address=d.address,
                            timeout_s=PAIR_TIMEOUT_S,
                        )
                    ),
                )
                submenu.append(pair)

            if device.paired and self._backend.capabilities.remove_pairing:
                remove = Gtk.MenuItem(label=_("Remove Pairing"))
                remove.connect(
                    "activate",
                    lambda _w, d=device: self._run_async(
                        lambda: self._backend.remove_device(
                            d.adapter_path,
                            d.path,
                        )
                    ),
                )
                submenu.append(remove)

            if self._backend.capabilities.trust:
                trust = Gtk.CheckMenuItem(label=_("Trusted"))
                trust.set_active(device.trusted)
                trust.connect(
                    "toggled",
                    lambda w, p=device.path: self._run_async(
                        lambda: self._backend.set_trusted(
                            p,
                            trusted=w.get_active(),
                        )
                    ),
                )
                submenu.append(trust)

            if device.battery_percent is not None:
                battery = Gtk.MenuItem(
                    label=_("Battery: {percent}%").format(
                        percent=device.battery_percent
                    )
                )
                battery.set_sensitive(False)
                submenu.append(battery)

            device_item.set_submenu(submenu)
            items.append(device_item)

        return items

    @staticmethod
    def _make_header(label: str) -> Gtk.MenuItem:
        menu_item = Gtk.MenuItem(label=label)
        menu_item.set_sensitive(False)
        return menu_item

    def _tick(self) -> bool:
        backend = self._backend
        generation = self._poll_generation
        self._worker.run_guarded(
            key="poll",
            name="bluetooth-poll",
            fn=lambda: backend.get_state(active_adapter_path=self._active_adapter_path),
            on_result=lambda state: (
                self._on_poll_result(state)
                if self._request_is_current(backend, generation)
                else False
            ),
        )
        return True

    def _discovery_tick(self) -> bool:
        self._ensure_discovery()
        return True

    def _on_poll_result(self, state: BluetoothState) -> bool:
        if self._stopped:
            return False
        previous_state = self._state
        previous_adapter_path = self._active_adapter_path
        previous_discovery_paths = self._local_discovery_paths.copy()
        self._state = state
        self._sync_selected_adapter()
        self._record_recent_connections(state)
        self._ensure_discovery()
        if (
            state != previous_state
            or self._active_adapter_path != previous_adapter_path
            or self._local_discovery_paths != previous_discovery_paths
        ):
            self.present()
        return False

    def _refresh_now(self) -> None:
        backend = self._backend
        generation = self._poll_generation
        self._worker.run_guarded(
            key="refresh",
            name="bluetooth-refresh",
            fn=lambda: backend.get_state(active_adapter_path=self._active_adapter_path),
            on_result=lambda state: (
                self._on_refresh_result(state)
                if self._request_is_current(backend, generation)
                else False
            ),
        )

    def _sync_selected_adapter(self, persist: bool = True) -> None:
        selected = adapter_from_state(
            state=self._state,
            preferred_path=self._active_adapter_path,
        )
        if selected is not None:
            if selected == self._active_adapter_path:
                return
            self._active_adapter_path = selected
            if persist:
                self._save_prefs()

    def _active_adapter(self) -> BluetoothAdapterState | None:
        return next(
            (
                adapter
                for adapter in self._state.adapters
                if adapter.path == self._active_adapter_path
            ),
            None,
        )

    def _ensure_discovery(self) -> None:
        if self._stopped or not self._continuous_discovery:
            return
        adapter = self._active_adapter()
        if adapter is None or not adapter.powered or adapter.discovering:
            return
        if (
            self._power_transition_in_progress
            and adapter.path == self._power_transition_adapter_path
        ):
            return
        if time.monotonic() < self._suppress_discovery_until:
            return
        backend = self._backend
        generation = self._poll_generation

        def completed(started: bool) -> bool:
            if not self._request_is_current(backend, generation):
                if started:
                    self._worker.run(
                        name="bluetooth-discovery-cleanup",
                        fn=lambda: backend.stop_discovery(adapter.path, quiet=True),
                    )
                return False
            return self._on_discovery_result(adapter.path, started)

        self._worker.run_guarded(
            key="discovery",
            name="bluetooth-discovery",
            fn=lambda: (
                backend.start_discovery(adapter_path=adapter.path)
                if self._request_is_current(backend, generation)
                else False
            ),
            on_result=completed,
        )

    def _request_is_current(self, backend: BluetoothBackend, generation: int) -> bool:
        return (
            not self._stopped
            and backend is self._backend
            and generation == self._poll_generation
        )

    def _run_async(self, action: Callable[[], bool]) -> None:
        backend = self._backend
        generation = self._poll_generation

        def task() -> BluetoothState | None:
            if not self._request_is_current(backend, generation):
                return None
            try:
                action()
            except Exception as exc:
                log.bind(action="async_action").debug(
                    "Bluetooth action failed: %s",
                    exc,
                )
            return backend.get_state(active_adapter_path=self._active_adapter_path)

        self._worker.run(
            name="bluetooth-action",
            fn=task,
            on_result=lambda state: (
                self._on_poll_result(state)
                if state is not None and self._request_is_current(backend, generation)
                else False
            ),
        )

    def _on_refresh_result(self, state: BluetoothState) -> bool:
        return self._on_poll_result(state=state)

    def _on_discovery_result(self, adapter_path: str, started: bool) -> bool:
        if not started:
            return False
        self._local_discovery_paths.add(adapter_path)
        if (
            self._stopped
            or not self._continuous_discovery
            or adapter_path != self._active_adapter_path
        ):
            self._worker.run(
                name="bluetooth-discovery-cleanup",
                fn=lambda: self._stop_discovery_path(adapter_path, quiet=True),
                on_result=lambda stopped: self._ensure_discovery() if stopped else None,
            )
        return False

    def _connect_device(self, device: BluetoothDeviceState) -> bool:
        backend = self._backend
        if device.paired:
            return backend.connect_device(device_path=device.path)
        paired = backend.pair_device(
            device_path=device.path,
            address=device.address,
            timeout_s=PAIR_TIMEOUT_S,
        )
        if not paired or self._stopped or backend is not self._backend:
            return False
        return backend.connect_device(device_path=device.path)

    def _on_select_adapter(self, widget: Gtk.RadioMenuItem, adapter_path: str) -> None:
        if not widget.get_active():
            return
        previous_path = self._active_adapter_path
        self._active_adapter_path = adapter_path
        self._save_prefs()
        if (
            previous_path != adapter_path
            and previous_path in self._local_discovery_paths
        ):
            self._worker.run(
                name="bluetooth-adapter-cleanup",
                fn=lambda: self._stop_discovery_path(previous_path, quiet=True),
                on_result=lambda _ok: (
                    self._refresh_now() if not self._stopped else None
                ),
            )
        else:
            self._refresh_now()

    def _on_power_toggled(self, widget: Gtk.CheckMenuItem) -> None:
        adapter = self._active_adapter()
        if adapter is None:
            return
        if self._power_transition_in_progress:
            # Revert the toggle while transition is in progress so the menu state
            # remains aligned with the backend's current state.
            widget.set_active(adapter.powered)
            return
        target = widget.get_active()
        if target == adapter.powered:
            return
        self._set_power_async(target=target)

    def _set_power_async(self, *, target: bool) -> None:
        if self._power_transition_in_progress:
            return
        adapter = self._active_adapter()
        if adapter is None:
            return
        adapter_path = adapter.path
        backend = self._backend
        generation = self._poll_generation
        discovery_paths = self._local_discovery_paths
        self._power_transition_in_progress = True
        self._power_transition_adapter_path = adapter_path
        if not target:
            # Prevent immediate keepalive restart while powering off.
            self._suppress_discovery_until = (
                time.monotonic() + DISCOVERY_SUPPRESS_AFTER_POWER_OFF_S
            )

        def task() -> tuple[str, bool, bool, BluetoothState] | None:
            if not self._request_is_current(backend, generation):
                return None
            if not target:
                for path in tuple(discovery_paths):
                    if backend.stop_discovery(path, quiet=True):
                        discovery_paths.discard(path)
            ok = backend.set_adapter_power(
                adapter_path=adapter_path,
                powered=target,
            )
            state = backend.get_state(active_adapter_path=self._active_adapter_path)
            return adapter_path, target, ok, state

        def completed(payload: tuple[str, bool, bool, BluetoothState] | None) -> bool:
            if payload is None or not self._request_is_current(backend, generation):
                return False
            return self._on_power_result(*payload)

        self._worker.run(
            name="bluetooth-power",
            fn=task,
            on_result=completed,
        )

    def _on_power_result(
        self,
        adapter_path: str,
        target: bool,
        success: bool,
        state: BluetoothState,
    ) -> bool:
        self._power_transition_in_progress = False
        self._power_transition_adapter_path = ""
        self._poll_generation += 1
        self._state = state
        self._sync_selected_adapter()
        if success:
            self._action_error = ""
            if not target:
                self._local_discovery_paths.discard(adapter_path)
        elif not target:
            adapter = self._active_adapter()
            if adapter is not None and adapter.discovering:
                self._action_error = (
                    "Power off blocked: another Bluetooth app is scanning."
                )
            else:
                self._action_error = "Power off failed."
        else:
            self._action_error = "Power on failed."
        self.present()
        return False

    def _on_continuous_discovery_toggled(self, widget: Gtk.CheckMenuItem) -> None:
        self._continuous_discovery = widget.get_active()
        self._save_prefs()
        adapter = self._active_adapter()
        if adapter is None:
            return
        if self._continuous_discovery:
            self._ensure_discovery()
        else:
            self._run_async(lambda: self._stop_local_discovery(quiet=True))

    def _stop_local_discovery(self, *, quiet: bool = True) -> bool:
        """Stop discovery only when this applet owns a discovery session."""
        stopped = True
        for adapter_path in tuple(self._local_discovery_paths):
            if not self._stop_discovery_path(adapter_path, quiet=quiet):
                stopped = False
        return stopped

    def _stop_discovery_path(self, adapter_path: str, *, quiet: bool) -> bool:
        stopped = self._backend.stop_discovery(adapter_path=adapter_path, quiet=quiet)
        if stopped:
            self._local_discovery_paths.discard(adapter_path)
        return stopped

    def _save_prefs(self) -> None:
        self._prefs_dirty = False
        self.save_prefs(
            {
                "active_adapter_path": self._active_adapter_path,
                "continuous_discovery": self._continuous_discovery,
                "recent_connections": self._trim_recent_connections(),
            }
        )

    def _seed_recent_connections(self) -> None:
        now = time.time()
        changed = False
        for address in self._known_connected_addresses:
            if not address or address in self._recent_connections:
                continue
            self._recent_connections[address] = now
            changed = True
        if changed:
            self._prefs_dirty = True

    def _record_recent_connections(self, state: BluetoothState) -> None:
        current_connected = self._connected_addresses(state)
        newly_connected = current_connected - self._known_connected_addresses
        self._known_connected_addresses = current_connected
        if not newly_connected:
            return
        now = time.time()
        for address in newly_connected:
            if address:
                self._recent_connections[address] = now
        self._save_prefs()

    def _recent_devices(self, *, adapter_path: str) -> list[BluetoothDeviceState]:
        recent_devices = [
            device
            for device in self._state.devices
            if device.adapter_path == adapter_path
            and device.paired
            and device.address in self._recent_connections
        ]
        recent_devices.sort(
            key=lambda device: (
                -self._recent_connections.get(device.address, 0.0),
                not device.connected,
                (device.alias or device.name or device.address).casefold(),
            )
        )
        return recent_devices[:RECENT_CONNECTION_LIMIT]

    def _trim_recent_connections(self) -> dict[str, float]:
        items = sorted(
            self._recent_connections.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:RECENT_CONNECTION_LIMIT]
        self._recent_connections = dict(items)
        return dict(items)

    @staticmethod
    def _connected_addresses(state: BluetoothState) -> set[str]:
        return {device.address for device in state.devices if device.connected}


def _as_pref_bool(value: object, *, default: bool) -> bool:
    """Parse bool-like config values while preserving boolean semantics."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return default
    if isinstance(value, int | float):
        return bool(value)
    return default
