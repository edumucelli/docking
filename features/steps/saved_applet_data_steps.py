"""Load malformed on-disk configuration through actual applet constructors."""

import datetime as dt
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from behave import given, then, when

from docking.applets.aiusage.applet import AiUsageApplet
from docking.applets.certwatch.applet import CertwatchApplet
from docking.applets.hydration.applet import HydrationApplet
from docking.applets.sunrise.applet import SunriseApplet
from docking.applets.weather.applet import WeatherApplet
from docking.core.config import Config


@given('a saved "{applet}" configuration containing malformed values')
def saved_applet_data(context, applet):
    folder = TemporaryDirectory(prefix="docking-bdd-saved-data-")
    context.add_cleanup(folder.cleanup)
    cities = [
        {"city_display": "Broken", "lat": None, "lng": 0},
        {"city_display": "Berlin", "lat": 52.52, "lng": 13.41},
    ]
    preferences = {
        "hydration": {"interval": None},
        "weather": {"cities": cities, "active_index": None},
        "sunrise": {"cities": cities, "active_index": None},
        "aiusage": {
            "days": [
                {
                    "date": dt.date.today().isoformat(),
                    "sessions": None,
                    "by_model": {"gpt-5": {"in": None, "out": 20}},
                }
            ]
        },
        "certwatch": {"domains": [{"host": "example.test", "port": float("inf")}]},
    }
    context.saved_applet_name = applet
    path = Path(folder.name) / "dock.json"
    path.write_text(json.dumps({"applet_prefs": {applet: preferences[applet]}}))
    context.saved_applet_config = Config.load(path)


@when("the configured applet starts")
def start_saved_applet(context):
    classes = {
        "hydration": HydrationApplet,
        "weather": WeatherApplet,
        "sunrise": SunriseApplet,
        "aiusage": AiUsageApplet,
        "certwatch": CertwatchApplet,
    }
    context.saved_applet = classes[context.saved_applet_name](
        48, config=context.saved_applet_config
    )
    context.add_cleanup(context.saved_applet.stop)


@then("its icon and tooltip are available and valid saved data is retained")
def recovered_applet(context):
    applet = context.saved_applet
    assert applet.item.icon is not None
    assert applet.item.icon.get_width() == 48
    assert applet.item.name
    if context.saved_applet_name == "hydration":
        assert applet._state.interval_min == 45
    elif context.saved_applet_name in {"weather", "sunrise"}:
        assert len(applet._cities) == 1
        assert applet._active_city.city_display == "Berlin"
    elif context.saved_applet_name == "aiusage":
        assert applet._state.days[0].sessions == 0
        usage = dict(applet._state.days[0].by_model)["gpt-5"]
        assert usage.input_tokens == 0
        assert usage.output_tokens == 20
    else:
        assert applet._domains[0].host == "example.test"
        assert applet._domains[0].port == 443
