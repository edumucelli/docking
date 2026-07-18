"""GTK lifecycle and management UI for Plant Care."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.plantcare import meta
from docking.applets.plantcare.render import render_icon
from docking.applets.plantcare.state import (
    CARE_KINDS,
    CHECK_INTERVAL_SECONDS,
    CareKind,
    CareTask,
    Plant,
    PlantCareSnapshot,
    PlantCareState,
    ScheduledCare,
    add_plant,
    care_kind_action,
    care_kind_label,
    complete_task,
    menu_status_text,
    new_plant,
    plant_summary,
    prefs_from_state,
    remove_plant,
    replace_plant,
    scheduled_care_label,
    snapshot,
    snooze_task,
    state_from_prefs,
    tooltip_text,
)
from docking.applets.popup import add_cancel_ok_buttons, prepare_dialog_content
from docking.i18n import _, ngettext

if TYPE_CHECKING:
    from docking.core.config import Config

MANAGER_DIALOG_WIDTH_PX = 560
MANAGER_DIALOG_HEIGHT_PX = 440
EDITOR_DIALOG_WIDTH_PX = 470
EDITOR_DIALOG_SPACING_PX = 8
EDITOR_DIALOG_MARGIN_PX = 12
MAX_MENU_DUE_TASKS = 6


class PlantCareApplet(Applet):
    """Local recurring care reminders for multiple plants."""

    id = meta.id
    name = _("Plant Care")
    icon_name = "emblem-default"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        today = self._today()
        prefs = config.applet_prefs.get(meta.id, {}) if config else None
        self._state: PlantCareState = state_from_prefs(
            prefs=prefs,
            today=today,
        )
        self._timer_id: int = 0
        self._known_due_count = 0
        self._last_snapshot: PlantCareSnapshot = snapshot(
            state=self._state,
            today=today,
        )
        self._manager_dialog: Gtk.Dialog | None = None
        self._manager_root: Gtk.Box | None = None
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(
            size=size,
            snapshot=snapshot(state=self._state, today=self._today()),
        )

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(state=self._state, today=self._today())

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._refresh_due_state()
        self._timer_id = GLib.timeout_add_seconds(
            CHECK_INTERVAL_SECONDS,
            self._tick,
        )

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._manager_dialog is not None:
            self._manager_dialog.destroy()
            self._manager_dialog = None
            self._manager_root = None
        super().stop()

    def on_clicked(self) -> None:
        self._show_manager()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        today = self._today()
        current = snapshot(state=self._state, today=today)
        status = [
            disabled_menu_item(
                menu_status_text(state=self._state, today=today),
                gtk=Gtk,
            )
        ]
        primary = self._due_menu_items(current=current)

        add = Gtk.MenuItem(label=_("Add Plant..."))
        add.connect("activate", lambda _widget: self._show_plant_editor())
        manage = Gtk.MenuItem(label=_("Manage Plants..."))
        manage.connect("activate", lambda _widget: self._show_manager())

        refresh = Gtk.MenuItem(label=_("Refresh Now"))
        refresh.connect("activate", lambda _widget: self._refresh_due_state())
        return menu_sections(
            status=status,
            primary=primary,
            manage=[add, manage],
            refresh=[refresh],
            gtk=Gtk,
        )

    def _due_menu_items(
        self,
        *,
        current: PlantCareSnapshot,
    ) -> list[Gtk.MenuItem]:
        due = [entry for entry in current.scheduled if entry.days_until <= 0]
        items: list[Gtk.MenuItem] = []
        for entry in due[:MAX_MENU_DUE_TASKS]:
            parent = Gtk.MenuItem(
                label=_("{action} {plant}").format(
                    action=care_kind_action(entry.task.kind),
                    plant=entry.plant_name,
                )
            )
            submenu = Gtk.Menu()

            done = Gtk.MenuItem(label=_("Done"))
            done.connect(
                "activate",
                lambda _widget, care=entry: self._complete(care),
            )
            snooze = Gtk.MenuItem(label=_("Snooze 1 Day"))
            snooze.connect(
                "activate",
                lambda _widget, care=entry: self._snooze(care),
            )
            submenu.append(done)
            submenu.append(snooze)
            parent.set_submenu(submenu)
            items.append(parent)

        hidden = len(due) - MAX_MENU_DUE_TASKS
        if hidden > 0:
            items.append(
                disabled_menu_item(
                    ngettext(
                        "{count} more due task",
                        "{count} more due tasks",
                        hidden,
                    ).format(count=hidden),
                    gtk=Gtk,
                )
            )
        return items

    def _tick(self) -> bool:
        self._refresh_due_state()
        return True

    def _refresh_due_state(self) -> None:
        current = snapshot(state=self._state, today=self._today())
        became_due = self._known_due_count == 0 and current.due_count > 0
        was_urgent = self.item.is_urgent
        self.item.is_urgent = current.due_count > 0
        if became_due:
            self.item.last_urgent = GLib.get_monotonic_time()

        changed = current != self._last_snapshot or was_urgent != self.item.is_urgent
        self._known_due_count = current.due_count
        self._last_snapshot = current
        if changed:
            self.present()
            self._refresh_manager_if_visible()

    def _complete(self, care: ScheduledCare) -> None:
        self._state = complete_task(
            state=self._state,
            plant_id=care.plant_id,
            kind=care.task.kind,
            today=self._today(),
        )
        self._save_and_refresh()

    def _snooze(self, care: ScheduledCare) -> None:
        self._state = snooze_task(
            state=self._state,
            plant_id=care.plant_id,
            kind=care.task.kind,
            today=self._today(),
        )
        self._save_and_refresh()

    def _save_and_refresh(self) -> None:
        self._save()
        self._refresh_due_state()
        self.present()
        self._refresh_manager_if_visible()

    def _save(self) -> None:
        self.save_prefs(prefs=prefs_from_state(self._state))

    # -- Manager -------------------------------------------------------------

    def _show_manager(self) -> None:
        if self._manager_dialog is None:
            self._manager_dialog = self._create_manager_dialog()
        self._rebuild_manager()
        self._manager_dialog.show_all()
        self._manager_dialog.present()

    def _create_manager_dialog(self) -> Gtk.Dialog:
        dialog = Gtk.Dialog(
            title=_("Plant Care"),
            destroy_with_parent=True,
        )
        dialog.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        content = prepare_dialog_content(
            dialog=dialog,
            width=MANAGER_DIALOG_WIDTH_PX,
            height=MANAGER_DIALOG_HEIGHT_PX,
            spacing=8,
            margin=10,
            resizable=True,
        )
        self._manager_root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
        )
        content.pack_start(self._manager_root, True, True, 0)
        dialog.connect("response", lambda widget, _response: widget.hide())
        dialog.connect("delete-event", self._on_manager_delete)
        return dialog

    @staticmethod
    def _on_manager_delete(dialog: Gtk.Dialog, _event: object) -> bool:
        dialog.hide()
        return True

    def _rebuild_manager(self) -> None:
        if self._manager_root is None:
            return
        for child in list(self._manager_root.get_children()):
            self._manager_root.remove(child)

        current = snapshot(state=self._state, today=self._today())
        summary = Gtk.Label(
            label=menu_status_text(state=self._state, today=self._today())
        )
        summary.set_xalign(0.0)
        summary.get_style_context().add_class("dim-label")
        self._manager_root.pack_start(summary, False, False, 0)

        notebook = Gtk.Notebook()
        notebook.append_page(
            self._build_care_page(current=current),
            Gtk.Label(label=_("Care")),
        )
        notebook.append_page(
            self._build_plants_page(),
            Gtk.Label(label=_("Plants")),
        )
        self._manager_root.pack_start(notebook, True, True, 0)

    def _build_care_page(self, *, current: PlantCareSnapshot) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)
        outer.set_margin_start(8)
        outer.set_margin_end(8)

        if not self._state.plants:
            outer.pack_start(
                self._empty_label(_("Add a plant to start tracking care.")),
                True,
                True,
                0,
            )
            add = Gtk.Button(label=_("Add Plant..."))
            add.connect("clicked", lambda _button: self._show_plant_editor())
            outer.pack_start(add, False, False, 0)
            return outer
        if not current.scheduled:
            outer.pack_start(
                self._empty_label(_("No care schedules are enabled.")),
                True,
                True,
                0,
            )
            return outer

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        due = tuple(entry for entry in current.scheduled if entry.days_until <= 0)
        upcoming = tuple(entry for entry in current.scheduled if entry.days_until > 0)
        if due:
            rows.pack_start(self._section_label(_("Due")), False, False, 0)
            for entry in due:
                rows.pack_start(
                    self._care_row(entry=entry, actionable=True),
                    False,
                    False,
                    0,
                )
        if upcoming:
            rows.pack_start(self._section_label(_("Upcoming")), False, False, 0)
            for entry in upcoming:
                rows.pack_start(
                    self._care_row(entry=entry, actionable=False),
                    False,
                    False,
                    0,
                )

        scrolled.add(rows)
        outer.pack_start(scrolled, True, True, 0)
        return outer

    def _care_row(
        self,
        *,
        entry: ScheduledCare,
        actionable: bool,
    ) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_top(3)
        row.set_margin_bottom(3)

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(
            label=_("{plant} - {care}").format(
                plant=entry.plant_name,
                care=care_kind_label(entry.task.kind),
            )
        )
        title.set_xalign(0.0)
        detail = Gtk.Label(label=scheduled_care_label(entry))
        detail.set_xalign(0.0)
        detail.get_style_context().add_class("dim-label")
        labels.pack_start(title, False, False, 0)
        labels.pack_start(detail, False, False, 0)
        row.pack_start(labels, True, True, 0)

        if actionable:
            done = Gtk.Button(label=_("Done"))
            done.connect("clicked", lambda _button: self._complete(entry))
            snooze = Gtk.Button(label=_("Snooze"))
            snooze.connect("clicked", lambda _button: self._snooze(entry))
            row.pack_end(snooze, False, False, 0)
            row.pack_end(done, False, False, 0)
        return row

    def _build_plants_page(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)
        outer.set_margin_start(8)
        outer.set_margin_end(8)

        add = Gtk.Button(label=_("Add Plant..."))
        add.connect("clicked", lambda _button: self._show_plant_editor())
        outer.pack_start(add, False, False, 0)

        if not self._state.plants:
            outer.pack_start(
                self._empty_label(_("No plants configured.")),
                True,
                True,
                0,
            )
            return outer

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for plant in self._state.plants:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            name = Gtk.Label(label=plant.name)
            name.set_xalign(0.0)
            detail_text = plant.species or plant_summary(
                plant,
                today=self._today(),
            )
            detail = Gtk.Label(label=detail_text)
            detail.set_xalign(0.0)
            detail.get_style_context().add_class("dim-label")
            labels.pack_start(name, False, False, 0)
            labels.pack_start(detail, False, False, 0)
            row.pack_start(labels, True, True, 0)

            edit = Gtk.Button(label=_("Edit"))
            edit.connect(
                "clicked",
                lambda _button, selected=plant: self._show_plant_editor(plant=selected),
            )
            row.pack_end(edit, False, False, 0)
            rows.pack_start(row, False, False, 0)
        scrolled.add(rows)
        outer.pack_start(scrolled, True, True, 0)
        return outer

    @staticmethod
    def _section_label(text: str) -> Gtk.Label:
        label = Gtk.Label()
        label.set_markup(f"<b>{GLib.markup_escape_text(text)}</b>")
        label.set_xalign(0.0)
        return label

    @staticmethod
    def _empty_label(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.set_xalign(0.5)
        label.set_yalign(0.5)
        label.set_line_wrap(True)
        label.get_style_context().add_class("dim-label")
        return label

    def _refresh_manager_if_visible(self) -> None:
        if self._manager_dialog is None or not self._manager_dialog.get_visible():
            return
        self._rebuild_manager()
        self._manager_dialog.show_all()

    # -- Plant editor --------------------------------------------------------

    def _show_plant_editor(self, *, plant: Plant | None = None) -> None:
        today = self._today()
        existing = plant or new_plant(
            name="",
            species="",
            today=today,
        )
        task_by_kind = {task.kind: task for task in existing.tasks}
        parent = self._manager_dialog
        dialog = Gtk.Dialog(
            title=_("Edit Plant") if plant is not None else _("Add Plant"),
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        add_cancel_ok_buttons(
            dialog=dialog,
            ok_label=_("Save"),
            cancel_label=_("Cancel"),
        )
        if plant is not None:
            dialog.add_button(_("Remove"), Gtk.ResponseType.REJECT)
        content = prepare_dialog_content(
            dialog=dialog,
            width=EDITOR_DIALOG_WIDTH_PX,
            spacing=EDITOR_DIALOG_SPACING_PX,
            margin=EDITOR_DIALOG_MARGIN_PX,
            default_response=Gtk.ResponseType.OK,
            resizable=False,
        )

        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        content.pack_start(grid, True, True, 0)
        name_entry = Gtk.Entry()
        name_entry.set_text(existing.name if plant is not None else "")
        species_entry = Gtk.Entry()
        species_entry.set_text(existing.species)
        grid.attach(Gtk.Label(label=_("Name")), 0, 0, 1, 1)
        grid.attach(name_entry, 1, 0, 2, 1)
        grid.attach(Gtk.Label(label=_("Species")), 0, 1, 1, 1)
        grid.attach(species_entry, 1, 1, 2, 1)
        grid.attach(self._section_label(_("Care Schedule")), 0, 2, 2, 1)
        days_header = Gtk.Label(label=_("Days"))
        days_header.get_style_context().add_class("dim-label")
        grid.attach(days_header, 2, 2, 1, 1)

        controls: dict[CareKind, tuple[Gtk.CheckButton, Gtk.SpinButton]] = {}
        for row_index, kind in enumerate(CARE_KINDS, start=3):
            task = task_by_kind[kind]
            enabled = Gtk.CheckButton(label=care_kind_label(kind))
            enabled.set_active(task.enabled)
            interval = Gtk.SpinButton()
            interval.set_adjustment(
                Gtk.Adjustment(task.interval_days, 1, 3650, 1, 7, 0)
            )
            interval.set_numeric(True)
            interval.set_sensitive(task.enabled)
            enabled.connect(
                "toggled",
                lambda widget, spin=interval: spin.set_sensitive(widget.get_active()),
            )
            grid.attach(enabled, 0, row_index, 2, 1)
            grid.attach(interval, 2, row_index, 1, 1)
            controls[kind] = (enabled, interval)

        def on_response(_dialog: Gtk.Dialog, response_id: int) -> None:
            if response_id == Gtk.ResponseType.OK:
                tasks = tuple(
                    self._task_from_editor(
                        original=task_by_kind[kind],
                        enabled=controls[kind][0].get_active(),
                        interval_days=controls[kind][1].get_value_as_int(),
                        today=today,
                    )
                    for kind in CARE_KINDS
                )
                updated = new_plant(
                    name=name_entry.get_text(),
                    species=species_entry.get_text(),
                    today=today,
                    tasks=tasks,
                    plant_id=existing.id,
                )
                self._upsert_plant(original=plant, updated=updated)
            elif response_id == Gtk.ResponseType.REJECT and plant is not None:
                self._state = remove_plant(state=self._state, plant_id=plant.id)
                self._save_and_refresh()
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.show_all()
        name_entry.grab_focus()

    @staticmethod
    def _task_from_editor(
        *,
        original: CareTask,
        enabled: bool,
        interval_days: int,
        today: dt.date,
    ) -> CareTask:
        newly_enabled = enabled and not original.enabled
        return CareTask(
            kind=original.kind,
            interval_days=interval_days,
            last_completed=today if newly_enabled else original.last_completed,
            enabled=enabled,
            snoozed_until=original.snoozed_until if enabled else None,
        )

    def _upsert_plant(
        self,
        *,
        original: Plant | None,
        updated: Plant,
    ) -> None:
        today = self._today()
        if original is None:
            self._state = add_plant(
                state=self._state,
                plant=updated,
                today=today,
            )
        else:
            self._state = replace_plant(
                state=self._state,
                plant_id=original.id,
                plant=updated,
                today=today,
            )
        self._save_and_refresh()

    @staticmethod
    def _today() -> dt.date:
        return dt.datetime.now().astimezone().date()
