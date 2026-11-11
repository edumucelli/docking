"""Collect the application's enabled actions into one portal session."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from docking.search.services.global_shortcuts import (
    ActivationCallback,
    GlobalShortcutActivation,
    GlobalShortcutService,
    GlobalShortcutSpec,
    GlobalShortcutsState,
    GlobalShortcutsStatus,
    StatusCallback,
)

from .shortcuts import GlobalShortcutsService


class PortalShortcutSession:
    def __init__(self) -> None:
        self._registrations: dict[str, PortalShortcutRegistration] = {}
        self._service: GlobalShortcutsService | None = None
        self._generation = 0
        self._source = 0
        self._queued = False

    def registration(
        self,
        *,
        app_id: str,
        spec: GlobalShortcutSpec,
        on_activated: ActivationCallback,
        on_status_changed: StatusCallback,
        schedule_idle: Callable[..., int],
        remove_source: Callable[[int], bool],
    ) -> PortalShortcutRegistration:
        return PortalShortcutRegistration(
            self,
            app_id,
            spec,
            on_activated,
            on_status_changed,
            schedule_idle,
            remove_source,
        )

    def changed(self, registration: PortalShortcutRegistration) -> None:
        self._generation += 1
        if self._source:
            registration.remove_source(self._source)
            self._source = 0
        if not self._registrations:
            self._rebind()
            return
        self._queued = True
        source = registration.schedule_idle(self._rebind)
        self._source = source if self._queued else 0

    def _rebind(self) -> bool:
        self._queued = False
        self._source = 0
        self._generation += 1
        generation = self._generation
        old, self._service = self._service, None
        if old is not None:
            old.stop()
        registrations = tuple(self._registrations.values())
        if not registrations:
            return False

        def status_changed(status: GlobalShortcutsStatus) -> None:
            if generation != self._generation:
                return
            bindings = {binding.shortcut_id: binding for binding in status.bindings}
            if status.binding is not None:
                bindings[status.binding.shortcut_id] = status.binding
            for registration in tuple(self._registrations.values()):
                binding = bindings.get(registration.spec.shortcut_id)
                state = status.state
                if state in {
                    GlobalShortcutsState.BOUND,
                    GlobalShortcutsState.REASSIGNED,
                    GlobalShortcutsState.DENIED,
                }:
                    state = (
                        GlobalShortcutsState.BOUND
                        if binding
                        else GlobalShortcutsState.DENIED
                    )
                    if (
                        binding
                        and registration.status.binding
                        and registration.status.binding != binding
                    ):
                        state = GlobalShortcutsState.REASSIGNED
                registration.publish(
                    replace(status, state=state, binding=binding, bindings=())
                )

        def activated(activation: GlobalShortcutActivation) -> None:
            if generation == self._generation:
                registration = self._registrations.get(activation.shortcut_id)
                if registration is not None:
                    registration.on_activated(activation)

        self._service = GlobalShortcutsService(
            app_id=registrations[0].app_id,
            preferred_trigger=registrations[0].spec.preferred_trigger,
            shortcuts=tuple(item.spec for item in registrations),
            on_activated=activated,
            on_status_changed=status_changed,
        )
        self._service.start()
        return False


class PortalShortcutRegistration(GlobalShortcutService):
    def __init__(
        self,
        session: PortalShortcutSession,
        app_id: str,
        spec: GlobalShortcutSpec,
        on_activated: ActivationCallback,
        on_status_changed: StatusCallback,
        schedule_idle: Callable[..., int],
        remove_source: Callable[[int], bool],
    ) -> None:
        self._session = session
        self.app_id = app_id
        self.spec = spec
        self.on_activated = on_activated
        self._on_status_changed = on_status_changed
        self.schedule_idle = schedule_idle
        self.remove_source = remove_source
        self._status = GlobalShortcutsStatus(GlobalShortcutsState.STOPPED)

    @property
    def status(self) -> GlobalShortcutsStatus:
        return self._status

    def publish(self, status: GlobalShortcutsStatus) -> None:
        if status != self._status:
            self._status = status
            self._on_status_changed(status)

    def start(self) -> None:
        if self._session._registrations.get(self.spec.shortcut_id) is self:
            return
        self._session._registrations[self.spec.shortcut_id] = self
        self.publish(GlobalShortcutsStatus(GlobalShortcutsState.STARTING))
        self._session.changed(self)

    def stop(self) -> None:
        if self._session._registrations.get(self.spec.shortcut_id) is self:
            del self._session._registrations[self.spec.shortcut_id]
            self._session.changed(self)
        self.publish(GlobalShortcutsStatus(GlobalShortcutsState.STOPPED))

    def list_shortcuts(self) -> bool:
        service = self._session._service
        return service.list_shortcuts() if service is not None else False
