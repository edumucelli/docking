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

"""Shared tooltip construction helpers for applets."""

from __future__ import annotations

from collections.abc import Iterable

from docking.i18n import _


def structured_tooltip(
    *,
    title: str,
    primary: str | None = None,
    details: Iterable[str | None] = (),
    freshness: Iterable[str | None] = (),
    error: str | None = None,
    recovery: str | None = None,
) -> str:
    """Build a multi-line tooltip in the standard applet order.

    Order is:
    title or primary object, primary current value, secondary details,
    freshness/timestamp, then error or recovery hints.
    """
    lines: list[str] = []
    _append_line(lines=lines, text=title)
    _append_line(lines=lines, text=primary)
    for detail in details:
        _append_line(lines=lines, text=detail)
    for line in freshness:
        _append_line(lines=lines, text=line)
    if error:
        _append_line(lines=lines, text=_("Error: {msg}").format(msg=error))
    _append_line(lines=lines, text=recovery)
    return "\n".join(lines)


def _append_line(*, lines: list[str], text: str | None) -> None:
    if text is None:
        return
    cleaned = str(text).strip()
    if cleaned:
        lines.append(cleaned)
