"""Tests for the Plant Care applet."""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import docking.applets.plantcare.applet as plantcare_mod
from docking.applets.plantcare.applet import PlantCareApplet
from docking.applets.plantcare.render import render_icon
from docking.applets.plantcare.state import (
    CARE_KINDS,
    MAX_INTERVAL_DAYS,
    CareKind,
    CareStatus,
    CareTask,
    Plant,
    PlantCareSnapshot,
    PlantCareState,
    add_plant,
    complete_task,
    default_tasks,
    menu_status_text,
    new_plant,
    plant_summary,
    prefs_from_state,
    remove_plant,
    replace_plant,
    scheduled_care,
    scheduled_care_label,
    snapshot,
    snooze_task,
    state_from_prefs,
    task_due_date,
    tooltip_text,
)
from docking.core.config import Config

TODAY = dt.date(2026, 3, 30)


def _task(
    *,
    kind: CareKind = CareKind.WATER,
    interval_days: int = 7,
    last_completed: dt.date = dt.date(2026, 3, 23),
    enabled: bool = True,
    snoozed_until: dt.date | None = None,
) -> CareTask:
    return CareTask(
        kind=kind,
        interval_days=interval_days,
        last_completed=last_completed,
        enabled=enabled,
        snoozed_until=snoozed_until,
    )


def _plant(
    *,
    plant_id: str = "aloe",
    name: str = "Aloe",
    task: CareTask | None = None,
) -> Plant:
    return Plant(
        id=plant_id,
        name=name,
        species="Aloe vera",
        tasks=(task or _task(),),
    )


class TestPlantCareState:
    def test_new_plant_enables_only_watering(self):
        plant = new_plant(
            name=" Fern ",
            species=" Nephrolepis ",
            today=TODAY,
            plant_id="fern",
        )

        assert plant.name == "Fern"
        assert plant.species == "Nephrolepis"
        assert len(plant.tasks) == len(CARE_KINDS)
        enabled = [task.kind for task in plant.tasks if task.enabled]
        assert enabled == [CareKind.WATER]
        assert all(task.last_completed == TODAY for task in plant.tasks)

    def test_preferences_round_trip(self):
        state = PlantCareState(
            plants=(
                new_plant(
                    name="Aloe",
                    species="Aloe vera",
                    today=TODAY,
                    plant_id="aloe",
                ),
            )
        )

        loaded = state_from_prefs(
            prefs=prefs_from_state(state),
            today=TODAY,
        )

        assert loaded == state

    def test_invalid_preference_shapes_are_ignored(self):
        assert state_from_prefs(prefs=None, today=TODAY) == PlantCareState()
        assert (
            state_from_prefs(
                prefs={"plants": "invalid"},
                today=TODAY,
            )
            == PlantCareState()
        )
        assert (
            state_from_prefs(
                prefs={"plants": ["invalid"]},
                today=TODAY,
            )
            == PlantCareState()
        )

    def test_malformed_task_values_are_normalized(self):
        state = state_from_prefs(
            prefs={
                "plants": [
                    {
                        "id": "fern",
                        "name": "  Fern  ",
                        "tasks": [
                            {
                                "kind": "water",
                                "interval_days": 99999,
                                "last_completed": "invalid",
                                "enabled": "yes",
                                "snoozed_until": "bad",
                            },
                            {"kind": "unknown"},
                        ],
                    }
                ]
            },
            today=TODAY,
        )

        water = state.plants[0].tasks[0]
        assert state.plants[0].name == "Fern"
        assert water.interval_days == MAX_INTERVAL_DAYS
        assert water.last_completed == TODAY
        assert water.enabled
        assert water.snoozed_until is None

    def test_duplicate_persisted_ids_become_unique(self):
        prefs = {
            "plants": [
                {"id": "plant", "name": "A"},
                {"id": "plant", "name": "B"},
            ]
        }

        state = state_from_prefs(prefs=prefs, today=TODAY)

        assert [plant.id for plant in state.plants] == ["plant", "plant-2"]

    def test_add_replace_and_remove_preserve_identity(self):
        first = new_plant(
            name="Aloe",
            species="",
            today=TODAY,
            plant_id="same",
        )
        state = add_plant(
            PlantCareState(),
            plant=first,
            today=TODAY,
        )
        duplicate = new_plant(
            name="Fern",
            species="",
            today=TODAY,
            plant_id="same",
        )
        state = add_plant(state, plant=duplicate, today=TODAY)
        assert [plant.id for plant in state.plants] == ["same", "same-2"]

        replacement = new_plant(
            name="Renamed",
            species="",
            today=TODAY,
            plant_id="different",
        )
        state = replace_plant(
            state,
            plant_id="same",
            plant=replacement,
            today=TODAY,
        )
        assert state.plants[0].id == "same"
        assert state.plants[0].name == "Renamed"

        state = remove_plant(state, plant_id="same")
        assert [plant.id for plant in state.plants] == ["same-2"]


