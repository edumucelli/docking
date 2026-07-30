"""Lifecycle and routing tests for GlobalSearchController."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

from gi.repository import GdkPixbuf

from docking.core.config import Config
from docking.core.items import APPLET_KIND
from docking.platform.application_catalog import ApplicationSnapshot, IconDescriptor
from docking.platform.backends.base import (
    DisplayServer,
    PreviewImage,
    WindowId,
    WindowSnapshot,
)
from docking.platform.global_shortcuts import (
    GlobalShortcutBinding,
    GlobalShortcutsState,
    GlobalShortcutsStatus,
)
from docking.search.controller import GlobalSearchController
from docking.search.types import (
    SearchAction,
    SearchIdentity,
    SearchPreview,
    SearchResult,
)


class _Catalog:
    def __init__(self, values=()) -> None:
        self.values = tuple(values)
        self.listeners = []
        self.started = False

    def snapshot(self):
        return self.values

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def add_listener(self, callback):
        self.listeners.append(callback)

    def remove_listener(self, callback):
        self.listeners.remove(callback)


class _Window:
    def __init__(self, **callbacks) -> None:
        self.callbacks = callbacks
        self.visible = False
        self.snapshots = []
        self.queries = []
        self.hints = []
        self.refinements = []
        self.completed_queries = []

    def present(self, *, initial_query="", activation_context=None):
        self.visible = True
        self.queries.append((initial_query, activation_context))

    def hide(self):
        self.visible = False
        self.callbacks["on_hidden"]()

    def destroy(self):
        self.visible = False

    def update(self, snapshot):
        self.snapshots.append(snapshot)

    def set_query_hint(self, hint):
        self.hints.append(hint)

    def show_actions_for(self, result):
        self.refinements.append(result)

    def set_query(self, query):
        self.completed_queries.append(query)


class _Usage:
    def boost(self, _result, _query):
        return 0

    def rank_actions(self, actions):
        return actions

    def record(self, **_kwargs):
        return None


def _make_controller(
    monkeypatch,
    *,
    schedule_idle=None,
    model_items=None,
    shortcut_fallback=None,
    currency_rates=None,
    usage_store=None,
    script_catalog=None,
):
    created = []

    def _window_factory(**kwargs):
        window = _Window(**kwargs)
        created.append(window)
        return window

    monkeypatch.setattr(
        "docking.search.controller.SearchWindow",
        _window_factory,
    )
    app = ApplicationSnapshot(
        desktop_id="firefox.desktop",
        name="Firefox",
        normalized_name="firefox",
        categories=("Network",),
        icon=IconDescriptor("themed", "firefox"),
    )
    applications = _Catalog((app,))
    recent = _Catalog()
    model_listeners = []
    items = [] if model_items is None else model_items
    model = SimpleNamespace(
        visible_items=lambda: list(items),
        add_change_listener=model_listeners.append,
        remove_change_listener=model_listeners.remove,
        find_by_desktop_id=lambda **_kwargs: None,
        get_applet=lambda _desktop_id: None,
        pin_application=MagicMock(return_value=True),
        unpin_item=MagicMock(),
    )
    windows = SimpleNamespace(
        list_all_windows=lambda: (),
        list_windows=lambda _desktop_id: (),
        icon_name_for_desktop=lambda _desktop_id: "application-x-executable",
    )
    shortcuts = MagicMock()
    config = Config(global_search_providers=["applications"])

    if schedule_idle is None:

        def schedule_idle(callback, *args):
            callback(*args)
            return 1

    controller = GlobalSearchController(
        config=config,
        launcher=MagicMock(),
        model=cast(Any, model),
        windows=cast(Any, windows),
        application_catalog=cast(Any, applications),
        recent_files=cast(Any, recent),
        global_shortcuts=shortcuts,
        shortcut_fallback=shortcut_fallback,
        currency_rates=currency_rates,
        usage_store=cast(Any, usage_store or _Usage()),
        script_catalog=script_catalog,
        schedule_idle=schedule_idle,
    )
    return controller, created[0], applications, recent, shortcuts


def test_controller_starts_catalogs_and_searches(monkeypatch) -> None:
    controller, window, applications, recent, shortcuts = _make_controller(monkeypatch)

    controller.start()
    controller.show(initial_query="fire")

    assert applications.started
    assert recent.started
    shortcuts.start.assert_called_once_with()
    assert window.visible
    assert window.snapshots[-1].results[0].title == "Firefox"
    update_count = len(window.snapshots)
    controller._refresh_for_model_change()
    assert len(window.snapshots) == update_count

    controller.stop()
    shortcuts.stop.assert_called_once_with()
    assert not applications.started
    assert not recent.started


def test_controller_toggle_and_disabled_setting(monkeypatch) -> None:
    controller, window, _applications, _recent, _shortcuts = _make_controller(
        monkeypatch
    )
    controller._config.global_search_enabled = False

    controller.show()
    assert not window.visible

    controller._config.global_search_enabled = True
    controller.toggle(activation_context={"XDG_ACTIVATION_TOKEN": "token"})
    assert window.visible
    controller.toggle()
    assert not window.visible


def test_new_query_rejects_queued_stale_provider_work(monkeypatch) -> None:
    queued = []

    def schedule_idle(callback, *args):
        queued.append((callback, args))
        return len(queued)

    controller, window, _applications, _recent, _shortcuts = _make_controller(
        monkeypatch,
        schedule_idle=schedule_idle,
    )
    controller.show(initial_query="fire")
    window.callbacks["on_query_changed"]("fox")

    stale, current = queued
    stale[0](*stale[1])
    assert window.snapshots[-1].query.text == "fox"
    current[0](*current[1])
    assert window.snapshots[-1].results[0].title == "Firefox"


def test_replaced_coordinator_cannot_publish_stale_snapshot(monkeypatch) -> None:
    queued = []

    def schedule_idle(callback, *args):
        queued.append((callback, args))
        return len(queued)

    controller, window, _apps, _recent, _shortcuts = _make_controller(
        monkeypatch,
        schedule_idle=schedule_idle,
    )
    controller.show(initial_query="fire")
    previous = controller._coordinator
    stale_snapshot = previous.snapshot()
    controller._config.global_search_providers = ["calculator"]

    window.callbacks["on_query_changed"]("100 + 20")
    update_count = len(window.snapshots)
    controller._publish_snapshot(stale_snapshot, coordinator=previous)

    assert previous.request is not None
    assert previous.request.cancelled
    assert len(window.snapshots) == update_count


def test_action_revalidation_rejects_old_generation(monkeypatch) -> None:
    controller, window, _apps, _recent, _shortcuts = _make_controller(monkeypatch)
    controller.show(initial_query="fire")
    coordinator = controller._coordinator
    request = coordinator.request
    result = window.snapshots[-1].results[0]
    action = result.actions[0]
    assert request is not None
    assert controller._action_is_current(
        coordinator=coordinator,
        generation=request.generation,
        result_identity=result.identity,
        action_identity=action.identity,
    )

    window.callbacks["on_query_changed"]("fox")

    assert not controller._action_is_current(
        coordinator=coordinator,
        generation=request.generation,
        result_identity=result.identity,
        action_identity=action.identity,
    )


def test_live_applet_tooltip_updates_do_not_restart_search(monkeypatch) -> None:
    clock = SimpleNamespace(
        desktop_id="applet://clock",
        kind=APPLET_KIND,
        name="Clock\n12:30:00",
        target="applet://clock",
        icon_name="clock",
        is_pinned=True,
        is_running=False,
        is_recent=False,
    )
    controller, window, _applications, _recent, _shortcuts = _make_controller(
        monkeypatch,
        model_items=[clock],
    )
    controller.start()
    controller.show(initial_query="fire")
    update_count = len(window.snapshots)

    clock.name = "Clock\n12:30:02"
    controller._refresh_for_model_change()

    assert len(window.snapshots) == update_count


def test_shortcut_status_uses_concise_text_and_summary(
    monkeypatch,
) -> None:
    controller, _window, _applications, _recent, _shortcuts = _make_controller(
        monkeypatch
    )
    raw_error = "GDBus.Error:org.freedesktop.DBus.Error.InvalidArgs"
    controller._shortcut_status = GlobalShortcutsStatus(
        state=GlobalShortcutsState.UNAVAILABLE,
        message=raw_error,
    )

    assert controller.shortcut_status_text() == "Unavailable on this desktop"
    assert controller.shortcut_status_summary() == "Unavailable"

    controller._shortcut_status = GlobalShortcutsStatus(
        state=GlobalShortcutsState.BOUND,
        binding=GlobalShortcutBinding(
            shortcut_id="toggle-search",
            description="Toggle Docking search",
            trigger_description="Super+Space",
        ),
    )
    assert controller.shortcut_status_text() == "Assigned: Super+Space"
    assert controller.shortcut_status_summary() == "Active"


def test_x11_fallback_activates_when_portal_is_unavailable(monkeypatch) -> None:
    fallback = MagicMock()
    fallback.active = False
    fallback.error = None

    def start_fallback():
        fallback.active = True
        return True

    def stop_fallback():
        fallback.active = False

    fallback.start.side_effect = start_fallback
    fallback.stop.side_effect = stop_fallback
    controller, _window, _apps, _recent, shortcuts = _make_controller(
        monkeypatch,
        shortcut_fallback=fallback,
    )
    controller.start()

    controller._on_shortcut_status(
        GlobalShortcutsStatus(state=GlobalShortcutsState.UNAVAILABLE)
    )

    fallback.start.assert_called_once_with()
    assert controller.shortcut_status_text() == "Active: Ctrl+Super+Space (X11)"
    assert controller.shortcut_status_summary() == "Active"
    controller.suspend_shortcuts()
    assert not fallback.active
    controller.resume_shortcuts()
    assert shortcuts.start.call_count == 2


def test_x11_fallback_does_not_override_portal_denial(monkeypatch) -> None:
    fallback = MagicMock(active=False, error=None)
    controller, _window, _apps, _recent, _shortcuts = _make_controller(
        monkeypatch,
        shortcut_fallback=fallback,
    )
    controller.start()

    controller._on_shortcut_status(
        GlobalShortcutsStatus(state=GlobalShortcutsState.DENIED)
    )

    fallback.start.assert_not_called()


def test_x11_shortcut_conflict_is_actionable(monkeypatch) -> None:
    fallback = MagicMock()
    fallback.active = False
    fallback.error = "shortcut is already in use"
    controller, _window, _apps, _recent, _shortcuts = _make_controller(
        monkeypatch,
        shortcut_fallback=fallback,
    )
    controller._shortcut_status = GlobalShortcutsStatus(
        state=GlobalShortcutsState.UNAVAILABLE
    )

    assert controller.shortcut_status_text() == "Shortcut already in use"
    assert controller.shortcut_status_summary() == "Conflict"


def test_x11_activation_timestamp_reaches_search_window(monkeypatch) -> None:
    controller, window, _apps, _recent, _shortcuts = _make_controller(monkeypatch)

    controller._on_fallback_shortcut(4321)

    assert window.visible
    assert window.queries[-1][1] == {"timestamp": 4321}


def test_intent_routing_supports_implicit_math_and_web_fallback(
    monkeypatch,
) -> None:
    controller, window, _apps, _recent, _shortcuts = _make_controller(monkeypatch)
    controller._config.global_search_providers = ["applications", "calculator"]

    controller.show(initial_query="100 + 20")

    assert window.hints[-1] == "Calculator"
    assert window.snapshots[-1].results[0].title == "120"

    window.callbacks["on_query_changed"]("2026-07-28")
    assert window.hints[-1] == "Date & Time"
    assert window.snapshots[-1].results[0].state == "Date"

    controller._config.global_search_web_engine = "google"
    window.callbacks["on_query_changed"]("unlikely-local-match-xyz")
    assert window.snapshots[-1].results[0].source == "Web"
    assert window.snapshots[-1].results[0].state == "Google"


def test_explicit_provider_keyword_suppresses_web_fallback(monkeypatch) -> None:
    controller, window, _apps, _recent, _shortcuts = _make_controller(monkeypatch)

    controller.show(initial_query="app firefox")

    assert window.hints[-1] == "Applications"
    assert [result.source for result in window.snapshots[-1].results] == [
        "Applications"
    ]

    window.callbacks["on_query_changed"]("app")
    assert [result.title for result in window.snapshots[-1].results] == ["Firefox"]


def test_web_fallback_can_be_disabled(monkeypatch) -> None:
    controller, window, _apps, _recent, _shortcuts = _make_controller(monkeypatch)
    controller._config.global_search_web_fallback = False

    controller.show(initial_query="unlikely-local-match-xyz")

    assert window.snapshots[-1].results == ()


def test_currency_conversion_uses_ready_rate_catalog(monkeypatch) -> None:
    from docking.applets.unitconverter.state import Unit
    from docking.search.services.currency_rates import (
        CurrencyRatesCatalog,
        CurrencyRatesState,
    )

    rates = CurrencyRatesCatalog(
        schedule_idle=lambda _callback, *_args: 1,
    )
    rates._state = CurrencyRatesState.READY
    rates._units = (
        Unit("Euro", "EUR", 1.0),
        Unit("US Dollar", "USD", 0.8),
    )
    controller, window, _apps, _recent, _shortcuts = _make_controller(
        monkeypatch,
        currency_rates=rates,
    )
    controller._config.global_search_providers = ["calculator"]

    controller.show(initial_query="10 USD to EUR")

    assert window.hints[-1] == "Converter"
    assert window.snapshots[-1].results[0].title == "8 EUR"


def test_intent_recognition_is_forwarded_to_provider(monkeypatch) -> None:
    controller, window, _apps, _recent, _shortcuts = _make_controller(monkeypatch)
    controller._config.global_search_providers = ["calculator"]
    parse_again = MagicMock(side_effect=AssertionError("parsed twice"))
    monkeypatch.setattr(
        "docking.search.providers.converter.parse_unit_conversion",
        parse_again,
    )

    controller.show(initial_query="10 km to mi")

    assert window.snapshots[-1].results[0].title.startswith("6.213")
    parse_again.assert_not_called()


def test_tab_completes_query_keyword_before_refining_result(monkeypatch) -> None:
    controller, window, _apps, _recent, _shortcuts = _make_controller(monkeypatch)
    controller.show(initial_query="ap")

    assert controller._complete_query()

    assert window.completed_queries == ["app "]
    assert controller._current_query == "app "


def test_cmd_keyword_routes_only_to_user_script_provider(
    monkeypatch,
    tmp_path,
) -> None:
    from docking.search.services.script_commands import ScriptCommandCatalog

    script = tmp_path / "deploy"
    script.write_text(
        "#!/bin/sh\n# @docking.name Deploy Project\n# @docking.keyword deploy\n"
    )
    script.chmod(0o700)
    controller, window, _apps, _recent, _shortcuts = _make_controller(
        monkeypatch,
        script_catalog=ScriptCommandCatalog(directories=(tmp_path,)),
    )

    controller.show(initial_query="cmd deploy staging")

    assert window.hints[-1] == "Script Commands"
    assert window.snapshots[-1].results[0].title == "Deploy Project"


def test_live_window_preview_uses_backend_capture(monkeypatch) -> None:
    controller, _window, _apps, _recent, _shortcuts = _make_controller(monkeypatch)
    window_id = WindowId(DisplayServer.X11, 42)
    snapshot = WindowSnapshot(
        id=window_id,
        desktop_id="editor.desktop",
        title="Editor",
        can_preview=True,
    )
    controller._windows = SimpleNamespace(list_all_windows=lambda: (snapshot,))
    pixbuf = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB,
        False,
        8,
        280,
        160,
    )
    controller._preview_service = MagicMock(
        capture=MagicMock(
            return_value=PreviewImage(
                image=pixbuf,
                width=1280,
                height=720,
            )
        )
    )

    loaded = controller._load_dynamic_preview(
        SearchPreview(
            title="Editor",
            body="editor.desktop",
            kind="window",
            target=str(window_id),
        ),
        280,
        250,
    )

    assert loaded is not None
    assert loaded.pixbuf is pixbuf
    assert loaded.width == 1280
    assert loaded.height == 720


def test_provider_refine_and_invoke_failures_are_contained(monkeypatch) -> None:
    controller, window, _apps, _recent, _shortcuts = _make_controller(monkeypatch)
    provider = MagicMock()
    provider.refine.side_effect = RuntimeError("refine failed")
    provider.invoke.side_effect = RuntimeError("invoke failed")
    controller._provider_by_id["broken"] = provider
    result = SearchResult(
        identity=SearchIdentity("broken", "result"),
        title="Broken",
        actions=(
            SearchAction(
                identity=SearchIdentity("broken", "result/open"),
                label="Open",
            ),
        ),
    )

    controller._refine_result(result)
    controller._activate_action(result, result.actions[0])

    assert window.refinements == []
