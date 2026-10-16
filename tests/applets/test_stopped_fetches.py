"""Deliver production worker replies after an applet has been unloaded."""

from importlib import import_module
from unittest.mock import MagicMock

import pytest

from docking.core.config import Config
from tests.applets.deferred_worker import DeferredWorker


def make_fetch_applet(name, monkeypatch, tmp_path, *, error=False):
    classes = {
        "weather": "WeatherApplet",
        "crypto": "CryptoApplet",
        "currencyfx": "CurrencyFxApplet",
        "news": "NewsApplet",
        "reddit": "RedditApplet",
    }
    module = import_module(f"docking.applets.{name}.applet")
    config = Config.load(tmp_path / "dock.json")
    applet = getattr(module, classes[name])(48, config=config)
    if name == "weather":
        from tests.applets.test_weather import _BERLIN, _SAMPLE_WEATHER

        applet._cities = [_BERLIN]
        provider, payload = "fetch_weather", _SAMPLE_WEATHER
        monkeypatch.setattr(module, "fetch_air_quality", lambda **_kwargs: None)
    elif name == "crypto":
        from tests.applets.test_crypto import BTC, _snapshot

        applet._assets = [BTC]
        provider, payload = "fetch_crypto_snapshot", _snapshot()
    elif name == "currencyfx":
        from tests.applets.test_currencyfx import _snapshot

        provider, payload = "fetch_fx_snapshot", (_snapshot(), ("EUR", "USD"))
    elif name == "news":
        from tests.applets.test_news import _article, _source

        applet._sources = [_source()]
        provider, payload = "fetch_news_articles", (_article(),)
    else:
        from tests.applets.test_reddit import _post

        applet._subreddits = ["linux"]
        provider, payload = "fetch_reddit_posts", (_post(),)

    def fetch(**_kwargs):
        if error:
            raise OSError("late network failure")
        return payload

    monkeypatch.setattr(module, provider, fetch)
    queue = DeferredWorker()
    applet._worker = queue.worker
    return applet, config, queue


@pytest.mark.parametrize("name", ["weather", "crypto", "currencyfx", "news", "reddit"])
@pytest.mark.parametrize("error", [False, True])
def test_unloaded_applet_ignores_late_network_replies(
    monkeypatch, tmp_path, name, error
):
    applet, config, queue = make_fetch_applet(name, monkeypatch, tmp_path, error=error)
    applet._fetch_async()
    assert queue.tasks
    queue.read()
    applet.stop()
    config.applet_prefs[name] = {"newer-instance": True}
    present = MagicMock()
    monkeypatch.setattr(applet, "present", present)

    queue.publish()

    assert config.applet_prefs[name] == {"newer-instance": True}
    present.assert_not_called()
    assert not queue.tasks


def test_unloaded_weather_does_not_start_a_queued_refresh(monkeypatch, tmp_path):
    applet, _config, queue = make_fetch_applet("weather", monkeypatch, tmp_path)
    applet._fetch_async()
    applet._fetch_async()
    assert applet._fetch_pending
    queue.read()
    applet.stop()
    queue.publish()
    assert not queue.tasks


@pytest.mark.parametrize("name", ["weather", "crypto", "currencyfx", "news", "reddit"])
def test_restarted_applet_can_receive_a_new_fetch(monkeypatch, tmp_path, name):
    applet, _config, queue = make_fetch_applet(name, monkeypatch, tmp_path)
    module = import_module(f"docking.applets.{name}.applet")
    monkeypatch.setattr(module.GLib, "timeout_add_seconds", lambda *_args: 0)
    monkeypatch.setattr(module.GLib, "timeout_add", lambda *_args: 0)
    applet._fetch_async()
    queue.read()
    applet.stop()
    queue.publish()
    applet.start(lambda: None)
    try:
        applet._fetch_async()
        assert queue.tasks
        queue.read()
        present = MagicMock()
        monkeypatch.setattr(applet, "present", present)
        queue.publish()
        present.assert_called_once()
        assert not applet._loading
    finally:
        applet.stop()