class TestPlantCareScheduling:
    def test_due_date_uses_calendar_days_across_dst_boundary(self):
        task = _task(
            interval_days=1,
            last_completed=dt.date(2026, 3, 29),
        )

        assert task_due_date(task) == TODAY

    def test_snooze_moves_effective_due_date(self):
        task = _task(snoozed_until=dt.date(2026, 4, 2))

        assert task_due_date(task) == dt.date(2026, 4, 2)

    def test_scheduled_tasks_are_sorted_by_urgency_then_name(self):
        state = PlantCareState(
            plants=(
                _plant(
                    plant_id="fern",
                    name="Fern",
                    task=_task(last_completed=dt.date(2026, 3, 20)),
                ),
                _plant(
                    plant_id="aloe",
                    name="Aloe",
                    task=_task(last_completed=dt.date(2026, 3, 21)),
                ),
            )
        )

        entries = scheduled_care(state, today=TODAY)

        assert [entry.plant_name for entry in entries] == ["Fern", "Aloe"]
        assert [entry.days_until for entry in entries] == [-3, -2]

    def test_snapshot_reports_all_statuses(self):
        empty = snapshot(PlantCareState(), today=TODAY)
        healthy = snapshot(
            PlantCareState(
                plants=(
                    _plant(
                        task=_task(last_completed=TODAY),
                    ),
                )
            ),
            today=TODAY,
        )
        due = snapshot(
            PlantCareState(plants=(_plant(),)),
            today=TODAY,
        )
        overdue = snapshot(
            PlantCareState(
                plants=(
                    _plant(
                        task=_task(last_completed=dt.date(2026, 3, 20)),
                    ),
                )
            ),
            today=TODAY,
        )

        assert empty.status is CareStatus.EMPTY
        assert healthy.status is CareStatus.HEALTHY
        assert due.status is CareStatus.DUE
        assert due.due_count == 1
        assert overdue.status is CareStatus.OVERDUE
        assert overdue.overdue_count == 1

    def test_complete_updates_date_and_clears_snooze(self):
        state = PlantCareState(
            plants=(
                _plant(
                    task=_task(snoozed_until=dt.date(2026, 4, 2)),
                ),
            )
        )

        completed = complete_task(
            state,
            plant_id="aloe",
            kind=CareKind.WATER,
            today=TODAY,
        )
        task = completed.plants[0].tasks[0]

        assert task.last_completed == TODAY
        assert task.snoozed_until is None

    def test_snooze_delays_without_marking_complete(self):
        state = PlantCareState(plants=(_plant(),))

        snoozed = snooze_task(
            state,
            plant_id="aloe",
            kind=CareKind.WATER,
            today=TODAY,
        )
        task = snoozed.plants[0].tasks[0]

        assert task.last_completed == dt.date(2026, 3, 23)
        assert task.snoozed_until == dt.date(2026, 3, 31)
        assert snapshot(snoozed, today=TODAY).due_count == 0

    def test_disabled_or_unknown_task_actions_are_noops(self):
        disabled = _task(enabled=False)
        state = PlantCareState(plants=(_plant(task=disabled),))

        assert (
            complete_task(
                state,
                plant_id="missing",
                kind=CareKind.WATER,
                today=TODAY,
            )
            == state
        )
        assert (
            snooze_task(
                state,
                plant_id="aloe",
                kind=CareKind.WATER,
                today=TODAY,
            )
            == state
        )


class TestPlantCareFormatting:
    def test_empty_and_disabled_tooltips(self):
        assert "No plants" in tooltip_text(PlantCareState(), today=TODAY)
        no_tasks = PlantCareState(plants=(_plant(task=_task(enabled=False)),))
        assert "No care schedules" in tooltip_text(no_tasks, today=TODAY)

    def test_due_tooltip_and_menu_include_count(self):
        state = PlantCareState(plants=(_plant(),))

        assert "1 task due" in tooltip_text(state, today=TODAY)
        assert "1 task due" in menu_status_text(state, today=TODAY)

    def test_scheduled_label_covers_overdue_due_and_future(self):
        overdue = scheduled_care(
            PlantCareState(
                plants=(
                    _plant(
                        task=_task(last_completed=dt.date(2026, 3, 20)),
                    ),
                )
            ),
            today=TODAY,
        )[0]
        due = scheduled_care(
            PlantCareState(plants=(_plant(),)),
            today=TODAY,
        )[0]
        future = scheduled_care(
            PlantCareState(
                plants=(
                    _plant(
                        task=_task(last_completed=TODAY),
                    ),
                )
            ),
            today=TODAY,
        )[0]

        assert "overdue" in scheduled_care_label(overdue)
        assert "due today" in scheduled_care_label(due)
        assert "in 7 days" in scheduled_care_label(future)

    def test_plant_summary_reports_due_or_no_schedules(self):
        assert "task due" in plant_summary(_plant(), today=TODAY)
        assert "no schedules" in plant_summary(
            _plant(task=_task(enabled=False)),
            today=TODAY,
        )


