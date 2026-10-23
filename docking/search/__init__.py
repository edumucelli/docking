"""Docking's process-wide, provider-based global search subsystem.

The package is split into deliberately narrow layers. Recognizers interpret
structured text without GTK or side effects. Providers turn a request into
immutable result batches and own the actions for those results. The
coordinator merges those batches with generation-safe cancellation, stable
selection, deterministic ranking, and cross-provider deduplication. Stateful
catalogs and shortcut integrations live under :mod:`docking.search.services`,
while :mod:`docking.search.ui` owns the GTK presentation.

The :class:`~docking.search.controller.GlobalSearchController` is the only
component that joins all layers. Code outside this package should normally use
the small presenter protocol or the runtime controller instead of importing a
provider, catalog, or GTK window directly.
"""
