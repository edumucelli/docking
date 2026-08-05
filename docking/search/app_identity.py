# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Resolve the application identity used by search-related desktop services.

Portal sessions and desktop integration must identify the package that is
actually running. A Flatpak build is known to the desktop by ``FLATPAK_ID``;
an unpackaged or traditionally packaged build uses Docking's canonical
application ID. Keeping this decision in the search package prevents shortcut
services from depending on broader application bootstrap code.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

HOST_APPLICATION_ID = "org.docking.Docking"


def application_id(env: Mapping[str, str] | None = None) -> str:
    """Return the desktop identity of the package currently running.

    ``env`` exists so callers and tests can evaluate the rule without changing
    process state. An empty or whitespace-only Flatpak value is intentionally
    treated as absent.
    """
    values = env if env is not None else os.environ
    flatpak_id = values.get("FLATPAK_ID", "").strip()
    if flatpak_id:
        return flatpak_id
    return HOST_APPLICATION_ID


__all__ = [
    "HOST_APPLICATION_ID",
    "application_id",
]
