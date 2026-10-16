"""Applet preferences can contain JSON numeric exponents outside float range."""

import json
from importlib import import_module

import pytest

from docking.core.config import Config

NUMERIC_PREFERENCES = [
    ("crypto", "prefs_from_mapping", "active_index"),
    ("currencyfx", "prefs_from_mapping", "active_index"),
    ("lastfm", "prefs_from_mapping", "max_entries"),
    ("news", "prefs_from_mapping", "active_article_index"),
    ("news", "prefs_from_mapping", "active_source_index"),
    ("reddit", "prefs_from_mapping", "active_post_index"),
    ("reddit", "prefs_from_mapping", "active_subreddit_index"),
    ("stretchcoach", "state_from_prefs", "interval"),
]


@pytest.mark.parametrize("name,loader_name,key", NUMERIC_PREFERENCES)
@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_out_of_range_saved_number_uses_the_default(name, loader_name, key, value):
    loader = getattr(import_module(f"docking.applets.{name}.state"), loader_name)
    assert loader({key: value}) == loader({})


@pytest.mark.parametrize(
    "name,key", [(name, key) for name, _, key in NUMERIC_PREFERENCES]
)
def test_applet_starts_from_a_saved_overflowing_json_number(tmp_path, name, key):
    path = tmp_path / "dock.json"
    data = {"applet_prefs": {name: {key: float("inf")}}}
    path.write_text(json.dumps(data).replace("Infinity", "1e999"))
    config = Config.load(path)
    classes = {
        "crypto": "CryptoApplet",
        "currencyfx": "CurrencyFxApplet",
        "lastfm": "LastfmApplet",
        "news": "NewsApplet",
        "reddit": "RedditApplet",
        "stretchcoach": "StretchCoachApplet",
    }
    module = import_module(f"docking.applets.{name}.applet")
    applet = getattr(module, classes[name])(48, config=config)
    try:
        assert applet.item.icon is not None
        assert applet.item.icon.get_width() == 48
        assert applet.item.name
    finally:
        applet.stop()
