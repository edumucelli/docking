"""Tests for gettext initialization helpers."""

from __future__ import annotations

import gettext
import locale
import struct
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import docking.i18n as i18n_mod


@pytest.fixture(autouse=True)
def isolate_translation(monkeypatch):
    i18n_mod._get_translation.cache_clear()
    monkeypatch.setattr(i18n_mod.locale, "setlocale", lambda *_args: "C")
    yield
    i18n_mod._get_translation.cache_clear()


def _write_catalog(path: Path, messages: dict[str, str]) -> None:
    """Create a tiny GNU catalog without requiring an installed msgfmt."""
    originals = [key.encode() for key in sorted(messages)]
    translations = [messages[key].encode() for key in sorted(messages)]
    count = len(originals)
    offset = 28 + count * 16
    tables = []
    strings = bytearray()
    for values in (originals, translations):
        for value in values:
            tables.append(struct.pack("<II", len(value), offset + len(strings)))
            strings.extend(value + b"\0")
    path.parent.mkdir(parents=True)
    path.write_bytes(
        struct.pack("<7I", 0x950412DE, 0, count, 28, 28 + count * 8, 0, 0)
        + b"".join(tables)
        + strings
    )


def test_missing_catalog_keeps_english_and_plural_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n_mod, "_LOCALE_DIR", tmp_path)
    i18n_mod.init()

    assert i18n_mod._("Open") == "Open"
    assert i18n_mod.ngettext("window", "windows", 1) == "window"
    assert i18n_mod.ngettext("window", "windows", 0) == "windows"
    assert i18n_mod.ngettext("window", "windows", 2) == "windows"


def test_existing_imports_use_initialized_catalog_without_discovery(
    tmp_path, monkeypatch
):
    _write_catalog(
        tmp_path / "fr/LC_MESSAGES/docking.mo",
        {
            "": "Content-Type: text/plain; charset=UTF-8\nPlural-Forms: nplurals=2; plural=(n != 1);\n",
            "Open": "Ouvrir",
            "window\0windows": "fenetre\0fenetres",
        },
    )
    translate = i18n_mod._
    plural = i18n_mod.ngettext
    assert translate("Open") == "Open"
    monkeypatch.setattr(i18n_mod, "_LOCALE_DIR", tmp_path)
    monkeypatch.setenv("LANGUAGE", "fr")
    i18n_mod.init()
    monkeypatch.setattr(
        i18n_mod.gettext,
        "find",
        MagicMock(side_effect=AssertionError("catalog lookup")),
    )

    for _ in range(20):
        assert translate("Open") == "Ouvrir"
        assert translate("Missing") == "Missing"
        assert plural("window", "windows", 1) == "fenetre"
        assert plural("window", "windows", 2) == "fenetres"
    assert translate is i18n_mod._
    assert plural is i18n_mod.ngettext


def test_repeat_init_changes_catalog_without_replacing_wrappers(monkeypatch):
    translate = i18n_mod._
    first = MagicMock(spec=gettext.NullTranslations)
    second = MagicMock(spec=gettext.NullTranslations)
    first.gettext.return_value = "first"
    second.gettext.return_value = "second"
    loader = MagicMock(side_effect=[first, second])
    monkeypatch.setattr(i18n_mod.gettext, "translation", loader)

    i18n_mod.init()
    assert translate("Open") == "first"
    i18n_mod.init()
    assert translate("Open") == "second"
    assert translate is i18n_mod._
    assert loader.call_count == 2
    assert i18n_mod._get_translation.cache_info().maxsize == 1
    assert i18n_mod._get_translation.cache_info().currsize == 1
    loader.assert_called_with(
        i18n_mod.DOMAIN, localedir=str(i18n_mod._LOCALE_DIR), fallback=True
    )


def test_malformed_catalog_is_not_silently_discarded(monkeypatch):
    monkeypatch.setattr(
        i18n_mod.gettext,
        "translation",
        MagicMock(side_effect=ValueError("invalid plural rule")),
    )

    with pytest.raises(ValueError, match="invalid plural rule"):
        i18n_mod.init()


def test_init_logs_locale_fallback_and_binds_domain(monkeypatch):
    warning = MagicMock()
    monkeypatch.setattr(i18n_mod.log, "warning", warning)
    monkeypatch.setattr(
        i18n_mod.locale,
        "setlocale",
        MagicMock(side_effect=locale.Error("unsupported")),
    )
    bindtextdomain = MagicMock()
    textdomain = MagicMock()
    monkeypatch.setattr(i18n_mod.gettext, "bindtextdomain", bindtextdomain)
    monkeypatch.setattr(i18n_mod.gettext, "textdomain", textdomain)

    i18n_mod.init()

    warning.assert_called_once()
    bindtextdomain.assert_called_once_with(i18n_mod.DOMAIN, str(i18n_mod._LOCALE_DIR))
    textdomain.assert_called_once_with(i18n_mod.DOMAIN)


def test_system_monitor_translation_does_not_regress_to_cpu_monitor():
    for path in Path("docking/locale").glob("*/LC_MESSAGES/docking.po"):
        text = path.read_text(encoding="utf-8")
        assert 'msgid "System Monitor"\nmsgstr "CPU Monitor"' not in text, (
            f"{path} still translates System Monitor as CPU Monitor"
        )
