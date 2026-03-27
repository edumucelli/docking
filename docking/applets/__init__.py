"""Applet catalog and lazy class loading via auto-discovery.

Docking ships many optional applets, but most sessions only instantiate a small
subset. This module discovers applet metadata from each package's ``__init__.py``
(cheap -- no GTK imports) and lazily loads the concrete applet class only when
one is actually needed.

Each applet package declares an ``AppletMeta`` instance as a module-level
``meta`` attribute in its ``__init__.py``. The catalog scans for these at
startup, giving menus and settings UI everything they need without importing
every applet implementation.
"""

from __future__ import annotations

import inspect
import logging
from functools import cache, lru_cache
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

from docking.applets.identity import AppletMeta

if TYPE_CHECKING:
    from docking.applets.base import Applet

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_applet_catalog() -> dict[str, AppletMeta]:
    """Discover applets by scanning package ``__init__.py`` for ``meta``."""
    applets_dir = Path(__file__).parent
    result: dict[str, AppletMeta] = {}
    for pkg in sorted(applets_dir.iterdir()):
        if not pkg.is_dir():
            log.debug("Skipping non-package applet entry %s", pkg)
            continue
        if pkg.name.startswith("_"):
            log.debug("Skipping private applet package %s", pkg.name)
            continue
        init_py = pkg / "__init__.py"
        if not init_py.exists():
            log.warning("Skipping %s: missing __init__.py", pkg)
            continue
        module_name = f"docking.applets.{pkg.name}"
        try:
            mod = import_module(module_name)
        except Exception:
            log.warning("Failed to import %s, skipping", module_name, exc_info=True)
            continue
        meta = getattr(mod, "meta", None)
        if meta is None:
            log.warning("Skipping %s: missing meta declaration", module_name)
            continue
        if not isinstance(meta, AppletMeta):
            log.warning(
                "%s.meta is %s, expected AppletMeta", module_name, type(meta).__name__
            )
            continue
        if meta.id in result:
            log.warning("Duplicate applet id %r from %s", meta.id, module_name)
            continue
        result[meta.id] = meta
    log.debug("Discovered %d applets", len(result))
    return result


@cache
def load_applet_class(applet_id: str) -> type[Applet] | None:
    """Import and return a specific applet class on demand."""
    meta = get_applet_catalog().get(applet_id)
    if meta is None:
        log.warning("No catalog entry for applet %r", applet_id)
        return None
    module_name = f"docking.applets.{meta.id}.applet"
    try:
        module = import_module(module_name)
    except Exception:
        log.error("Failed to import %s", module_name, exc_info=True)
        return None

    from docking.applets.base import Applet as _Base

    # Discover the concrete Applet subclass from the module instead of storing
    # a class name in AppletMeta. That keeps metadata minimal and avoids
    # duplicating another identifier that can drift during refactors.
    for obj in vars(module).values():
        if inspect.isclass(obj) and issubclass(obj, _Base) and obj is not _Base:
            return obj
    log.warning("No Applet subclass found in %s", module_name)
    return None
