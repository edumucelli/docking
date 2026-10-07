"""Cross-consumer checks for one shared application registry generation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.applets.runcommand.applet as runcommand_applet_mod
from docking.applets.applications.state import build_app_categories
from docking.applets.runcommand.applet import RunCommandApplet
from docking.search.coordinator import SearchCancellation, SearchRequest
from docking.search.providers.applications import ApplicationSearchProvider
from docking.search.types import SearchQuery
from tests.platform.application_fakes import (
    ApplicationRegistryHarness,
    GioApplicationFake,
)


class _ApplicationRow:
    def __init__(self, app) -> None:
        self.app = app
        self.child = None

    def add(self, child) -> None:
        self.child = child


class _ApplicationList:
    def __init__(self) -> None:
        self.children: list[_ApplicationRow] = []

    def get_children(self) -> list[_ApplicationRow]:
        return list(self.children)

    def remove(self, child: _ApplicationRow) -> None:
        self.children.remove(child)

    def add(self, child: _ApplicationRow) -> None:
        self.children.append(child)


def _request(text: str, generation: int) -> SearchRequest:
    return SearchRequest(
        query=SearchQuery(text=text, limit=20),
        generation=generation,
        cancellation=SearchCancellation(generation),
    )


def _application_ids_from_categories(registry) -> set[str]:
    return {
        application.desktop_id
        for applications in build_app_categories(registry).values()
        for application in applications
    }


def test_registry_generation_reaches_applications_run_and_search_consumers(
    monkeypatch,
):
    alpha = GioApplicationFake(
        "alpha.desktop",
        name="Alpha Editor",
        icon="alpha",
        categories="Utility;",
    )
    beta = GioApplicationFake(
        "beta.desktop",
        name="Beta Browser",
        icon="beta",
        categories="Network;",
    )
    harness = ApplicationRegistryHarness((alpha,))
    registry = harness.registry

    monkeypatch.setattr(runcommand_applet_mod, "_ApplicationRow", _ApplicationRow)
    run_applet = object.__new__(RunCommandApplet)
    run_applet._application_registry = registry
    run_applet._app_list = _ApplicationList()
    run_applet._entry = None
    run_applet._apps = []
    run_applet._app_rows = []
    run_applet._build_app_row = lambda application: application
    run_applet._apply_app_filter = lambda _query: None

    model = SimpleNamespace(visible_items=MagicMock(return_value=[]))
    windows = SimpleNamespace(list_windows=MagicMock(return_value=()))
    search = ApplicationSearchProvider(
        registry=registry,
        application_launcher=MagicMock(),
        target_service=MagicMock(),
        model=model,
        windows=windows,
        recent_docs_limit=5,
    )

    run_applet._refresh_app_list()
    alpha_results = next(search.search(_request("Alpha", 1))).results

    assert _application_ids_from_categories(registry) == {"alpha.desktop"}
    assert [application.desktop_id for application in run_applet._apps] == [
        "alpha.desktop"
    ]
    assert [result.identity.key for result in alpha_results] == ["alpha.desktop"]

    harness.publish((beta,))
    run_applet._refresh_app_list()
    removed_results = next(search.search(_request("Alpha", 2))).results
    beta_results = next(search.search(_request("Beta", 3))).results

    assert _application_ids_from_categories(registry) == {"beta.desktop"}
    assert [application.desktop_id for application in run_applet._apps] == [
        "beta.desktop"
    ]
    assert removed_results == ()
    assert [result.identity.key for result in beta_results] == ["beta.desktop"]