class TestPlantCareRendering:
    def test_renders_all_states_at_supported_sizes(self):
        for status in CareStatus:
            due_count = 2 if status in {CareStatus.DUE, CareStatus.OVERDUE} else 0
            current = PlantCareSnapshot(
                status=status,
                due_count=due_count,
                overdue_count=1 if status is CareStatus.OVERDUE else 0,
                scheduled=(),
            )
            for size in (32, 48, 64):
                pixbuf = render_icon(size=size, snapshot=current)
                assert pixbuf is not None
                assert pixbuf.get_width() == size
                assert pixbuf.get_height() == size


class TestPlantCareApplet:
    def test_creates_with_icon_and_empty_tooltip(self, monkeypatch):
        monkeypatch.setattr(PlantCareApplet, "_today", staticmethod(lambda: TODAY))

        applet = PlantCareApplet(48, config=Config())

        assert applet.item.icon is not None
        assert "No plants" in applet.item.name

    def test_menu_contains_manage_actions(self, monkeypatch):
        monkeypatch.setattr(PlantCareApplet, "_today", staticmethod(lambda: TODAY))
        applet = PlantCareApplet(48, config=Config())

        labels = [item.get_label() for item in applet.get_menu_items()]

        assert "Add Plant..." in labels
        assert "Manage Plants..." in labels
        assert "Refresh Now" in labels

    def test_due_menu_exposes_done_and_snooze(self, monkeypatch):
        monkeypatch.setattr(PlantCareApplet, "_today", staticmethod(lambda: TODAY))
        applet = PlantCareApplet(48, config=Config())
        applet._state = PlantCareState(plants=(_plant(),))

        menu = applet.get_menu_items()
        due_item = next(item for item in menu if item.get_submenu() is not None)
        submenu = due_item.get_submenu()
        children = getattr(submenu, "children", None)
        if children is None:
            children = submenu.get_children()
        submenu_labels = [child.get_label() for child in children]

        assert submenu_labels == ["Done", "Snooze 1 Day"]

    def test_add_plant_persists_preferences(self, tmp_path, monkeypatch):
        monkeypatch.setattr(PlantCareApplet, "_today", staticmethod(lambda: TODAY))
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        applet = PlantCareApplet(48, config=Config.load(path))
        plant = new_plant(
            name="Fern",
            species="",
            today=TODAY,
            plant_id="fern",
        )

        applet._upsert_plant(original=None, updated=plant)

        prefs = Config.load(path).applet_prefs["plantcare"]
        assert prefs["plants"][0]["name"] == "Fern"

    def test_completion_clears_urgency_when_last_task_is_done(self, monkeypatch):
        monkeypatch.setattr(PlantCareApplet, "_today", staticmethod(lambda: TODAY))
        applet = PlantCareApplet(48, config=Config())
        applet._state = PlantCareState(plants=(_plant(),))
        applet._known_due_count = 0
        applet._refresh_due_state()
        assert applet.item.is_urgent
        care = snapshot(applet._state, today=TODAY).scheduled[0]

        applet._complete(care)

        assert not applet.item.is_urgent
        assert snapshot(applet._state, today=TODAY).due_count == 0

    def test_urgency_timestamp_only_changes_on_transition(self, monkeypatch):
        monkeypatch.setattr(PlantCareApplet, "_today", staticmethod(lambda: TODAY))
        monotonic = MagicMock(side_effect=[100, 200])
        monkeypatch.setattr(plantcare_mod.GLib, "get_monotonic_time", monotonic)
        applet = PlantCareApplet(48, config=Config())
        applet._state = PlantCareState(plants=(_plant(),))

        applet._refresh_due_state()
        applet._refresh_due_state()

        assert applet.item.last_urgent == 100
        monotonic.assert_called_once()

    def test_start_and_stop_manage_timer(self, monkeypatch):
        monkeypatch.setattr(PlantCareApplet, "_today", staticmethod(lambda: TODAY))
        applet = PlantCareApplet(48, config=Config())
        monkeypatch.setattr(
            plantcare_mod.GLib,
            "timeout_add_seconds",
            lambda _seconds, _callback: 321,
        )
        removed: list[int] = []
        monkeypatch.setattr(
            plantcare_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )

        applet.start(lambda: None)
        applet.stop()

        assert removed == [321]
        assert applet._timer_id == 0

    def test_newly_enabled_editor_task_starts_today(self):
        original = _task(
            enabled=False,
            last_completed=dt.date(2025, 1, 1),
        )

        task = PlantCareApplet._task_from_editor(
            original=original,
            enabled=True,
            interval_days=10,
            today=TODAY,
        )

        assert task.enabled
        assert task.last_completed == TODAY
        assert task.interval_days == 10

    def test_default_task_count_matches_supported_care_kinds(self):
        assert len(default_tasks(today=TODAY)) == len(CARE_KINDS)
