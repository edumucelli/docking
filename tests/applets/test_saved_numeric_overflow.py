"""Applet preferences can contain JSON numeric exponents outside float range."""

from importlib import import_module

import pytest


@pytest.mark.parametrize(
    "name,loader_name,key",
    [
        ("crypto", "prefs_from_mapping", "active_index"),
        ("currencyfx", "prefs_from_mapping", "active_index"),
        ("lastfm", "prefs_from_mapping", "max_entries"),
        ("news", "prefs_from_mapping", "active_article_index"),
        ("news", "prefs_from_mapping", "active_source_index"),
        ("reddit", "prefs_from_mapping", "active_post_index"),
        ("reddit", "prefs_from_mapping", "active_subreddit_index"),
        ("stretchcoach", "state_from_prefs", "interval"),
    ],
)
@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_out_of_range_saved_number_uses_the_default(name, loader_name, key, value):
    loader = getattr(import_module(f"docking.applets.{name}.state"), loader_name)
    assert loader({key: value}) == loader({})
