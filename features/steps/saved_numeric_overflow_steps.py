"""Start applets from saved JSON numeric exponents outside float range."""

import json
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory

from behave import given, then, when

from docking.core.config import Config


@given('the "{name}" applet has "{key}" saved as an overflowing JSON number')
def saved_overflow(context, name, key):
    directory = TemporaryDirectory(prefix="docking-bdd-overflow-")
    context.add_cleanup(directory.cleanup)
    path = Path(directory.name) / "dock.json"
    data = {"applet_prefs": {name: {key: float("inf")}}}
    path.write_text(json.dumps(data).replace("Infinity", "1e999"))
    context.overflow_config = Config.load(path)
    context.overflow_name = name


@when("that applet is constructed from the saved file")
def start_overflow(context):
    names = {
        "crypto": "CryptoApplet",
        "currencyfx": "CurrencyFxApplet",
        "lastfm": "LastfmApplet",
        "news": "NewsApplet",
        "reddit": "RedditApplet",
        "stretchcoach": "StretchCoachApplet",
    }
    module = import_module(f"docking.applets.{context.overflow_name}.applet")
    context.overflow_applet = getattr(module, names[context.overflow_name])(
        48, config=context.overflow_config
    )
    context.add_cleanup(context.overflow_applet.stop)


@then("its icon and tooltip are available")
def recovered_overflow(context):
    applet = context.overflow_applet
    assert applet.item.icon is not None
    assert applet.item.icon.get_width() == 48
    assert applet.item.name
