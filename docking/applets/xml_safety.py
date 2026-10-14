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

"""Guarded XML parsing for feed payloads that arrive over the network.

ElementTree expands internal entities, so a DTD turns a few hundred bytes of
feed into an arbitrarily large tree. The check runs on the decoded text as well
as the raw bytes, since UTF-16 does not spell out ``<!DOCTYPE`` in single bytes.
"""

from __future__ import annotations

import codecs
import re
import xml.etree.ElementTree as ET

# No feed format Docking consumes has a legitimate use for a DTD.
_UNSAFE_XML_TEXT_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_UNSAFE_XML_BYTES_RE = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)

# Longest first: UTF-32-LE's BOM starts with UTF-16-LE's.
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
_DECLARED_ENCODING_RE = re.compile(
    r"""<\?xml[^>]*?encoding\s*=\s*["']([A-Za-z0-9._-]+)["']""",
    re.IGNORECASE,
)
_DECLARATION_SEARCH_WINDOW = 512


class UnsafeXmlError(ValueError):
    """Raised when a payload declares a DTD or an entity."""


def _encoding_of(payload: bytes) -> str:
    """Return the encoding a conforming parser will read this payload with.

    Byte order mark, then declaration, then sniffing: the same precedence the
    parser applies, so the checked text is the text the parser will see.
    """
    for bom, encoding in _BOMS:
        if payload.startswith(bom):
            return encoding
    # The parser also sniffs UTF-16 from the NUL in every other byte, and a
    # NUL cannot appear in a well-formed UTF-8 document.
    head = payload[:2]
    if len(head) == 2:
        if head[1] == 0:
            return "utf-16-le"
        if head[0] == 0:
            return "utf-16-be"
    window = payload[:_DECLARATION_SEARCH_WINDOW].decode("ascii", errors="replace")
    match = _DECLARED_ENCODING_RE.search(window)
    if match is None:
        return "utf-8"
    try:
        codecs.lookup(match.group(1))
    except LookupError:
        return "utf-8"
    return match.group(1)


def parse_untrusted_xml(payload: bytes | str) -> ET.Element:
    """Parse XML from an untrusted source, refusing DTD and entity declares.

    Raises ``UnsafeXmlError`` for a doctype or entity declaration and
    ``ET.ParseError`` (or ``TypeError``) for anything the parser rejects.
    """
    raw_bytes = payload.encode("utf-8") if isinstance(payload, str) else payload
    if _UNSAFE_XML_BYTES_RE.search(raw_bytes):
        raise UnsafeXmlError("XML declares a DTD or entity")
    # A declaration is not spelled out in bytes under a multi-byte encoding.
    decoded = raw_bytes.decode(_encoding_of(raw_bytes), errors="replace")
    if _UNSAFE_XML_TEXT_RE.search(decoded):
        raise UnsafeXmlError("XML declares a DTD or entity")
    # Safe only because the checks above rejected every way to declare an entity.
    return ET.fromstring(raw_bytes)


__all__: list[str] = ["UnsafeXmlError", "parse_untrusted_xml"]
