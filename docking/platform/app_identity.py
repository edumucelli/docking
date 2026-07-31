# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Package-aware application identity for portals and session-bus ownership."""

from __future__ import annotations

import os
from collections.abc import Mapping

HOST_APPLICATION_ID = "org.docking.Docking"
FLATPAK_APPLICATION_ID = "cc.docking.Docking"


def application_id(env: Mapping[str, str] | None = None) -> str:
    """Return the identity of the installed package currently running."""
    values = env if env is not None else os.environ
    flatpak_id = values.get("FLATPAK_ID", "").strip()
    if flatpak_id:
        return flatpak_id
    return HOST_APPLICATION_ID


__all__ = [
    "FLATPAK_APPLICATION_ID",
    "HOST_APPLICATION_ID",
    "application_id",
]
