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

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from docking.applets.base import Applet
from docking.applets.bluetooth import meta
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

from .render import create_bluetooth_icon
from .state import (
    BluetoothAdapterState,
    BluetoothDeviceState,
    BluetoothState,
    BluezBackend,
    adapter_from_state,
    adapters_command,
    build_tooltip,
    connected_count,
    device_menu_label,
    devices_command,
    local_services_command,
    open_adapters,
    open_devices,
    open_local_services,
    open_send_files,
    send_files_command,
    unavailable_state,
)

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="bluetooth"), applet_id=meta.id)

POLL_INTERVAL_S = 2
DISCOVERY_KEEPALIVE_S = 8
PAIR_TIMEOUT_S = 20
DISCOVERY_SUPPRESS_AFTER_POWER_OFF_S = 4.0
RECENT_CONNECTION_LIMIT = 6


class BluetoothApplet(Applet):
    """Bluetooth quick manager with multi-adapter support."""

    id = meta.id
    name = _("Bluetooth")
    icon_name = "bluetooth-active-symbolic"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._backend = BluezBackend()
        self._state: BluetoothState = unavailable_state()
        self._poll_id: int = 0
        self._discovery_id: int = 0
        self._active_adapter_path: str = ""
        self._continuous_discovery = True
        self._suppress_discovery_until: float = 0.0
        # True only when this applet successfully started discovery.
        self._local_discovery_active = False
        # Serializes power transitions. Prevents repeated calls when users click
        # quickly or when menu toggle events fire while the backend is busy.
        self._power_transition_in_progress = False
        self._worker = BackgroundWorker(logger=log)
        # Optional user-facing line appended to tooltip when an action fails.
        self._action_error: str = ""
        self._recent_connections: dict[str, float] = {}
        self._prefs_dirty = False

        if config:
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
        super().__init__(icon_size=icon_size, config=config)
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
        super().start(notify=notify)
        self._refresh_now()
        self._poll_id = GLib.timeout_add_seconds(POLL_INTERVAL_S, self._tick)
        self._discovery_id = GLib.timeout_add_seconds(
            DISCOVERY_KEEPALIVE_S,
            self._discovery_tick,
        )

    def stop(self) -> None:
        # Only try to stop discovery if we own a local discovery session.
        # This avoids noisy NotReady calls when scanning is externally owned.
        self._stop_local_discovery(quiet=True)
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
            placeholder = Gtk.MenuItem(label=_("Bluetooth unavailable"))
            placeholder.set_sensitive(False)
            return [placeholder]

        items: list[Gtk.MenuItem] = []
        adapter = self._active_adapter()
        if adapter is None:
            placeholder = Gtk.MenuItem(label=_("No Bluetooth adapter"))
            placeholder.set_sensitive(False)
            return [placeholder]

        items.extend(self._build_quick_actions(adapter=adapter))
        if items:
            items.append(Gtk.SeparatorMenuItem())

        powered_label = _("On") if adapter.powered else _("Off")
        connected_devices = [
            d
            for d in self._state.devices
            if d.adapter_path == adapter.path and d.connected
        ]

        status = Gtk.MenuItem(
            label=_("{alias} ({powered}) - Connected {connected}").format(
                alias=adapter.alias,
                powered=powered_label,
                connected=len(connected_devices),
            )
        )
        status.set_sensitive(False)
        items.append(status)

        discovery_toggle = Gtk.CheckMenuItem(label=_("Continuous Discovery"))
        discovery_toggle.set_active(self._continuous_discovery)
        discovery_toggle.connect("toggled", self._on_continuous_discovery_toggled)
        items.append(discovery_toggle)

        if len(self._state.adapters) > 1:
            items.append(self._build_adapter_submenu())

        items.append(Gtk.SeparatorMenuItem())
        items.extend(self._build_devices_sections(adapter_path=adapter.path))

        refresh_item = Gtk.MenuItem(label=_("Refresh Now"))
        refresh_item.connect("activate", lambda _w: self._refresh_now())
        items.append(Gtk.SeparatorMenuItem())
        items.append(refresh_item)
        return items

    def _build_quick_actions(
        self,
        *,
        adapter: BluetoothAdapterState,
    ) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []

        power_label = (
            _("Turn Bluetooth Off") if adapter.powered else _("Turn Bluetooth On")
        )
        power_item = Gtk.MenuItem(label=power_label)
        power_item.connect(
            "activate",
            lambda _w: self._set_power_async(target=not adapter.powered),
        )
        items.append(power_item)

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
            items.append(disconnect_item)

        send_cmd = send_files_command()
        if send_cmd is not None:
            send_item = Gtk.MenuItem(label=_("Send Files to Device..."))
            send_item.connect("activate", lambda _w: open_send_files())
            items.append(send_item)

        items.append(self._build_recent_connections_submenu(adapter_path=adapter.path))

        devices_cmd = devices_command()
        if devices_cmd is not None:
            devices_item = Gtk.MenuItem(label=_("Devices..."))
            devices_item.connect("activate", lambda _w: open_devices())
            items.append(devices_item)

        adapters_cmd = adapters_command()
        if adapters_cmd is not None:
            adapters_item = Gtk.MenuItem(label=_("Adapters..."))
            adapters_item.connect("activate", lambda _w: open_adapters())
            items.append(adapters_item)

        services_cmd = local_services_command()
        if services_cmd is not None:
            services_item = Gtk.MenuItem(label=_("Local Services..."))
            services_item.connect("activate", lambda _w: open_local_services())
            items.append(services_item)

        return items

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
        item = Gtk.MenuItem(label=_("Adapter"))
        submenu = Gtk.Menu()
        first: Gtk.RadioMenuItem | None = None

        for adapter in self._state.adapters:
            label = adapter.alias or adapter.name
            radio = Gtk.RadioMenuItem(label=label)
            if first is None:
                first = radio
            else:
                radio.join_group(first)
            radio.set_active(adapter.path == self._active_adapter_path)
            radio.connect("toggled", self._on_select_adapter, adapter.path)
            submenu.append(radio)

        item.set_submenu(submenu)
        return item

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

            if device.paired:
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
        self._worker.run(
            name="bluetooth-poll",
            fn=lambda: self._backend.get_state(
                active_adapter_path=self._active_adapter_path
            ),
            on_result=self._on_poll_result,
        )
        return True

    def _discovery_tick(self) -> bool:
        self._ensure_discovery()
        return True

    def _on_poll_result(self, state: BluetoothState) -> bool:
        self._state = state
        self._sync_selected_adapter()
        self._record_recent_connections(state)
        adapter = self._active_adapter()
        if adapter is None or not adapter.discovering:
            # Adapter no longer discovering => local discovery session cannot
            # still be active.
            self._local_discovery_active = False
        self._ensure_discovery()
        self.present()
        return False

    def _refresh_now(self) -> None:
        self._worker.run_guarded(
            key="refresh",
            name="bluetooth-refresh",
            fn=lambda: self._backend.get_state(
                active_adapter_path=self._active_adapter_path
            ),
            on_result=self._on_refresh_result,
        )

    def _sync_selected_adapter(self, persist: bool = True) -> None:
        selected = adapter_from_state(
            state=self._state,
            preferred_path=self._active_adapter_path,
        )
        if selected is not None:
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
        if not self._continuous_discovery:
            return
        adapter = self._active_adapter()
        if adapter is None or not adapter.powered or adapter.discovering:
            return
        if time.monotonic() < self._suppress_discovery_until:
            return
        self._worker.run_guarded(
            key="discovery",
            name="bluetooth-discovery",
            fn=lambda: self._backend.start_discovery(adapter_path=adapter.path),
            on_result=self._on_discovery_result,
        )

    def _run_async(self, action: Callable[[], bool]) -> None:
        def task() -> BluetoothState:
            try:
                action()
            except Exception as exc:
                log.bind(action="async_action").debug(
                    "Bluetooth action failed: %s",
                    exc,
                )
            return self._backend.get_state(
                active_adapter_path=self._active_adapter_path
            )

        self._worker.run(
            name="bluetooth-action",
            fn=task,
            on_result=self._on_poll_result,
        )

    def _on_refresh_result(self, state: BluetoothState) -> bool:
        return self._on_poll_result(state=state)

    def _on_discovery_result(self, started: bool) -> bool:
        if started:
            self._local_discovery_active = True
        return False

    def _connect_device(self, device: BluetoothDeviceState) -> bool:
        if device.paired:
            return self._backend.connect_device(device_path=device.path)
        paired = self._backend.pair_device(
            device_path=device.path,
            address=device.address,
            timeout_s=PAIR_TIMEOUT_S,
        )
        if not paired:
            return False
        return self._backend.connect_device(device_path=device.path)

    def _on_select_adapter(self, widget: Gtk.RadioMenuItem, adapter_path: str) -> None:
        if not widget.get_active():
            return
        self._active_adapter_path = adapter_path
        self._save_prefs()
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
        self._power_transition_in_progress = True
        if not target:
            # Prevent immediate keepalive restart while powering off.
            self._suppress_discovery_until = (
                time.monotonic() + DISCOVERY_SUPPRESS_AFTER_POWER_OFF_S
            )

        def task() -> tuple[bool, bool, BluetoothState]:
            if not target:
                self._stop_local_discovery(quiet=True)
            ok = self._backend.set_adapter_power(
                adapter_path=adapter.path,
                powered=target,
            )
            state = self._backend.get_state(
                active_adapter_path=self._active_adapter_path
            )
            return target, ok, state

        self._worker.run(
            name="bluetooth-power",
            fn=task,
            on_result=lambda payload: self._on_power_result(*payload),
        )

    def _on_power_result(
        self,
        target: bool,
        success: bool,
        state: BluetoothState,
    ) -> bool:
        self._power_transition_in_progress = False
        self._state = state
        self._sync_selected_adapter()
        if success:
            self._action_error = ""
            if not target:
                self._local_discovery_active = False
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
        adapter = self._active_adapter()
        if adapter is None or not self._local_discovery_active:
            return True
        stopped = self._backend.stop_discovery(adapter_path=adapter.path, quiet=quiet)
        if stopped:
            self._local_discovery_active = False
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
