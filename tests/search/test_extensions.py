"""Tests for third-party search provider discovery."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.search.extensions import (
    SEARCH_PROVIDER_ENTRY_POINT,
    SearchProviderContext,
    load_search_provider_extensions,
)


class _Provider:
    provider_id = "example"

    def search(self, _request):
        return ()

    def invoke(self, **_kwargs):
        return True


class _InvalidProvider:
    provider_id = " invalid "

    def search(self, _request):
        return ()


class _EntryPoint:
    def __init__(self, name, loaded) -> None:
        self.name = name
        self._loaded = loaded

    def load(self):
        if isinstance(self._loaded, Exception):
            raise self._loaded
        return self._loaded


def _context() -> SearchProviderContext:
    return SearchProviderContext(
        config=MagicMock(),
        launcher=MagicMock(),
        model=MagicMock(),
        windows=MagicMock(),
        copy_text=MagicMock(),
        schedule_idle=MagicMock(),
    )


def test_provider_entry_point_group_is_stable() -> None:
    assert SEARCH_PROVIDER_ENTRY_POINT == "docking.search_providers"


def test_extension_factories_receive_context_and_failures_are_isolated() -> None:
    factory = MagicMock(return_value=_Provider())
    providers = load_search_provider_extensions(
        context=_context(),
        entry_points=(
            _EntryPoint("valid", factory),
            _EntryPoint("broken", RuntimeError("broken")),
            _EntryPoint("not-callable", object()),
            _EntryPoint("invalid-provider", lambda _context: _InvalidProvider()),
            _EntryPoint("duplicate", lambda _context: _Provider()),
        ),
    )

    assert len(providers) == 1
    assert providers[0].provider_id == "example"
    factory.assert_called_once()
