"""Tests for recent-application policy and persistence."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from docking.platform.applications.recents import (
    RecentApplications,
    RecentApplicationsPersistence,
    RecentAppRecord,
)
from docking.platform.applications.types import ApplicationOrigin

NOW = 2_000_000_000


@dataclass
class _Config:
    show_recent_apps: bool = True
    recent_apps_max: int = 5
    recent_apps_retention_days: int = 14
    recent_apps: Any = field(default_factory=list)
    saved_values: list[list[dict[str, object]]] = field(default_factory=list)

    def save(self) -> None:
        self.saved_values.append(
            [dict(entry) for entry in self.recent_apps]
            if isinstance(self.recent_apps, list)
            else []
        )


class _Registry:
    def __init__(self, origins: dict[str, ApplicationOrigin]) -> None:
        self.origins = origins
        self.resolve_calls: list[tuple[str, bool]] = []

    def resolve(self, desktop_id: str, *, log_failures: bool = True) -> object | None:
        self.resolve_calls.append((desktop_id, log_failures))
        origin = self.origins.get(desktop_id)
        return SimpleNamespace(origin=origin) if origin is not None else None


def _service(
    config: _Config,
    *desktop_ids: str,
    registry: _Registry | None = None,
) -> RecentApplications:
    resolver = registry or _Registry(
        dict.fromkeys(desktop_ids, ApplicationOrigin.INSTALLED)
    )
    return RecentApplications(
        resolver,
        RecentApplicationsPersistence(config),
        clock=lambda: NOW,
    )


def _wire(*entries: tuple[str, int]) -> list[dict[str, object]]:
    return [
        {"desktop_id": desktop_id, "last_closed": last_closed}
        for desktop_id, last_closed in entries
    ]


def test_record_is_keyword_only_frozen_and_snapshots_are_immutable():
    with pytest.raises(TypeError):
        RecentAppRecord("app.desktop", NOW)  # type: ignore[misc]

    record = RecentAppRecord(desktop_id="app.desktop", last_closed=NOW)
    with pytest.raises(FrozenInstanceError):
        record.last_closed = 0  # type: ignore[misc]

    config = _Config(recent_apps=_wire(("app.desktop", NOW)))
    service = _service(config, "app.desktop")
    service.load()

    assert service.snapshot() == (record,)
    assert isinstance(service.snapshot(), tuple)


def test_persistence_decodes_malformed_values_and_writes_exact_wire_shape():
    config = _Config(
        recent_apps=[
            "bad",
            {"desktop_id": "", "last_closed": NOW},
            {"desktop_id": "missing-time.desktop"},
            {"desktop_id": "text-time.desktop", "last_closed": "today"},
            {
                "desktop_id": "valid.desktop",
                "last_closed": NOW + 0.75,
                "ignored": True,
            },
        ]
    )
    persistence = RecentApplicationsPersistence(config)

    assert persistence.read() == (
        RecentAppRecord(desktop_id="valid.desktop", last_closed=NOW),
    )

    persistence.save(persistence.read())

    assert config.recent_apps == _wire(("valid.desktop", NOW))
    assert config.saved_values == [_wire(("valid.desktop", NOW))]


def test_load_filters_in_order_and_rewrites_memory_without_saving():
    old = NOW - (15 * 86400)
    config = _Config(
        recent_apps=[
            {"desktop_id": "first.desktop", "last_closed": NOW},
            {"desktop_id": "first.desktop", "last_closed": NOW - 1},
            {"desktop_id": "pinned.desktop", "last_closed": NOW},
            {"desktop_id": "expired.desktop", "last_closed": old},
            {"desktop_id": "missing.desktop", "last_closed": NOW},
            {"desktop_id": "runtime.desktop", "last_closed": NOW},
            {"desktop_id": "second.desktop", "last_closed": NOW - 2},
            {"desktop_id": 42, "last_closed": NOW},
        ]
    )
    registry = _Registry(
        {
            "first.desktop": ApplicationOrigin.INSTALLED,
            "pinned.desktop": ApplicationOrigin.INSTALLED,
            "expired.desktop": ApplicationOrigin.INSTALLED,
            "runtime.desktop": ApplicationOrigin.RUNTIME,
            "second.desktop": ApplicationOrigin.GENERATED,
        }
    )
    service = _service(config, registry=registry)

    service.load(pinned_ids={"pinned.desktop"})

    assert service.snapshot() == (
        RecentAppRecord(desktop_id="first.desktop", last_closed=NOW),
        RecentAppRecord(desktop_id="second.desktop", last_closed=NOW - 2),
    )
    assert config.recent_apps == _wire(
        ("first.desktop", NOW),
        ("second.desktop", NOW - 2),
    )
    assert config.saved_values == []
    assert all(
        log_failures is False for _desktop_id, log_failures in registry.resolve_calls
    )


def test_load_applies_maximum_and_retention_without_saving():
    config = _Config(
        recent_apps_max=1,
        recent_apps_retention_days=1,
        recent_apps=_wire(
            ("expired.desktop", NOW - (2 * 86400)),
            ("first.desktop", NOW),
            ("second.desktop", NOW - 1),
        ),
    )
    service = _service(
        config,
        "expired.desktop",
        "first.desktop",
        "second.desktop",
    )

    service.load()

    assert service.snapshot() == (
        RecentAppRecord(desktop_id="first.desktop", last_closed=NOW),
    )
    assert config.recent_apps == _wire(("first.desktop", NOW))
    assert config.saved_values == []


def test_disabled_load_keeps_persisted_history_but_exposes_no_records():
    persisted = _wire(("app.desktop", NOW))
    config = _Config(show_recent_apps=False, recent_apps=list(persisted))
    service = _service(config, "app.desktop")

    service.load()

    assert service.snapshot() == ()
    assert config.recent_apps == persisted
    assert config.saved_values == []


@pytest.mark.parametrize("operation", ["close", "unpin"])
def test_close_and_unpin_save_before_pruning(operation: str):
    config = _Config(
        recent_apps_max=1,
        recent_apps=_wire(("old.desktop", NOW - 1)),
    )
    service = _service(config, "old.desktop", "new.desktop")
    service.load()

    if operation == "close":
        changed = service.record_closed("new.desktop")
    else:
        changed = service.record_unpinned("new.desktop")

    assert changed is True
    assert config.saved_values == [
        _wire(("new.desktop", NOW), ("old.desktop", NOW - 1))
    ]
    assert config.recent_apps == config.saved_values[-1]
    assert service.snapshot() == (
        RecentAppRecord(desktop_id="new.desktop", last_closed=NOW),
    )


def test_record_closed_reorders_existing_record_and_skips_pinned_or_unresolvable():
    config = _Config(
        recent_apps=_wire(
            ("first.desktop", NOW - 2),
            ("second.desktop", NOW - 1),
        )
    )
    service = _service(config, "first.desktop", "second.desktop")
    service.load()

    assert service.record_closed("first.desktop") is True
    assert [record.desktop_id for record in service.snapshot()] == [
        "first.desktop",
        "second.desktop",
    ]
    assert service.snapshot()[0].last_closed == NOW
    assert (
        service.record_closed("second.desktop", pinned_ids={"second.desktop"}) is False
    )
    assert service.record_closed("missing.desktop") is False
    assert len(config.saved_values) == 1


def test_discard_models_pinning_and_syncs_without_saving():
    config = _Config(recent_apps=_wire(("app.desktop", NOW)))
    service = _service(config, "app.desktop")
    service.load()

    assert service.discard("app.desktop") is True
    assert service.discard("app.desktop") is False

    assert service.snapshot() == ()
    assert config.recent_apps == []
    assert config.saved_values == []


def test_running_removal_syncs_memory_without_saving():
    config = _Config(recent_apps=_wire(("app.desktop", NOW)))
    service = _service(config, "app.desktop")
    service.load()

    service.reconcile_running({"app.desktop"})

    assert service.snapshot() == ()
    assert config.recent_apps == []
    assert config.saved_values == []


def test_simultaneous_closures_are_saved_one_at_a_time_in_current_set_order():
    config = _Config()
    service = _service(config, "a.desktop", "b.desktop")
    service.load()
    service.reconcile_running({"a.desktop", "b.desktop"})

    service.reconcile_running(set())

    assert len(config.saved_values) == 2
    assert [len(value) for value in config.saved_values] == [1, 2]
    assert config.saved_values[-1][1:] == config.saved_values[0]
    assert {record.desktop_id for record in service.snapshot()} == {
        "a.desktop",
        "b.desktop",
    }


def test_previous_running_ids_are_retained_while_disabled():
    config = _Config(show_recent_apps=False)
    service = _service(config, "app.desktop")
    service.load()

    service.reconcile_running({"app.desktop"})
    config.show_recent_apps = True
    service.reconcile_running(set())

    assert service.snapshot() == (
        RecentAppRecord(desktop_id="app.desktop", last_closed=NOW),
    )
    assert config.saved_values == [_wire(("app.desktop", NOW))]


def test_runtime_only_closure_is_excluded_until_generated_entry_resolves():
    config = _Config()
    registry = _Registry({"runtime.desktop": ApplicationOrigin.RUNTIME})
    service = _service(config, registry=registry)
    service.load()

    service.reconcile_running({"runtime.desktop"})
    service.reconcile_running(set())
    assert service.snapshot() == ()
    assert config.saved_values == []

    registry.origins["runtime.desktop"] = ApplicationOrigin.GENERATED
    service.reconcile_running({"runtime.desktop"})
    service.reconcile_running(set())

    assert service.snapshot() == (
        RecentAppRecord(desktop_id="runtime.desktop", last_closed=NOW),
    )


def test_reload_policy_notifies_without_eager_pruning_or_saving():
    config = _Config(
        recent_apps=_wire(
            ("first.desktop", NOW),
            ("second.desktop", NOW - 1),
        )
    )
    service = _service(config, "first.desktop", "second.desktop")
    service.load()
    listener = _Listener()
    service.subscribe(listener)

    config.recent_apps_max = 1
    service.reload_policy()

    assert len(service.snapshot()) == 2
    assert len(config.recent_apps) == 2
    assert config.saved_values == []
    assert listener.calls == 1


def test_disabling_and_clear_save_immediately_then_reenable_loads():
    config = _Config(recent_apps=_wire(("app.desktop", NOW)))
    service = _service(config, "app.desktop")
    service.load()

    config.show_recent_apps = False
    service.reload_policy()

    assert service.snapshot() == ()
    assert config.recent_apps == []
    assert config.saved_values == [[]]

    config.show_recent_apps = True
    config.recent_apps = _wire(("app.desktop", NOW))
    service.reload_policy()

    assert service.snapshot() == (
        RecentAppRecord(desktop_id="app.desktop", last_closed=NOW),
    )
    assert config.saved_values == [[]]

    service.clear()
    assert config.saved_values == [[], []]


class _Listener:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


def test_listeners_are_unique_and_unsubscribe_is_idempotent():
    config = _Config()
    service = _service(config)
    listener = _Listener()
    unsubscribe = service.subscribe(listener)
    duplicate_unsubscribe = service.subscribe(listener)

    service.clear()
    assert listener.calls == 1

    unsubscribe()
    unsubscribe()
    service.clear()
    duplicate_unsubscribe()

    assert listener.calls == 1
