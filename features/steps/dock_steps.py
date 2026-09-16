"""Behave steps for deterministic dock interaction flows."""

from __future__ import annotations

from behave import given, then, when


@given('the dock is at the "{position}" edge with a {gap:d} pixel floating gap')
def step_dock_geometry_at_edge(context, position: str, gap: int) -> None:
    context.harness.configure_geometry(position=position, gap=gap)


@when("I render the resting dock geometry")
def step_render_resting_geometry(context) -> None:
    context.harness.render_resting_geometry()


@when("launch and urgency bounces peak at maximum zoom")
def step_render_peak_bounce(context) -> None:
    context.harness.render_peak_bounce()


@then("the painted icon matches its input target")
def step_painted_icon_matches_target(context) -> None:
    assert context.harness.painted_geometry_matches_target is True


@then("the floating gap does not target the icon")
def step_floating_gap_is_clear(context) -> None:
    assert context.harness.floating_gap_is_clear is True


@then("the painted shelf is {gap:d} pixels from the screen edge")
def step_painted_shelf_gap(context, gap: int) -> None:
    assert context.harness.painted_shelf_gap == gap


@then("the painted icon stays inside the dock surface")
def step_bounced_icon_within_surface(context) -> None:
    assert context.harness.bounced_icon_within_surface is True


@given('the dock has rendered at the "{position}" edge')
def step_dock_rendered_at_edge(context, position: str) -> None:
    context.old_dock_position = position


@when('I move the dock to the "{position}" edge')
def step_move_dock_to_edge(context, position: str) -> None:
    context.harness.render_position_change(
        old_position=context.old_dock_position,
        new_position=position,
    )


@then("the icons immediately align with the new shelf")
def step_icons_align_after_position_change(context) -> None:
    assert context.harness.position_change_aligned is True


@then("the left screen edge remains inside the dock input")
def step_left_edge_inside_dock_input(context) -> None:
    assert context.harness.left_edge_input_owned is True


@given("the pointer is held at the left screen edge")
def step_pointer_held_at_left_edge(context) -> None:
    context.harness.hold_pointer_at_left_edge()


@when("X11 reports a shape crossing outside the left edge")
def step_x11_shape_crossing_outside_left_edge(context) -> None:
    context.harness.report_left_edge_shape_leave()


@then("the dock remains hovered")
def step_dock_remains_hovered(context) -> None:
    assert context.harness.left_edge_hover_retained is True


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


@when('I drag the "{desktop_id}" dock item outside the dock and release it')
def step_drag_item_outside_and_release(context, desktop_id: str) -> None:
    context.harness.drag_outside_and_release(desktop_id)


@then("the drag reorder is applied")
def step_drag_reorder_is_applied(context) -> None:
    assert context.harness.drag_reordered is True


@then('the "{desktop_id}" dock item is unpinned')
def step_dragged_item_is_unpinned(context, desktop_id: str) -> None:
    assert context.harness.drag_removed_desktop_id == desktop_id


@when('I drop the launcher URI "{uri}" at insertion index {index:d}')
def step_drop_launcher_uri(context, uri: str, index: int) -> None:
    context.harness.drop_external_uri(uri, index)


@then('the pinned targets include "{desktop_id}"')
def step_pinned_targets_include(context, desktop_id: str) -> None:
    assert desktop_id in context.harness.external_pinned_targets


@given("preview support is enabled")
def step_preview_support_is_enabled(context) -> None:
    return


@when('I hover the running "{desktop_id}" dock item long enough for preview')
def step_hover_running_item_long_enough(context, desktop_id: str) -> None:
    context.harness.hover_running_item_long_enough(desktop_id)


@then('the preview for "{desktop_id}" is visible')
def step_preview_for_item_is_visible(context, desktop_id: str) -> None:
    assert context.harness.preview_visible is True


@when("I leave the dock while the preview is visible")
def step_leave_dock_while_preview_visible(context) -> None:
    context.harness.leave_dock_with_preview_visible()


@then("the preview hide is scheduled")
def step_preview_hide_is_scheduled(context) -> None:
    assert context.harness.preview_hide_scheduled is True


@then("the dock autohide leave is not released yet")
def step_autohide_leave_not_released(context) -> None:
    assert context.harness.autohide_leave_released is False


@when("the preview finishes hiding")
def step_preview_finishes_hiding(context) -> None:
    context.harness.finish_preview_hide()


@then("the dock autohide leave is released")
def step_autohide_leave_released(context) -> None:
    assert context.harness.autohide_leave_released is True


@given("the dock is currently showing from autohide")
def step_dock_is_currently_showing(context) -> None:
    context.harness.set_dock_showing()


@when('I hover the "{desktop_id}" dock item')
def step_hover_item(context, desktop_id: str) -> None:
    context.harness.hover_item(desktop_id)


@then("the tooltip is suppressed")
def step_tooltip_is_suppressed(context) -> None:
    assert context.harness.tooltip_suppressed is True
