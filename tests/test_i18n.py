"""Tests for gettext initialization helpers."""

from __future__ import annotations

import locale
from pathlib import Path
from unittest.mock import MagicMock

import docking.i18n as i18n_mod


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
