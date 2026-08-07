"""Tests for the registry-backed ApplicationCatalog compatibility adapter."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from docking.platform.applications.registry import ApplicationRegistry
from docking.search.services.application_catalog import ApplicationCatalog


class _Icon:
    def __init__(self, value: str) -> None:
        self._value = value

    def to_string(self) -> str:
        return self._value


class _Application:
    def __init__(
        self,
        desktop_id: str,
        name: str,
        *,
        commandline: str = "example",
        icon: str = "example",
    ) -> None:
        self.desktop_id = desktop_id
        self.name = name
        self.commandline = commandline
        self.icon = icon

    def get_id(self) -> str:
        return self.desktop_id

    def get_is_hidden(self) -> bool:
        return False

    def get_nodisplay(self) -> bool:
        return False

    def get_filename(self) -> str:
        return ""

    def get_commandline(self) -> str:
        return self.commandline

    def get_startup_wm_class(self) -> str:
        return self.desktop_id.removesuffix(".desktop")

    def get_icon(self) -> _Icon:
        return _Icon(self.icon)

    def get_display_name(self) -> str:
        return self.name

    def get_generic_name(self) -> str:
        return ""

    def get_description(self) -> str:
        return f"{self.name} description"

    def get_categories(self) -> str:
        return "Utility;"

    def get_keywords(self) -> tuple[str, ...]:
        return ("utility",)

    def list_actions(self) -> tuple[str, ...]:
        return ()


def _registry(source: list[_Application]) -> ApplicationRegistry:
    return ApplicationRegistry(
        application_source=lambda: tuple(source),
        desktop_directories_source=lambda: (),
    )


class _BorrowedRegistry:
    def __init__(self, registry: ApplicationRegistry) -> None:
        self._registry = registry
        self.listeners: list[Callable[[], None]] = []
        self.start_calls = 0
        self.stop_calls = 0
        self.refresh_calls = 0

    def snapshot(self):
        return self._registry.snapshot()

    def subscribe(self, callback: Callable[[], None]):
        self.listeners.append(callback)
        subscribed = True

        def unsubscribe() -> None:
            nonlocal subscribed
            if not subscribed:
                return
            subscribed = False
            self.listeners.remove(callback)

        return unsubscribe

    def start(self) -> None:
        self.start_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1

    def refresh(self) -> bool:
        self.refresh_calls += 1
        changed = self._registry.refresh()
        if changed:
            self.publish()
        return changed

    def publish(self) -> None:
        for callback in tuple(self.listeners):
            callback()


def test_owned_refresh_projects_registry_sources_and_tracks_projection_generation():
    source = [
        _Application("zulu.desktop", "Zulu"),
        _Application("alpha.desktop", "Alpha"),
    ]
    catalog = ApplicationCatalog(
        application_source=lambda: tuple(source),
        desktop_directories_source=lambda: (),
    )
    notifications: list[int] = []
    catalog.subscribe(lambda: notifications.append(catalog.generation))

    assert catalog.refresh() is True
    assert [application.desktop_id for application in catalog.applications] == [
        "alpha.desktop",
        "zulu.desktop",
    ]
    assert catalog.get("alpha.desktop") is catalog.applications[0]
    assert catalog.generation == 1
    assert notifications == [1]

    assert catalog.refresh() is False
    assert catalog.generation == 1

    source[0].name = "Aardvark"
    assert catalog.refresh() is True
    assert catalog.generation == 2
    assert catalog.applications[0].name == "Aardvark"


def test_owned_failed_initial_refresh_does_not_publish_or_notify():
    def fail() -> tuple[_Application, ...]:
        raise RuntimeError("initial discovery failed")

    catalog = ApplicationCatalog(
        application_source=fail,
        desktop_directories_source=lambda: (),
    )
    notifications: list[int] = []
    catalog.subscribe(lambda: notifications.append(catalog.generation))

    assert catalog.refresh() is False
    assert catalog.generation == 0
    assert catalog.snapshot() == ()
    assert notifications == []


def test_owned_successful_empty_initial_refresh_publishes_once():
    catalog = ApplicationCatalog(
        application_source=tuple,
        desktop_directories_source=tuple,
    )
    notifications: list[int] = []
    catalog.subscribe(lambda: notifications.append(catalog.generation))

    assert catalog.refresh() is True
    assert catalog.generation == 1
    assert catalog.snapshot() == ()
    assert notifications == [1]
    assert catalog.refresh() is False
    assert notifications == [1]


def test_owned_start_and_stop_own_exactly_one_registry(monkeypatch):
    catalog = ApplicationCatalog(
        application_source=tuple,
        desktop_directories_source=tuple,
    )
    registry = catalog.registry
    start = registry.start
    stop = registry.stop
    start_calls = 0
    stop_calls = 0

    def counted_start() -> None:
        nonlocal start_calls
        start_calls += 1
        start()

    def counted_stop() -> None:
        nonlocal stop_calls
        stop_calls += 1
        stop()

    monkeypatch.setattr(registry, "start", counted_start)
    monkeypatch.setattr(registry, "stop", counted_stop)

    catalog.start()
    catalog.start()
    assert catalog.started is True
    assert start_calls == 1

    catalog.stop()
    catalog.stop()
    assert catalog.started is False
    assert stop_calls == 1


def test_borrowed_mode_subscribes_without_mutating_registry_lifecycle():
    source = [_Application("example.desktop", "Example")]
    canonical = _registry(source)
    assert canonical.refresh() is True
    borrowed = _BorrowedRegistry(canonical)
    catalog = ApplicationCatalog(registry=borrowed)  # type: ignore[arg-type]

    catalog.start()
    catalog.start()

    assert borrowed.start_calls == 0
    assert len(borrowed.listeners) == 1
    assert catalog.applications[0].name == "Example"

    source[0].name = "Changed"
    assert canonical.refresh() is True
    borrowed.publish()
    assert catalog.applications[0].name == "Changed"

    catalog.stop()
    catalog.stop()
    assert borrowed.stop_calls == 0
    assert borrowed.listeners == []


def test_borrowed_refresh_synchronously_refreshes_and_notifies_once():
    source = [_Application("example.desktop", "Example")]
    canonical = _registry(source)
    canonical.refresh()
    borrowed = _BorrowedRegistry(canonical)
    catalog = ApplicationCatalog(registry=borrowed)  # type: ignore[arg-type]
    notifications: list[int] = []
    catalog.subscribe(lambda: notifications.append(catalog.generation))
    catalog.start()
    notifications.clear()

    source[0].name = "Fresh"
    assert catalog.refresh() is True
    assert borrowed.refresh_calls == 1
    assert catalog.applications[0].name == "Fresh"
    assert notifications == [2]

    assert catalog.refresh() is False
    assert borrowed.refresh_calls == 2
    assert notifications == [2]


def test_borrowed_failed_refresh_preserves_published_projection():
    def stable() -> tuple[_Application, ...]:
        return (_Application("stable.desktop", "Stable"),)

    source: Callable[[], tuple[_Application, ...]] = stable
    canonical = ApplicationRegistry(
        application_source=lambda: source(),
        desktop_directories_source=lambda: (),
    )
    canonical.refresh()
    borrowed = _BorrowedRegistry(canonical)
    catalog = ApplicationCatalog(registry=borrowed)  # type: ignore[arg-type]
    notifications: list[int] = []
    catalog.subscribe(lambda: notifications.append(catalog.generation))
    catalog.start()
    notifications.clear()
    published = catalog.snapshot()
    generation = catalog.generation

    def fail() -> tuple[_Application, ...]:
        raise RuntimeError("discovery failed")

    source = fail
    assert catalog.refresh() is False
    assert borrowed.refresh_calls == 1
    assert catalog.snapshot() is published
    assert catalog.generation == generation
    assert notifications == []


def test_projection_generation_ignores_nonsearch_registry_changes():
    source = [_Application("example.desktop", "Example", commandline="first")]
    registry = _registry(source)
    registry.refresh()
    catalog = ApplicationCatalog(registry=registry)
    catalog.start()
    generation = catalog.generation
    published = catalog.snapshot()

    source[0].commandline = "second"
    assert registry.refresh() is True

    assert catalog.generation == generation
    assert catalog.snapshot() is published


def test_listener_unsubscribe_is_idempotent_and_failures_are_isolated():
    source = [_Application("example.desktop", "Example")]
    catalog = ApplicationCatalog(
        application_source=lambda: source,
        desktop_directories_source=lambda: (),
    )
    notifications: list[int] = []
    catalog.subscribe(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    unsubscribe = catalog.add_listener(lambda: notifications.append(catalog.generation))

    assert catalog.refresh() is True
    assert notifications == [1]

    unsubscribe()
    unsubscribe()
    source[0].name = "Changed"
    assert catalog.refresh() is True
    assert notifications == [1]


def test_owned_source_failure_preserves_published_projection():
    def stable() -> tuple[_Application, ...]:
        return (_Application("stable.desktop", "Stable"),)

    source: Callable[[], tuple[_Application, ...]] = stable
    catalog = ApplicationCatalog(
        application_source=lambda: source(),
        desktop_directories_source=lambda: (),
    )
    assert catalog.refresh() is True
    published = catalog.snapshot()
    generation = catalog.generation

    def fail() -> tuple[_Application, ...]:
        raise RuntimeError("discovery failed")

    source = fail
    assert catalog.refresh() is False
    assert catalog.snapshot() is published
    assert catalog.generation == generation


def test_constructor_rejects_sources_with_borrowed_registry():
    registry = _registry([])

    try:
        ApplicationCatalog(
            registry=registry,
            application_source=tuple,
        )
    except TypeError as exc:
        assert "owned mode" in str(exc)
    else:
        raise AssertionError("expected TypeError")


def test_catalog_has_no_discovery_or_monitor_implementation():
    catalog = ApplicationCatalog(
        application_source=tuple,
        desktop_directories_source=lambda: (Path("/unused"),),
    )

    assert not hasattr(catalog, "_application_source")
    assert not hasattr(catalog, "_app_monitor")
    assert not hasattr(catalog, "_directory_monitors")
    assert not hasattr(catalog, "_debounce_source_id")
