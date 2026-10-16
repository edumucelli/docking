"""Exercise hydration timing and visible reminder state without wall-clock waits."""

from behave import given, then, when

from docking.applets.hydration.applet import HydrationApplet
from docking.applets.hydration.state import tick
from docking.core.config import Config


@given("a hydration reminder with an interval of {minutes:d} minutes")
def hydration_reminder(context, minutes):
    config = Config()
    config.applet_prefs["hydration"] = {"interval": minutes}
    context.hydration = HydrationApplet(48, config=config)


@when("one second remains in the hydration interval")
def almost_due(context):
    state = context.hydration._state
    for _ in range(state.interval_min * 60 - 1):
        result = tick(state)
        assert not result.became_empty
        state = result.state
    context.hydration._state = state


@then("the hydration reminder is not urgent")
def hydration_not_urgent(context):
    assert not context.hydration.item.is_urgent
    assert context.hydration._state.fill > 0


@when("the hydration interval's final second passes")
def hydration_due(context):
    context.hydration._tick()


@then("the hydration reminder is urgent and asks me to drink water")
def hydration_urgent(context):
    assert context.hydration.item.is_urgent
    assert context.hydration._state.fill == 0
    assert context.hydration.item.name == "Drink water!"


@when("I refill the hydration reminder")
def refill_hydration(context):
    context.hydration.on_clicked()
