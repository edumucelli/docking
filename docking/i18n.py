"""Gettext initialization for the docking application.

Every module with user-visible strings imports ``_`` and ``ngettext`` from here:

    from docking.i18n import _, ngettext

The ``init()`` function must be called once at startup (in ``app.py``) before
any translated string is evaluated. When no translation catalog is loaded
(e.g. in tests or on unsupported locales), ``_()`` returns the original
English string unchanged.
"""

from __future__ import annotations

import gettext
import locale
from pathlib import Path

DOMAIN = "docking"

# In-tree locale directory (works for development and pip install -e).
# Installed packages also keep .mo files here via package_data.
_LOCALE_DIR = Path(__file__).resolve().parent / "locale"


def init() -> None:
    """Initialize gettext for the docking domain.

    Call this once at application startup, before any UI code runs.
    Safe to call multiple times (idempotent).
    """
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass  # Unsupported locale -- fall back to C
    gettext.bindtextdomain(DOMAIN, str(_LOCALE_DIR))
    gettext.textdomain(DOMAIN)


# Module-level aliases used by all translatable modules.
_ = gettext.gettext
ngettext = gettext.ngettext
