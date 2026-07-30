"""Stable entry-point API for third-party Docking Search providers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib import metadata
from typing import TYPE_CHECKING, Protocol

from docking.log import get_logger
from docking.search.coordinator import SearchProvider
from docking.search.types import SearchIdentity

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.backends.base import WindowService
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel

SEARCH_PROVIDER_ENTRY_POINT = "docking.search_providers"
log = get_logger("search.extensions")


@dataclass(frozen=True, slots=True)
class SearchProviderContext:
    """Services exposed to trusted installed provider factories."""

    config: Config
    launcher: Launcher
    model: DockModel
    windows: WindowService
    copy_text: Callable[[str], None]
    schedule_idle: Callable[..., int]


class SearchProviderFactory(Protocol):
    def __call__(
        self,
        context: SearchProviderContext,
    ) -> ExtensionSearchProvider: ...


class ExtensionSearchProvider(SearchProvider, Protocol):
    def invoke(
        self,
        *,
        result_identity: SearchIdentity,
        action_identity: SearchIdentity,
    ) -> bool: ...


def _provider_entry_points() -> Iterable[metadata.EntryPoint]:
    discovered = metadata.entry_points()
    select = getattr(discovered, "select", None)
    if callable(select):
        return select(group=SEARCH_PROVIDER_ENTRY_POINT)
    return discovered.get(SEARCH_PROVIDER_ENTRY_POINT, ())  # type: ignore[union-attr]


def load_search_provider_extensions(
    *,
    context: SearchProviderContext,
    entry_points: Iterable[metadata.EntryPoint] | None = None,
) -> tuple[ExtensionSearchProvider, ...]:
    """Load isolated provider factories while containing extension failures."""
    providers: list[ExtensionSearchProvider] = []
    provider_ids: set[str] = set()
    discovered = _provider_entry_points() if entry_points is None else entry_points
    for entry_point in discovered:
        try:
            factory = entry_point.load()
            if not callable(factory):
                raise TypeError("entry point is not callable")
            provider = factory(context)
            provider_id = getattr(provider, "provider_id", "")
            if (
                not isinstance(provider_id, str)
                or not provider_id.strip()
                or provider_id != provider_id.strip()
            ):
                raise ValueError("provider_id must be non-empty and normalized")
            if provider_id in provider_ids:
                raise ValueError(f"duplicate provider_id: {provider_id}")
            if not callable(getattr(provider, "search", None)) or not callable(
                getattr(provider, "invoke", None)
            ):
                raise TypeError("provider must define callable search and invoke")
        except Exception as exc:
            log.warning(
                "Failed to load search provider extension %s: %s",
                entry_point.name,
                exc,
            )
            continue
        provider_ids.add(provider_id)
        providers.append(provider)
    return tuple(providers)


__all__ = [
    "SEARCH_PROVIDER_ENTRY_POINT",
    "ExtensionSearchProvider",
    "SearchProviderContext",
    "SearchProviderFactory",
    "load_search_provider_extensions",
]
