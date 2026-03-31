"""Behave steps for deterministic dock interaction flows."""

from __future__ import annotations

from behave import given, then, when


@given("the dock is in autohide mode")
def step_dock_is_in_autohide_mode(context) -> None:
    context.harness.set_hide_mode("autohide")


@when("I move the pointer onto the dock")
def step_move_pointer_onto_dock(context) -> None:
    context.harness.move_pointer_to_dock()


@when("I move the pointer off the dock")
def step_move_pointer_off_dock(context) -> None:
    context.harness.move_pointer_off_dock()


@when("I advance dock time by {milliseconds:d} milliseconds")
def step_advance_dock_time(context, milliseconds: int) -> None:
    context.harness.advance_time(milliseconds)


@then("the dock is visible")
def step_dock_is_visible(context) -> None:
    assert context.harness.dock_visible is True


@then("the dock is hidden")
def step_dock_is_hidden(context) -> None:
    assert context.harness.dock_hidden is True


@given('the folder stack for "{desktop_id}" is open')
def step_folder_stack_is_open(context, desktop_id: str) -> None:
    context.harness.left_click_item(desktop_id)


@when('I left click the "{desktop_id}" dock item')
def step_left_click_dock_item(context, desktop_id: str) -> None:
    context.harness.left_click_item(desktop_id)


@when('I move the pointer to the "{desktop_id}" dock item')
def step_move_pointer_to_item(context, desktop_id: str) -> None:
    context.harness.move_pointer_to_item(desktop_id)


@then('the folder stack for "{desktop_id}" is open')
def step_folder_stack_for_item_is_open(context, desktop_id: str) -> None:
    assert context.harness.folder_stack_open_for == desktop_id


@then("no folder stack is open")
def step_no_folder_stack_is_open(context) -> None:
    assert context.harness.folder_stack_open_for is None


@given('a drag can start from the "{desktop_id}" dock item')
def step_drag_can_start_from_item(context, desktop_id: str) -> None:
    context.drag_source = desktop_id


@when('I begin dragging the "{desktop_id}" dock item')
def step_begin_dragging_item(context, desktop_id: str) -> None:
    context.harness.begin_drag(desktop_id)


@when("I drag to insertion index {index:d}")
def step_drag_to_insertion_index(context, index: int) -> None:
    context.harness.drag_to_index(index)


@then("the drag reorder is applied")
def step_drag_reorder_is_applied(context) -> None:
    assert context.harness.drag_reordered is True


@when('I drop the launcher URI "{uri}" at insertion index {index:d}')
def step_drop_launcher_uri(context, uri: str, index: int) -> None:
    context.harness.drop_external_uri(uri, index)


@then('the pinned targets include "{desktop_id}"')
def step_pinned_targets_include(context, desktop_id: str) -> None:
    assert desktop_id in context.harness.external_pinned_targets
