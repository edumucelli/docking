"""Expose the built-in result and action providers used by global search.

Providers translate toolkit-free requests into immutable batches. Each owns a
stable ``provider_id``, all result and action identities in that namespace, and
the side effects required to invoke those actions. They do not merge results,
manage selection, create GTK widgets, or decide whether another provider
should run. Those responsibilities belong to the coordinator and controller.
"""

from docking.search.providers.applications import ApplicationSearchProvider
from docking.search.providers.base import InvokableSearchProvider
from docking.search.providers.calculator import CalculatorSearchProvider
from docking.search.providers.converter import ConverterSearchProvider
from docking.search.providers.dock import DockSearchProvider
from docking.search.providers.path import PathSearchProvider
from docking.search.providers.recent import RecentFilesSearchProvider
from docking.search.providers.temporal import TemporalSearchProvider
from docking.search.providers.web import WebSearchProvider
from docking.search.providers.windows import WindowSearchProvider

__all__ = [
    "ApplicationSearchProvider",
    "CalculatorSearchProvider",
    "ConverterSearchProvider",
    "DockSearchProvider",
    "InvokableSearchProvider",
    "PathSearchProvider",
    "RecentFilesSearchProvider",
    "TemporalSearchProvider",
    "WebSearchProvider",
    "WindowSearchProvider",
]
