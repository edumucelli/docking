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

"""Small text helpers shared by applets."""

from __future__ import annotations

import html


def normalize_text(value: object) -> str:
    """Decode HTML entities and collapse whitespace for compact UI text."""
    text = html.unescape(str(value or ""))
    return " ".join(text.replace("\n", " ").replace("\r", " ").split()).strip()
