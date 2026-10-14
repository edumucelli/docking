"""Tests for the guarded XML parser shared by feed applets."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from docking.applets.xml_safety import UnsafeXmlError, parse_untrusted_xml


def test_plain_document_parses_and_returns_a_usable_root():
    root = parse_untrusted_xml("<feed><title>News</title></feed>")

    assert isinstance(root, ET.Element)
    assert root.findtext("title") == "News"


def test_str_payload_is_accepted_like_bytes():
    root = parse_untrusted_xml("<feed><title>News</title></feed>")

    assert root.tag == "feed"


@pytest.mark.parametrize(
    "payload",
    [
        '<!DOCTYPE feed [<!ENTITY a "lo">]><feed><title>&a;</title></feed>',
        "<!doctype feed><feed/>",
        "<!  DOCTYPE feed><feed/>",
        '<!ENTITY a "lo"><feed/>',
        '<feed><!ENTITY a "lo"></feed>',
    ],
)
def test_doctype_and_entity_declarations_are_refused(payload):
    """Refusing the declaration is the only thing that stops expansion.

    ElementTree expands internal entities while parsing, so detection has to
    happen on the raw payload, whatever casing and whitespace the grammar allows.
    """
    with pytest.raises(UnsafeXmlError):
        parse_untrusted_xml(payload)


def test_entity_expansion_never_reaches_the_caller():
    """A doubling entity would otherwise grow far beyond the input size."""
    bomb = (
        '<!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        '<!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">'
        '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">'
        "]><feed><title>&lol3;</title></feed>"
    )

    with pytest.raises(UnsafeXmlError):
        parse_untrusted_xml(bomb)


def test_unsafe_xml_error_is_a_value_error():
    """Callers map every parse failure onto their own user-facing message."""
    assert issubclass(UnsafeXmlError, ValueError)


_BOMB = (
    '<!DOCTYPE lolz [<!ENTITY a "lo"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
    "<feed><title>&b;</title></feed>"
)


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("utf-8", _BOMB.encode("utf-8")),
        ("utf-16 with bom", _BOMB.encode("utf-16")),
        ("utf-16-le without bom", _BOMB.encode("utf-16-le")),
        ("utf-16-be without bom", _BOMB.encode("utf-16-be")),
        (
            "utf-16-le behind a declaration",
            ('<?xml version="1.0" encoding="utf-16"?>' + _BOMB).encode("utf-16-le"),
        ),
        ("utf-16-le after whitespace", ("  " + _BOMB).encode("utf-16-le")),
        (
            "single-byte declared encoding",
            (
                '<?xml version="1.0" encoding="iso-8859-1"?>' + _BOMB + "<!-- \xe9 -->"
            ).encode("iso-8859-1"),
        ),
    ],
)
def test_declarations_are_found_whatever_the_document_encoding(label, payload):
    """The scan cannot be byte-level: UTF-16 does not spell out ``<!DOCTYPE``.

    Expat takes a byte order mark or the NUL in every other byte before the
    declaration, so the guard has to apply the same precedence.
    """
    with pytest.raises(UnsafeXmlError):
        parse_untrusted_xml(payload)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "iso-8859-1"])
def test_ordinary_documents_in_those_encodings_still_parse(encoding):
    """Rejecting the declaration must not reject the encoding itself."""
    document = (
        f'<?xml version="1.0" encoding="{encoding}"?><feed><title>Café</title></feed>'
    )

    root = parse_untrusted_xml(document.encode(encoding))

    assert root.findtext("title") == "Caf\xe9"


def test_malformed_xml_still_raises_a_parse_error():
    with pytest.raises(ET.ParseError):
        parse_untrusted_xml(b"<feed")


def test_non_parsable_payload_type_raises_type_error():
    with pytest.raises(TypeError):
        parse_untrusted_xml(None)  # type: ignore[arg-type]
