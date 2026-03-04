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

import threading
import time
from typing import TYPE_CHECKING, Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from docking.applets.base import Applet
from docking.applets.identity import AppletId
from docking.log import get_logger, with_context

from .render import create_bluetooth_icon
from .state import (
    BluetoothAdapterState,
    BluetoothDeviceState,
    BluetoothState,
    BluezBackend,
    adapter_from_state,
    build_tooltip,
    connected_count,
    device_menu_label,
    unavailable_state,
)

if TYPE_CHECKING:
    from docking.core.config import Config

_log = with_context(get_logger(name="bluetooth"), applet_id=str(AppletId.BLUETOOTH))

POLL_INTERVAL_S = 2
DISCOVERY_KEEPALIVE_S = 8
PAIR_TIMEOUT_S = 20
DISCOVERY_SUPPRESS_AFTER_POWER_OFF_S = 4.0


class BluetoothApplet(Applet):
    """Bluetooth quick manager with multi-adapter support."""

    id = AppletId.BLUETOOTH
    name = "Bluetooth"
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
        # Optional user-facing line appended to tooltip when an action fails.
        self._action_error: str = ""

        if config:
            prefs = config.applet_prefs.get(AppletId.BLUETOOTH, {})
            self._active_adapter_path = str(prefs.get("active_adapter_path", "") or "")
            self._continuous_discovery = _as_pref_bool(
                prefs.get("continuous_discovery", True),
                default=True,
            )

        self._state = self._backend.get_state(
            active_adapter_path=self._active_adapter_path
        )
        self._sync_selected_adapter(persist=False)
        super().__init__(icon_size=icon_size, config=config)

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
        self._poll_id = GLib.timeout_add_seconds(POLL_INTERVAL_S, self._tick)
        self._discovery_id = GLib.timeout_add_seconds(
            DISCOVERY_KEEPALIVE_S,
            self._discovery_tick,
        )
        self._ensure_discovery()

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
            placeholder = Gtk.MenuItem(label="Bluetooth unavailable")
            placeholder.set_sensitive(False)
            return [placeholder]

        items: list[Gtk.MenuItem] = []
        adapter = self._active_adapter()
        if adapter is None:
            placeholder = Gtk.MenuItem(label="No Bluetooth adapter")
            placeholder.set_sensitive(False)
            return [placeholder]

        items.append(self._make_header(label="General"))

        status = Gtk.MenuItem(
            label=(
                f"{adapter.alias} ({'On' if adapter.powered else 'Off'})"
                f" - Connected {connected_count(self._state)}"
            )
        )
        status.set_sensitive(False)
        items.append(status)

        power_toggle = Gtk.CheckMenuItem(label="Bluetooth On")
        power_toggle.set_active(adapter.powered)
        power_toggle.connect("toggled", self._on_power_toggled)
        items.append(power_toggle)

        discovery_toggle = Gtk.CheckMenuItem(label="Continuous Discovery")
        discovery_toggle.set_active(self._continuous_discovery)
        discovery_toggle.connect("toggled", self._on_continuous_discovery_toggled)
        items.append(discovery_toggle)

        if len(self._state.adapters) > 1:
            items.append(self._build_adapter_submenu())

        items.append(Gtk.SeparatorMenuItem())
        items.extend(self._build_devices_sections(adapter_path=adapter.path))

        refresh_item = Gtk.MenuItem(label="Refresh Now")
        refresh_item.connect("activate", lambda _w: self._refresh_now())
        items.append(Gtk.SeparatorMenuItem())
        items.append(refresh_item)
        return items

    def _build_adapter_submenu(self) -> Gtk.MenuItem:
        item = Gtk.MenuItem(label="Adapter")
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
            ("Connected Devices", connected),
            ("Paired Devices", paired),
            ("Discovered Devices", discovered),
        ]
        for index, (title, members) in enumerate(groups):
            items.extend(self._device_group(title, members))
            if index < len(groups) - 1:
                items.append(Gtk.SeparatorMenuItem())
        return items

    def _device_group(
        self,
        title: str,
        devices: list[BluetoothDeviceState],
    ) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []
        items.append(self._make_header(label=title))

        if not devices:
            return items

        for device in devices:
            label = device_menu_label(device)
            device_item = Gtk.MenuItem(label=label)
            submenu = Gtk.Menu()

            if device.connected:
                disconnect = Gtk.MenuItem(label="Disconnect")
                disconnect.connect(
                    "activate",
                    lambda _w, p=device.path: self._run_async(
                        lambda: self._backend.disconnect_device(p)
                    ),
                )
                submenu.append(disconnect)
            else:
                connect = Gtk.MenuItem(label="Connect")
                connect.connect(
                    "activate",
                    lambda _w, d=device: self._run_async(
                        lambda: self._connect_device(device=d)
                    ),
                )
                submenu.append(connect)

            if not device.paired:
                pair = Gtk.MenuItem(label="Pair")
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
                remove = Gtk.MenuItem(label="Remove Pairing")
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

            trust = Gtk.CheckMenuItem(label="Trusted")
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
                battery = Gtk.MenuItem(label=f"Battery: {device.battery_percent}%")
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
        threading.Thread(target=self._poll_worker, daemon=True).start()
        return True

    def _discovery_tick(self) -> bool:
        self._ensure_discovery()
        return True

    def _poll_worker(self) -> None:
        state = self._backend.get_state(active_adapter_path=self._active_adapter_path)
        GLib.idle_add(self._on_poll_result, state)

    def _on_poll_result(self, state: BluetoothState) -> bool:
        self._state = state
        self._sync_selected_adapter()
        adapter = self._active_adapter()
        if adapter is None or not adapter.discovering:
            # Adapter no longer discovering => local discovery session cannot
            # still be active.
            self._local_discovery_active = False
        self._ensure_discovery()
        self.refresh_presentation()
        return False

    def _refresh_now(self) -> None:
        self._on_poll_result(
            self._backend.get_state(active_adapter_path=self._active_adapter_path)
        )

    def _sync_selected_adapter(self, persist: bool = True) -> None:
        selected = adapter_from_state(
            state=self._state,
            preferred_path=self._active_adapter_path,
        )
        if selected is not None:
            self._active_adapter_path = selected
            if persist:
                self.save_prefs(
                    {
                        "active_adapter_path": self._active_adapter_path,
                        "continuous_discovery": self._continuous_discovery,
                    }
                )

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
        if self._backend.start_discovery(adapter.path):
            self._local_discovery_active = True

    def _run_async(self, action: Callable[[], bool]) -> None:
        def worker() -> None:
            try:
                action()
            except Exception as exc:
                _log.bind(action="async_action").debug(
                    "Bluetooth action failed: %s",
                    exc,
                )
            finally:
                state = self._backend.get_state(
                    active_adapter_path=self._active_adapter_path
                )
                GLib.idle_add(self._on_poll_result, state)

        threading.Thread(target=worker, daemon=True).start()

    def _connect_device(self, device: BluetoothDeviceState) -> bool:
        if device.paired:
            return self._backend.connect_device(device.path)
        paired = self._backend.pair_device(
            device.path,
            address=device.address,
            timeout_s=PAIR_TIMEOUT_S,
        )
        if not paired:
            return False
        return self._backend.connect_device(device.path)

    def _on_select_adapter(self, widget: Gtk.RadioMenuItem, adapter_path: str) -> None:
        if not widget.get_active():
            return
        self._active_adapter_path = adapter_path
        self.save_prefs(
            {
                "active_adapter_path": self._active_adapter_path,
                "continuous_discovery": self._continuous_discovery,
            }
        )
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

        def worker() -> None:
            if not target:
                self._stop_local_discovery(quiet=True)
            ok = self._backend.set_adapter_power(adapter.path, target)
            state = self._backend.get_state(
                active_adapter_path=self._active_adapter_path
            )
            GLib.idle_add(self._on_power_result, target, ok, state)

        threading.Thread(target=worker, daemon=True).start()

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
        self.refresh_presentation()
        return False

    def _on_continuous_discovery_toggled(self, widget: Gtk.CheckMenuItem) -> None:
        self._continuous_discovery = widget.get_active()
        self.save_prefs(
            {
                "active_adapter_path": self._active_adapter_path,
                "continuous_discovery": self._continuous_discovery,
            }
        )
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
        stopped = self._backend.stop_discovery(adapter.path, quiet=quiet)
        if stopped:
            self._local_discovery_active = False
        return stopped


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
    if isinstance(value, (int, float)):
        return bool(value)
    return default
