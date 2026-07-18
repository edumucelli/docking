"""Pure state, scheduling, and formatting logic for Plant Care."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from docking.core.math import clamp_int
from docking.i18n import _, ngettext

CHECK_INTERVAL_SECONDS = 15 * 60
DEFAULT_SNOOZE_DAYS = 1
MAX_INTERVAL_DAYS = 3650
MAX_PLANTS = 100
MAX_TOOLTIP_TASKS = 4


class CareKind(str, Enum):
    """Stable identifiers for supported recurring care tasks."""

    WATER = "water"
    FERTILIZE = "fertilize"
    MIST = "mist"
    ROTATE = "rotate"
    PRUNE = "prune"
    REPOT = "repot"
    PEST_CHECK = "pest_check"


CARE_KINDS: tuple[CareKind, ...] = tuple(CareKind)
DEFAULT_INTERVALS: dict[CareKind, int] = {
    CareKind.WATER: 7,
    CareKind.FERTILIZE: 30,
    CareKind.MIST: 3,
    CareKind.ROTATE: 14,
    CareKind.PRUNE: 90,
    CareKind.REPOT: 365,
    CareKind.PEST_CHECK: 30,
}


class CareStatus(str, Enum):
    """Overall icon state ordered from inactive to urgent."""

    EMPTY = "empty"
    HEALTHY = "healthy"
    DUE = "due"
    OVERDUE = "overdue"


@dataclass(frozen=True, slots=True)
class CareTask:
    """One recurring care schedule for a plant."""

    kind: CareKind
    interval_days: int
    last_completed: dt.date
    enabled: bool = False
    snoozed_until: dt.date | None = None


@dataclass(frozen=True, slots=True)
class Plant:
    """One configured plant and its care schedules."""

    id: str
    name: str
    tasks: tuple[CareTask, ...]
    species: str = ""


@dataclass(frozen=True, slots=True)
class PlantCareState:
    """All configured plants."""

    plants: tuple[Plant, ...] = ()


@dataclass(frozen=True, slots=True)
class ScheduledCare:
    """One concrete due date derived from a plant care task."""

    plant_id: str
    plant_name: str
    species: str
    task: CareTask
    due_date: dt.date
    days_until: int


@dataclass(frozen=True, slots=True)
class PlantCareSnapshot:
    """Glanceable state derived for icon, tooltip, and urgency."""

    status: CareStatus
    due_count: int
    overdue_count: int
    scheduled: tuple[ScheduledCare, ...]


def care_kind_label(kind: CareKind) -> str:
    """Return the translated noun label for a care type."""
    labels = {
        CareKind.WATER: _("Watering"),
        CareKind.FERTILIZE: _("Fertilizing"),
        CareKind.MIST: _("Misting"),
        CareKind.ROTATE: _("Rotating"),
        CareKind.PRUNE: _("Pruning"),
        CareKind.REPOT: _("Repotting"),
        CareKind.PEST_CHECK: _("Pest check"),
    }
    return labels[kind]


def care_kind_action(kind: CareKind) -> str:
    """Return the translated imperative label for a care type."""
    labels = {
        CareKind.WATER: _("Water"),
        CareKind.FERTILIZE: _("Fertilize"),
        CareKind.MIST: _("Mist"),
        CareKind.ROTATE: _("Rotate"),
        CareKind.PRUNE: _("Prune"),
        CareKind.REPOT: _("Repot"),
        CareKind.PEST_CHECK: _("Check for pests"),
    }
    return labels[kind]


def default_tasks(
    *,
    today: dt.date,
    water_enabled: bool = True,
) -> tuple[CareTask, ...]:
    """Return canonical schedules for a newly configured plant."""
    return tuple(
        CareTask(
            kind=kind,
            interval_days=DEFAULT_INTERVALS[kind],
            last_completed=today,
            enabled=water_enabled and kind is CareKind.WATER,
        )
        for kind in CARE_KINDS
    )


def new_plant(
    *,
    name: str,
    species: str,
    today: dt.date,
    tasks: Sequence[CareTask] | None = None,
    plant_id: str | None = None,
) -> Plant:
    """Create and normalize one plant with a stable identity."""
    raw = Plant(
        id=(plant_id or uuid.uuid4().hex),
        name=name,
        species=species,
        tasks=tuple(tasks) if tasks is not None else default_tasks(today=today),
    )
    return normalize_plant(plant=raw, today=today)


def normalize_plant(*, plant: Plant, today: dt.date) -> Plant:
    """Normalize one in-memory plant and fill missing care kinds."""
    task_by_kind = {
        task.kind: normalize_task(task=task, today=today) for task in plant.tasks
    }
    tasks = tuple(
        task_by_kind.get(
            kind,
            CareTask(
                kind=kind,
                interval_days=DEFAULT_INTERVALS[kind],
                last_completed=today,
            ),
        )
        for kind in CARE_KINDS
    )
    return Plant(
        id=plant.id.strip() or uuid.uuid4().hex,
        name=(plant.name.strip() or _("Plant"))[:80],
        species=plant.species.strip()[:120],
        tasks=tasks,
    )


def normalize_task(*, task: CareTask, today: dt.date) -> CareTask:
    """Clamp one care task to valid values."""
    last_completed = task.last_completed
    if not isinstance(last_completed, dt.date):
        last_completed = today
    snoozed_until = task.snoozed_until
    if snoozed_until is not None and not isinstance(snoozed_until, dt.date):
        snoozed_until = None
    return CareTask(
        kind=CareKind(task.kind),
        interval_days=clamp_int(int(task.interval_days), 1, MAX_INTERVAL_DAYS),
        last_completed=last_completed,
        enabled=bool(task.enabled),
        snoozed_until=snoozed_until,
    )


def state_from_prefs(
    *,
    prefs: Mapping[str, Any] | None,
    today: dt.date,
) -> PlantCareState:
    """Build Plant Care state from persisted preferences."""
    if not prefs:
        return PlantCareState()
    raw_plants = prefs.get("plants")
    if not isinstance(raw_plants, list | tuple):
        return PlantCareState()

    plants: list[Plant] = []
    used_ids: set[str] = set()
    for index, raw in enumerate(raw_plants[:MAX_PLANTS]):
        plant = _plant_from_mapping(raw=raw, index=index, today=today)
        if plant is None:
            continue
        plant_id = _unique_plant_id(
            candidate=plant.id,
            fallback=f"plant-{index + 1}",
            used=used_ids,
        )
        used_ids.add(plant_id)
        plants.append(replace(plant, id=plant_id))
    return PlantCareState(plants=tuple(plants))


def prefs_from_state(state: PlantCareState) -> dict[str, object]:
    """Return a JSON-compatible persistent preference payload."""
    return {
        "plants": [
            {
                "id": plant.id,
                "name": plant.name,
                "species": plant.species,
                "tasks": [
                    {
                        "kind": task.kind.value,
                        "interval_days": task.interval_days,
                        "last_completed": task.last_completed.isoformat(),
                        "enabled": task.enabled,
                        "snoozed_until": (
                            task.snoozed_until.isoformat()
                            if task.snoozed_until is not None
                            else None
                        ),
                    }
                    for task in plant.tasks
                ],
            }
            for plant in state.plants
        ]
    }


def add_plant(state: PlantCareState, *, plant: Plant, today: dt.date) -> PlantCareState:
    """Append a plant unless the catalog limit has been reached."""
    if len(state.plants) >= MAX_PLANTS:
        return state
    normalized = normalize_plant(plant=plant, today=today)
    used = {existing.id for existing in state.plants}
    plant_id = _unique_plant_id(
        candidate=normalized.id,
        fallback=f"plant-{len(state.plants) + 1}",
        used=used,
    )
    return replace(state, plants=(*state.plants, replace(normalized, id=plant_id)))


def replace_plant(
    state: PlantCareState,
    *,
    plant_id: str,
    plant: Plant,
    today: dt.date,
) -> PlantCareState:
    """Replace a configured plant while preserving its identity."""
    if not any(existing.id == plant_id for existing in state.plants):
        return state
    normalized = replace(
        normalize_plant(plant=plant, today=today),
        id=plant_id,
    )
    return replace(
        state,
        plants=tuple(
            normalized if existing.id == plant_id else existing
            for existing in state.plants
        ),
    )


def remove_plant(state: PlantCareState, *, plant_id: str) -> PlantCareState:
    """Remove one configured plant."""
    return replace(
        state,
        plants=tuple(plant for plant in state.plants if plant.id != plant_id),
    )


def complete_task(
    state: PlantCareState,
    *,
    plant_id: str,
    kind: CareKind,
    today: dt.date,
) -> PlantCareState:
    """Mark one task complete today and clear any snooze."""
    return _replace_task(
        state=state,
        plant_id=plant_id,
        kind=kind,
        update=lambda task: replace(
            task,
            last_completed=today,
            snoozed_until=None,
        ),
    )


def snooze_task(
    state: PlantCareState,
    *,
    plant_id: str,
    kind: CareKind,
    today: dt.date,
    days: int = DEFAULT_SNOOZE_DAYS,
) -> PlantCareState:
    """Delay one due task without recording care as completed."""
    snoozed_until = today + dt.timedelta(days=max(1, int(days)))
    return _replace_task(
        state=state,
        plant_id=plant_id,
        kind=kind,
        update=lambda task: replace(task, snoozed_until=snoozed_until),
    )


def task_due_date(task: CareTask) -> dt.date:
    """Return the effective due date after any snooze."""
    scheduled = task.last_completed + dt.timedelta(days=task.interval_days)
    if task.snoozed_until is None:
        return scheduled
    return max(scheduled, task.snoozed_until)


def scheduled_care(
    state: PlantCareState,
    *,
    today: dt.date,
) -> tuple[ScheduledCare, ...]:
    """Return all enabled tasks ordered by urgency."""
    order = {kind: index for index, kind in enumerate(CARE_KINDS)}
    entries: list[ScheduledCare] = []
    for plant in state.plants:
        for task in plant.tasks:
            if not task.enabled:
                continue
            due_date = task_due_date(task)
            entries.append(
                ScheduledCare(
                    plant_id=plant.id,
                    plant_name=plant.name,
                    species=plant.species,
                    task=task,
                    due_date=due_date,
                    days_until=(due_date - today).days,
                )
            )
    entries.sort(
        key=lambda entry: (
            entry.days_until,
            entry.plant_name.casefold(),
            order[entry.task.kind],
        )
    )
    return tuple(entries)


def snapshot(state: PlantCareState, *, today: dt.date) -> PlantCareSnapshot:
    """Build the current glanceable Plant Care snapshot."""
    scheduled = scheduled_care(state=state, today=today)
    due_count = sum(entry.days_until <= 0 for entry in scheduled)
    overdue_count = sum(entry.days_until < 0 for entry in scheduled)
    if not state.plants or not scheduled:
        status = CareStatus.EMPTY
    elif overdue_count:
        status = CareStatus.OVERDUE
    elif due_count:
        status = CareStatus.DUE
    else:
        status = CareStatus.HEALTHY
    return PlantCareSnapshot(
        status=status,
        due_count=due_count,
        overdue_count=overdue_count,
        scheduled=scheduled,
    )


def scheduled_care_label(entry: ScheduledCare) -> str:
    """Return a compact human-readable task status."""
    care = care_kind_label(entry.task.kind)
    if entry.days_until < 0:
        days = abs(entry.days_until)
        duration = ngettext(
            "overdue by {count} day",
            "overdue by {count} days",
            days,
        ).format(count=days)
    elif entry.days_until == 0:
        duration = _("due today")
    else:
        duration = ngettext(
            "in {count} day",
            "in {count} days",
            entry.days_until,
        ).format(count=entry.days_until)
    return _("{plant}: {care} {duration}").format(
        plant=entry.plant_name,
        care=care,
        duration=duration,
    )


def tooltip_text(state: PlantCareState, *, today: dt.date) -> str:
    """Build multiline tooltip text."""
    current = snapshot(state=state, today=today)
    lines = [_("Plant Care")]
    if not state.plants:
        lines.append(_("No plants configured"))
        return "\n".join(lines)
    if not current.scheduled:
        lines.append(_("No care schedules enabled"))
        return "\n".join(lines)

    if current.due_count:
        lines.append(
            ngettext(
                "{count} task due",
                "{count} tasks due",
                current.due_count,
            ).format(count=current.due_count)
        )
    else:
        lines.append(_("All plants are on schedule"))
    lines.extend(
        scheduled_care_label(entry) for entry in current.scheduled[:MAX_TOOLTIP_TASKS]
    )
    remaining = len(current.scheduled) - MAX_TOOLTIP_TASKS
    if remaining > 0:
        lines.append(
            ngettext(
                "{count} more task",
                "{count} more tasks",
                remaining,
            ).format(count=remaining)
        )
    return "\n".join(lines)


def menu_status_text(state: PlantCareState, *, today: dt.date) -> str:
    """Return one summary line for the applet menu."""
    current = snapshot(state=state, today=today)
    if not state.plants:
        return _("No plants configured")
    if not current.scheduled:
        return _("No care schedules enabled")
    if current.due_count:
        return ngettext(
            "{count} task due",
            "{count} tasks due",
            current.due_count,
        ).format(count=current.due_count)
    return scheduled_care_label(current.scheduled[0])


def plant_summary(plant: Plant, *, today: dt.date) -> str:
    """Return a compact summary for one configured plant."""
    one_plant = PlantCareState(plants=(plant,))
    current = snapshot(state=one_plant, today=today)
    if not current.scheduled:
        return _("{plant} - no schedules").format(plant=plant.name)
    if current.due_count:
        due = ngettext(
            "{count} task due",
            "{count} tasks due",
            current.due_count,
        ).format(count=current.due_count)
        return _("{plant} - {due}").format(plant=plant.name, due=due)
    return scheduled_care_label(current.scheduled[0])


def _replace_task(
    *,
    state: PlantCareState,
    plant_id: str,
    kind: CareKind,
    update,
) -> PlantCareState:
    plants: list[Plant] = []
    changed = False
    for plant in state.plants:
        if plant.id != plant_id:
            plants.append(plant)
            continue
        tasks: list[CareTask] = []
        for task in plant.tasks:
            if task.kind is kind and task.enabled:
                tasks.append(update(task))
                changed = True
            else:
                tasks.append(task)
        plants.append(replace(plant, tasks=tuple(tasks)))
    return replace(state, plants=tuple(plants)) if changed else state


def _plant_from_mapping(
    *,
    raw: object,
    index: int,
    today: dt.date,
) -> Plant | None:
    if not isinstance(raw, Mapping):
        return None
    values = {str(key): value for key, value in raw.items()}
    raw_tasks = values.get("tasks")
    if isinstance(raw_tasks, list | tuple):
        tasks = _tasks_from_sequence(raw_tasks, today=today)
    else:
        tasks = default_tasks(today=today)
    return normalize_plant(
        plant=Plant(
            id=str(values.get("id", f"plant-{index + 1}")),
            name=str(values.get("name", _("Plant"))),
            species=str(values.get("species", "")),
            tasks=tasks,
        ),
        today=today,
    )


def _tasks_from_sequence(
    raw_tasks: Sequence[object],
    *,
    today: dt.date,
) -> tuple[CareTask, ...]:
    parsed: dict[CareKind, CareTask] = {}
    for raw in raw_tasks:
        task = _task_from_mapping(raw=raw, today=today)
        if task is not None:
            parsed[task.kind] = task
    defaults = {
        task.kind: task for task in default_tasks(today=today, water_enabled=False)
    }
    defaults.update(parsed)
    return tuple(defaults[kind] for kind in CARE_KINDS)


def _task_from_mapping(*, raw: object, today: dt.date) -> CareTask | None:
    if not isinstance(raw, Mapping):
        return None
    values = {str(key): value for key, value in raw.items()}
    try:
        kind = CareKind(str(values.get("kind", "")))
    except ValueError:
        return None
    interval = _parse_int(
        values.get("interval_days"),
        default=DEFAULT_INTERVALS[kind],
    )
    return normalize_task(
        task=CareTask(
            kind=kind,
            interval_days=interval,
            last_completed=_parse_date(values.get("last_completed")) or today,
            enabled=_parse_bool(values.get("enabled"), default=False),
            snoozed_until=_parse_date(values.get("snoozed_until")),
        ),
        today=today,
    )


def _parse_date(value: object) -> dt.date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _parse_int(value: object, *, default: int) -> int:
    if not isinstance(value, str | int | float):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
    return default


def _unique_plant_id(*, candidate: str, fallback: str, used: set[str]) -> str:
    base = candidate.strip() or fallback
    current = base
    suffix = 2
    while current in used:
        current = f"{base}-{suffix}"
        suffix += 1
    return current
