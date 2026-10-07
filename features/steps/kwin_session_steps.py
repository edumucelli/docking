"""F12 behavior checks using real private D-Bus sessions."""

from behave import given, then, when

from tests.bdd_support.atspi_session import AccessibilitySession


def add_session(context, title="Session", **options):
    session = AccessibilitySession(title, **options)
    context.accessibility_sessions.append(session)
    return session


@given('isolated accessibility sessions "{first}" and "{second}"')
def separate_sessions(context, first, second):
    add_session(context, first)
    add_session(context, second)


@then("each dock lists only its own session's window")
def own_windows_only(context):
    results = [
        session.wait_for_titles([session.title])
        for session in context.accessibility_sessions
    ]
    assert results[0]["address"] != results[1]["address"]


@given("an isolated session whose accessibility service is unavailable")
def missing_service(context):
    add_session(context, available=False)


@given("an isolated session with a tracked accessibility window")
def tracked_window(context):
    session = add_session(context)
    context.old_address = session.wait_for_titles([session.title])["address"]


@given("an isolated session with an explicit accessibility address but no discovery")
def explicit_address(context):
    add_session(context, available=False, explicit=True)


@then("its dock has no tracked windows and remains running")
def no_windows(context):
    assert context.accessibility_sessions[0].wait_for_titles([])["running"]


@when("that session's accessibility service becomes available")
def service_available(context):
    context.accessibility_sessions[0].request("available")


@when("its accessibility bus disappears")
def bus_disappears(context):
    context.accessibility_sessions[0].request("missing")


@then("its dock tracks the session window")
def tracks_window(context):
    session = context.accessibility_sessions[0]
    session.wait_for_titles([session.title])


@then("the accessibility bus address has changed")
def address_changed(context):
    assert (
        context.accessibility_sessions[0].request("snapshot")["address"]
        != context.old_address
    )
