"""Input diagnostics observe GTK selection without taking over its IM state."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.windows.visual import input_gtk as mod


@pytest.fixture
def gtk_probe(monkeypatch, tmp_path):
    context = Mock()
    context.get_context_id.return_value = "ime"
    settings = Mock()
    settings.get_property.return_value = ""
    cache = tmp_path / "immodules.cache"
    monkeypatch.setattr(
        mod,
        "Gtk",
        SimpleNamespace(
            Settings=SimpleNamespace(get_default=lambda: settings),
            rc_get_im_module_file=lambda: str(cache),
            IMMulticontext=lambda: context,
            IMContext=SimpleNamespace(__gtype__="input-context"),
        ),
    )
    monkeypatch.setattr(
        mod,
        "GObject",
        SimpleNamespace(
            type_children=lambda kind: [
                SimpleNamespace(name="GtkIMContextIME"),
                SimpleNamespace(name="GtkIMContextSimple"),
            ]
        ),
    )
    entry = Mock()
    entry.get_property.return_value = None
    return SimpleNamespace(entry=entry, context=context, settings=settings, cache=cache)


def test_probe_observes_native_selection_without_focusing_or_changing_entry(gtk_probe):
    result = mod.snapshot(gtk_probe.entry)

    assert result["entry_override"] is None
    assert result["settings_module"] == ""
    assert result["probe_context_id"] == "ime"
    assert result["native_ime_type_registered"]
    assert not result["module_cache_exists"]  # Built-in modules need no cache/DLL.
    gtk_probe.entry.get_property.assert_called_once_with("im-module")
    assert [call[0] for call in gtk_probe.entry.mock_calls] == [
        "get_property",
        "get_window",
    ]
    assert [call[0] for call in gtk_probe.context.mock_calls] == [
        "set_client_window",
        "get_preedit_string",
        "get_context_id",
        "set_client_window",
    ]
    assert gtk_probe.context.set_client_window.call_args_list[0].args == (
        gtk_probe.entry.get_window.return_value,
    )
    gtk_probe.context.set_client_window.assert_called_with(None)


def test_probe_distinguishes_explicit_entry_override_from_global_settings(gtk_probe):
    gtk_probe.entry.get_property.return_value = "gtk-im-context-simple"
    gtk_probe.settings.get_property.return_value = "ime"
    gtk_probe.context.get_context_id.return_value = "gtk-im-context-simple"

    result = mod.snapshot(gtk_probe.entry)

    assert result["entry_override"] == "gtk-im-context-simple"
    assert result["settings_module"] == "ime"
    assert result["probe_context_id"] == "gtk-im-context-simple"
    gtk_probe.context.set_context_id.assert_called_once_with("gtk-im-context-simple")
    gtk_probe.entry.set_property.assert_not_called()


def test_probe_records_only_allowed_gtk_environment(gtk_probe, monkeypatch):
    monkeypatch.setenv("GTK_IM_MODULE", "ime")
    monkeypatch.setenv("GITHUB_TOKEN", "unrelated-test-value")
    gtk_probe.cache.write_text("", encoding="utf-8")

    result = mod.snapshot()

    assert result["environment"]["GTK_IM_MODULE"] == "ime"
    assert "GITHUB_TOKEN" not in result["environment"]
    assert result["module_cache_exists"]
    gtk_probe.context.set_context_id.assert_not_called()
    gtk_probe.context.set_client_window.assert_called_once_with(None)


def test_probe_detaches_its_client_even_when_selection_fails(gtk_probe):
    gtk_probe.context.get_preedit_string.side_effect = RuntimeError("Selection failed")

    with pytest.raises(RuntimeError, match="Selection failed"):
        mod.snapshot(gtk_probe.entry)

    gtk_probe.context.set_client_window.assert_called_with(None)
    gtk_probe.entry.set_property.assert_not_called()


def test_probe_redacts_paths_and_never_stats_network_cache(gtk_probe, monkeypatch):
    private = r"\\private-fixture\share\immodules.cache"
    monkeypatch.setenv("GTK_PATH", private)
    monkeypatch.setenv("PYGI_DLL_PATH", "/private-fixture/plugins")
    monkeypatch.setattr(mod.Gtk, "rc_get_im_module_file", lambda: private)
    monkeypatch.setattr(
        mod, "Path", Mock(side_effect=AssertionError("inspected network cache"))
    )

    result = mod.snapshot(gtk_probe.entry)

    assert "private-fixture" not in json.dumps(result)
    assert result["environment"]["GTK_PATH"]["network"]
    assert result["environment"]["PYGI_DLL_PATH"]["set"]
    assert result["module_cache"]["network"]
    assert result["module_cache_exists"] is None
