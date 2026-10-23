"""Shared pytest setup for the repository test suite."""

from __future__ import annotations

from contextlib import suppress
from types import SimpleNamespace

import pytest

try:
    import gi
except ModuleNotFoundError:  # pragma: no cover - no-GI smoke jobs
    gi = None

if gi is not None:  # pragma: no branch - tiny startup guard
    for namespace, version in (
        ("Gtk", "3.0"),
        ("Gdk", "3.0"),
        ("GdkPixbuf", "2.0"),
        ("Pango", "1.0"),
        ("Wnck", "3.0"),
    ):
        with suppress(ValueError):
            gi.require_version(namespace, version)


@pytest.fixture
def monotonic_clock(monkeypatch):
    """Advance animation time explicitly, without sleeping or dispatching timers."""
    from gi.repository import GLib

    clock = SimpleNamespace(now_us=0)
    monkeypatch.setattr(GLib, "get_monotonic_time", lambda: clock.now_us)
    return clock


@pytest.fixture(autouse=True)
def _isolate_config_paths(tmp_path, monkeypatch):
    """Redirect the module-level config path into a tmp dir for every test.

    Without this, any test that instantiates a bare ``Config()`` and calls
    ``save()`` would overwrite the developer's real ``~/.config/docking/dock.json``,
    because ``Config.__post_init__`` seeds ``self._path`` from
    ``DEFAULT_CONFIG_FILE``. This fixture patches the module constants so
    every test works against an isolated throwaway file.
    """
    from docking.core import config as config_mod

    tmp_file = tmp_path / "docking-test-dock.json"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_FILE", tmp_file)
    monkeypatch.setattr(
        config_mod,
        "DEFAULT_CONFIG_BACKUP_FILE",
        tmp_file.with_name(tmp_file.name + ".bak"),
    )
    yield
