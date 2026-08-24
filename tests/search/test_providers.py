"""Focused behavior tests for Docking's built-in search providers."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, call

from docking.core.items import APP_KIND, APPLET_KIND, FILE_KIND
from docking.platform.applications.recent_documents import RecentDocument
from docking.platform.applications.types import (
    ActionSource,
    ApplicationAction,
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
)
from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    WindowId,
    WindowSnapshot,
)
from docking.platform.targets import FileTargetInfo
from docking.search.coordinator import SearchCancellation, SearchRequest
from docking.search.providers import (
    ApplicationSearchProvider,
    CalculatorSearchProvider,
    ConverterSearchProvider,
    DockSearchProvider,
    PathSearchProvider,
    RecentFilesSearchProvider,
    TemporalSearchProvider,
    WebSearchProvider,
    WindowSearchProvider,
)
from docking.search.recognizers.calculation import recognize_calculation
from docking.search.recognizers.conversion import (
    parse_currency_conversion,
    parse_unit_conversion,
)
from docking.search.recognizers.temporal import parse_temporal_query
from docking.search.services.currency_rates import (
    CurrencyRatesCatalog,
    CurrencyRatesState,
)
from docking.search.services.recent_files import RecentFileSnapshot
from docking.search.types import SearchQuery


def _request(
    text: str,
    generation: int = 1,
    context: tuple[tuple[str, str], ...] = (),
    recognized: object | None = None,
) -> SearchRequest:
    return SearchRequest(
        query=SearchQuery(text=text, limit=20, context=context),
        generation=generation,
        cancellation=SearchCancellation(generation),
        recognized=recognized,
    )


def _results(provider, text: str, context=(), recognized=None):
    request = _request(text, context=context, recognized=recognized)
    return tuple(next(iter(provider.search(request))).results)


def _application(**changes) -> ApplicationInfo:
    application = ApplicationInfo(
        desktop_id="org.example.App.desktop",
        name="Example App",
        declared_icon="org.example.App",
        wm_class="ExampleApp",
        exec_line="example-app",
        origin=ApplicationOrigin.INSTALLED,
        location=ApplicationLocation.SANDBOX,
        desktop_file=Path("/usr/share/applications/org.example.App.desktop"),
        executable_path=None,
        aliases=("exampleapp",),
        visible=True,
        has_gio_source=True,
    )
    return replace(application, **changes)


class _Windows:
    def __init__(self, rows=()) -> None:
        self.rows = tuple(rows)
        self.activated = []
        self.closed = []

    def list_all_windows(self):
        return self.rows

    def list_windows(self, desktop_id):
        return tuple(row for row in self.rows if row.desktop_id == desktop_id)

    def icon_name_for_desktop(self, desktop_id):
        return f"icon-{desktop_id}"

    def activate_most_recent(self, desktop_id):
        self.activated.append(desktop_id)
        return ActionResult.OK

    def activate(self, window_id):
        self.activated.append(window_id)
        return ActionResult.OK

    def close(self, window_id):
        self.closed.append(window_id)
        return ActionResult.OK

    def close_all(self, desktop_id):
        self.closed.append(desktop_id)
        return ActionResult.OK


def test_application_provider_merges_dock_state_and_actions(monkeypatch) -> None:
    app = ApplicationInfo(
        desktop_id="firefox.desktop",
        name="Firefox",
        declared_icon="firefox",
        wm_class="Firefox",
        exec_line="firefox",
        origin=ApplicationOrigin.INSTALLED,
        location=ApplicationLocation.SANDBOX,
        desktop_file=Path("/usr/share/applications/firefox.desktop"),
        executable_path=None,
        aliases=("firefox",),
        visible=True,
        has_gio_source=True,
        description="Web Browser",
        categories=("Network",),
        keywords=("browser",),
        actions=(
            ApplicationAction(
                action_id="private",
                name="New Private Window",
                sources=frozenset({ActionSource.GIO}),
            ),
        ),
    )
    registry = SimpleNamespace(
        snapshot=lambda: (app,),
        get=lambda desktop_id: app if desktop_id == app.desktop_id else None,
    )
    dock_item = SimpleNamespace(
        desktop_id="firefox.desktop",
        kind=APP_KIND,
        is_pinned=True,
        is_running=True,
        is_recent=False,
    )
    model = SimpleNamespace(
        visible_items=lambda: [dock_item],
        pin_application=MagicMock(return_value=True),
        unpin_item=MagicMock(),
    )
    window = WindowSnapshot(
        id=WindowId(DisplayServer.X11, 5),
        desktop_id="firefox.desktop",
        title="Firefox",
        can_activate=True,
        can_close=True,
    )
    windows = _Windows((window,))
    application_launcher = MagicMock()
    application_launcher.launch.return_value = True
    application_launcher.launch_new_window.return_value = True
    application_launcher.launch_action.return_value = True
    target_service = MagicMock()
    target_service.open_target.return_value = True
    provider = ApplicationSearchProvider(
        registry=cast(Any, registry),
        application_launcher=application_launcher,
        target_service=target_service,
        model=cast(Any, model),
        windows=cast(Any, windows),
        recent_docs_limit=5,
    )
    seen_recent_applications = []

    def recent_docs(application, **_kwargs):
        seen_recent_applications.append(application)
        return [
            RecentDocument(
                uri="file:///tmp/guide.pdf",
                name="guide.pdf",
                mime_type="application/pdf",
                modified=10,
            )
        ]

    monkeypatch.setattr(
        "docking.search.providers.applications.recent_documents_for",
        recent_docs,
    )

    result = _results(provider, "fire")[0]

    assert result.title == "Firefox"
    assert result.state == "Running · Pinned"
    assert [action.label for action in result.actions] == [
        "Focus",
        "Open New Window",
        "New Private Window",
        "Remove from Dock",
        "Close",
    ]
    assert provider.invoke(
        result_identity=result.identity,
        action_identity=result.actions[0].identity,
    )
    assert windows.activated == ["firefox.desktop"]

    refined = provider.refine(result)
    assert "Activate “Firefox”" in [item.label for item in refined.actions]
    assert "Open Recent: guide.pdf" in [item.label for item in refined.actions]
    open_recent = next(
        item for item in refined.actions if item.label == "Open Recent: guide.pdf"
    )
    assert provider.invoke(
        result_identity=refined.identity,
        action_identity=open_recent.identity,
    )
    target_service.open_target.assert_called_once_with("file:///tmp/guide.pdf")
    preview = provider.build_preview(result)
    assert "Desktop ID: firefox.desktop" in preview.body
    assert "Windows\n• Firefox" in preview.body
    assert "Recent Documents\n• guide.pdf" in preview.body
    assert seen_recent_applications == [app, app]


def test_application_provider_uses_canonical_search_projection_order(
    monkeypatch,
) -> None:
    alpha = _application(
        desktop_id="alpha.desktop",
        name="  Café   Writer ",
        declared_icon="file:///opt/writer.svg",
        description="  Write   documents ",
        categories=(" Office ", "office", "Utility"),
        keywords=(" Writer ", "writer", "Documents"),
    )
    zeta = _application(
        desktop_id="zeta.desktop",
        name="Zeta",
        declared_icon="resource://org/example/zeta",
    )
    registry = SimpleNamespace(
        snapshot=lambda: (zeta, alpha),
        get=lambda desktop_id: {
            alpha.desktop_id: alpha,
            zeta.desktop_id: zeta,
        }.get(desktop_id),
    )
    items = [
        SimpleNamespace(
            desktop_id=application.desktop_id,
            kind=APP_KIND,
            is_pinned=True,
            is_running=False,
            is_recent=False,
        )
        for application in (alpha, zeta)
    ]
    provider = ApplicationSearchProvider(
        registry=cast(Any, registry),
        application_launcher=MagicMock(),
        target_service=MagicMock(),
        model=cast(Any, SimpleNamespace(visible_items=lambda: items)),
        windows=cast(Any, _Windows()),
        recent_docs_limit=5,
    )
    monkeypatch.setattr(
        "docking.search.providers.applications.recent_documents_for",
        lambda *_args, **_kwargs: [],
    )

    results = _results(provider, "")

    assert [result.identity.key for result in results] == [
        "alpha.desktop",
        "zeta.desktop",
    ]
    assert results[0].title == "Café Writer"
    assert results[0].description == "Write documents"
    assert results[0].icon_name == "/opt/writer.svg"
    assert results[0].keywords == ("Writer", "Documents")
    assert "Categories: Office, Utility" in provider.build_preview(results[0]).body


def test_application_provider_dispatches_through_injected_services(
    monkeypatch,
) -> None:
    application = _application(
        actions=(
            ApplicationAction(
                action_id="gio-action",
                name="Gio Action",
                sources=frozenset({ActionSource.GIO}),
            ),
            ApplicationAction(
                action_id="file-only",
                name="File Only",
                sources=frozenset({ActionSource.DESKTOP_FILE}),
                file_exec_line="example-app --file-only",
            ),
        )
    )
    registry = SimpleNamespace(
        snapshot=lambda: (application,),
        get=lambda _desktop_id: application,
    )
    model = SimpleNamespace(
        visible_items=list,
        pin_application=MagicMock(return_value=True),
        unpin_item=MagicMock(),
    )
    application_launcher = MagicMock()
    application_launcher.launch.return_value = True
    application_launcher.launch_new_window.return_value = True
    application_launcher.launch_action.return_value = True
    target_service = MagicMock()
    target_service.open_target.return_value = True
    provider = ApplicationSearchProvider(
        registry=cast(Any, registry),
        application_launcher=application_launcher,
        target_service=target_service,
        model=cast(Any, model),
        windows=cast(Any, _Windows()),
        recent_docs_limit=5,
    )
    document = RecentDocument(
        uri="file:///tmp/report.txt",
        name="report.txt",
        mime_type="text/plain",
        modified=10,
    )
    monkeypatch.setattr(
        "docking.search.providers.applications.recent_documents_for",
        lambda *_args, **_kwargs: [document],
    )
    result = _results(provider, "example")[0]
    refined = provider.refine(result)

    actions = {item.label: item for item in refined.actions}
    for label in ("Open", "Open New Window", "Gio Action", "File Only"):
        assert provider.invoke(
            result_identity=result.identity,
            action_identity=actions[label].identity,
        )
    assert provider.invoke(
        result_identity=result.identity,
        action_identity=actions["Open Recent: report.txt"].identity,
    )

    application_launcher.launch.assert_called_once_with(application.desktop_id)
    application_launcher.launch_new_window.assert_called_once_with(
        application.desktop_id
    )
    assert application_launcher.launch_action.call_args_list == [
        call(application.desktop_id, "gio-action"),
        call(application.desktop_id, "file-only"),
    ]
    target_service.open_target.assert_called_once_with(document.uri)


def test_dock_provider_opens_applet_and_removes_file() -> None:
    applet = MagicMock()
    applet_item = SimpleNamespace(
        desktop_id="applet://clock",
        target="applet://clock",
        name="Clock",
        icon_name="clock",
        kind=APPLET_KIND,
        is_pinned=True,
    )
    file_item = SimpleNamespace(
        desktop_id="file:///tmp/report.txt",
        target="file:///tmp/report.txt",
        name="report.txt",
        icon_name="text-x-generic",
        kind=FILE_KIND,
        is_pinned=True,
    )
    model = SimpleNamespace(
        visible_items=lambda: [applet_item, file_item],
        get_applet=lambda _desktop_id: applet,
        find_by_desktop_id=lambda desktop_id: {
            applet_item.desktop_id: applet_item,
            file_item.desktop_id: file_item,
        }.get(desktop_id),
        unpin_item=MagicMock(),
    )
    target_service = MagicMock()
    target_service.open_target.return_value = True
    provider = DockSearchProvider(
        model=cast(Any, model),
        target_service=target_service,
    )

    result = _results(provider, "clock")[0]
    assert provider.invoke(
        result_identity=result.identity,
        action_identity=result.actions[0].identity,
    )
    applet.on_clicked.assert_called_once_with()
    assert {item.title for item in _results(provider, "")} == {
        "Clock",
        "report.txt",
    }


def test_window_provider_activates_title_match() -> None:
    window = WindowSnapshot(
        id=WindowId(DisplayServer.WAYLAND, "42"),
        desktop_id="org.example.Editor.desktop",
        title="Global search plan",
        active=True,
        can_activate=True,
        can_close=True,
        can_preview=True,
    )
    windows = _Windows((window,))
    provider = WindowSearchProvider(windows=cast(Any, windows))

    result = _results(provider, "global plan")[0]

    assert result.state == "Active"
    assert result.preview is not None
    assert result.preview.kind == "window"
    assert provider.invoke(
        result_identity=result.identity,
        action_identity=result.actions[0].identity,
    )
    assert windows.activated == [window.id]
    assert _results(provider, "") == ()


def test_calculator_provider_uses_recognized_value_and_copies() -> None:
    copied: list[str] = []
    provider = CalculatorSearchProvider(copy_text=copied.append)

    recognized = recognize_calculation("2 + 2")
    assert recognized is not None
    error = _results(provider, "= 2 +")[0]
    assert error.title == "Invalid expression"
    assert error.actions == ()
    result = _results(provider, "2 + 2", recognized=recognized)[0]
    assert result.title == "4"
    assert provider.invoke(
        result_identity=result.identity,
        action_identity=result.actions[0].identity,
    )
    assert copied == ["4"]


def test_converter_provider_copies_implicit_result() -> None:
    copied: list[str] = []
    provider = ConverterSearchProvider(
        copy_text=copied.append,
        currency_rates=MagicMock(),
    )

    result = _results(provider, "10 km to mi")[0]

    assert result.title.startswith("6.213")
    assert provider.invoke(
        result_identity=result.identity,
        action_identity=result.actions[0].identity,
    )
    assert copied == [result.title]


def test_converter_provider_reuses_intent_recognition(monkeypatch) -> None:
    provider = ConverterSearchProvider(
        copy_text=MagicMock(),
        currency_rates=MagicMock(),
    )
    conversion = parse_unit_conversion("10 km to mi")
    assert conversion is not None
    parse_again = MagicMock(side_effect=AssertionError("parsed twice"))
    monkeypatch.setattr(
        "docking.search.providers.converter.parse_unit_conversion",
        parse_again,
    )

    result = _results(
        provider,
        "10 km to mi",
        recognized=conversion,
    )[0]

    assert result.title.startswith("6.213")
    parse_again.assert_not_called()


def test_converter_provider_reuses_currency_recognition(monkeypatch) -> None:
    rates = MagicMock()
    rates.state = CurrencyRatesState.ERROR
    provider = ConverterSearchProvider(
        copy_text=MagicMock(),
        currency_rates=rates,
    )
    conversion = parse_currency_conversion("10 USD to EUR")
    assert conversion is not None
    parse_unit_again = MagicMock(side_effect=AssertionError("parsed twice"))
    parse_currency_again = MagicMock(side_effect=AssertionError("parsed twice"))
    monkeypatch.setattr(
        "docking.search.providers.converter.parse_unit_conversion",
        parse_unit_again,
    )
    monkeypatch.setattr(
        "docking.search.providers.converter.parse_currency_conversion",
        parse_currency_again,
    )

    result = _results(
        provider,
        "10 USD to EUR",
        recognized=conversion,
    )[0]

    assert result.title == "Currency rates unavailable"
    parse_unit_again.assert_not_called()
    parse_currency_again.assert_not_called()


def test_converter_provider_uses_live_currency_factors() -> None:
    from docking.applets.unitconverter.state import Unit

    copied: list[str] = []
    rates = CurrencyRatesCatalog(
        schedule_idle=lambda _callback, *_args: 1,
    )
    rates._state = CurrencyRatesState.READY
    rates._units = (
        Unit("Euro", "EUR", 1.0),
        Unit("US Dollar", "USD", 0.8),
    )
    provider = ConverterSearchProvider(
        copy_text=copied.append,
        currency_rates=rates,
    )

    result = _results(provider, "10 USD to EUR")[0]

    assert result.title == "8 EUR"
    assert result.state == "Currency"


def test_web_provider_detects_urls_and_uses_selected_engine() -> None:
    copied: list[str] = []
    opened: list[str] = []
    target_service = MagicMock()
    target_service.open_target.side_effect = lambda target: not opened.append(target)
    provider = WebSearchProvider(
        target_service=target_service,
        copy_text=copied.append,
    )
    direct = _results(provider, "docs.python.org")[0]
    assert direct.title == "Open URL"
    assert provider.invoke(
        result_identity=direct.identity,
        action_identity=direct.actions[0].identity,
    )
    assert opened == ["https://docs.python.org"]

    web = _results(
        provider,
        "docking linux",
        context=(("web_engine", "google"), ("question_like", "true")),
    )[0]
    assert web.state == "Google"
    assert web.score == 250


def test_web_provider_reuses_recognized_target(monkeypatch) -> None:
    provider = WebSearchProvider(
        target_service=MagicMock(),
        copy_text=MagicMock(),
    )
    normalize_again = MagicMock(side_effect=AssertionError("parsed twice"))
    monkeypatch.setattr(
        "docking.search.providers.web.normalize_web_target",
        normalize_again,
    )

    result = _results(
        provider,
        "docs.python.org",
        context=(("intent_kind", "url"),),
        recognized="https://docs.python.org",
    )[0]

    assert result.title == "Open URL"
    normalize_again.assert_not_called()


def test_temporal_provider_copies_detected_date() -> None:
    copied: list[str] = []
    provider = TemporalSearchProvider(copy_text=copied.append)

    result = _results(provider, "2026-07-28")[0]

    assert result.title == dt.date(2026, 7, 28).strftime("%A, %x")
    assert provider.invoke(
        result_identity=result.identity,
        action_identity=result.actions[0].identity,
    )
    assert copied == ["2026-07-28"]


def test_temporal_provider_reuses_intent_recognition(monkeypatch) -> None:
    provider = TemporalSearchProvider(copy_text=MagicMock())
    temporal = parse_temporal_query("2026-07-28")
    assert temporal is not None
    parse_again = MagicMock(side_effect=AssertionError("parsed twice"))
    monkeypatch.setattr(
        "docking.search.providers.temporal.parse_temporal_query",
        parse_again,
    )

    result = _results(
        provider,
        "2026-07-28",
        recognized=temporal,
    )[0]

    assert result.title == dt.date(2026, 7, 28).strftime("%A, %x")
    parse_again.assert_not_called()


def test_recent_file_provider_searches_and_opens() -> None:
    entry = RecentFileSnapshot(
        name="Proposal.pdf",
        uri="file:///tmp/Proposal.pdf",
        modified=10,
        mime_type="application/pdf",
    )
    catalog = SimpleNamespace(
        snapshot=lambda: (entry,),
        open_uri=MagicMock(return_value=True),
    )
    provider = RecentFilesSearchProvider(catalog=cast(Any, catalog))

    result = _results(provider, "proposal")[0]
    assert provider.invoke(
        result_identity=result.identity,
        action_identity=result.actions[0].identity,
    )
    catalog.open_uri.assert_called_once_with(entry.uri)
    assert _results(provider, "") == ()


def test_direct_path_provider_uses_existing_path(tmp_path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("notes")
    target = path.resolve().as_uri()
    target_service = SimpleNamespace(
        normalize_file_target=lambda value: (
            Path(value).expanduser().resolve().as_uri()
            if not value.startswith("file://")
            else value
        ),
        resolve_file=lambda **_kwargs: FileTargetInfo(
            target=target,
            name="notes.txt",
            icon_name="text-x-generic",
            icon=None,
            is_dir=False,
        ),
        open_target=lambda value: value == target,
    )
    copied: list[str] = []
    provider = PathSearchProvider(
        target_service=cast(Any, target_service),
        copy_text=copied.append,
        icon_size=48,
    )

    result = _results(provider, str(path))[0]
    assert result.score == 1_100
    assert provider.invoke(
        result_identity=result.identity,
        action_identity=result.actions[1].identity,
    )
    assert copied == [str(path.resolve())]
