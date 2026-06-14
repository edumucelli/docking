# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

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

from docking.log import get_logger

DOMAIN = "docking"

# In-tree locale directory (works for development and pip install -e).
# Installed packages also keep .mo files here via package_data.
_LOCALE_DIR = Path(__file__).resolve().parent / "locale"
log = get_logger("i18n")


def init() -> None:
    """Initialize gettext for the docking domain.

    Call this once at application startup, before any UI code runs.
    Safe to call multiple times (idempotent).
    """
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error as exc:
        log.warning("Unsupported locale, falling back to C locale: %s", exc)
    gettext.bindtextdomain(DOMAIN, str(_LOCALE_DIR))
    gettext.textdomain(DOMAIN)


# Module-level aliases used by all translatable modules.
_ = gettext.gettext
ngettext = gettext.ngettext
