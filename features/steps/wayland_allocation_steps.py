"""Layer-shell allocation contract; native input is covered by compositor runs."""

from behave import given, then, when

from tests.bdd_support.wayland_geometry import ConfiguredLayerDock


@given('a crowded layer-shell dock at the "{edge}" edge')
def crowded_layer_dock(context, edge):
    context.layer_dock = ConfiguredLayerDock(edge)


@when("the compositor reserves {pixels:d} pixels along the dock's length")
def reserved_space(context, pixels):
    context.layer_dock.configure(pixels)


@when('the layer-shell dock moves to the "{edge}" edge')
def move_layer_dock(context, edge):
    context.layer_dock.place(edge)


@then("every icon fits and its painted center is clickable")
def icons_fit(context):
    context.layer_dock.assert_fits()


@then("the preferred icon size is restored")
def preferred_size(context):
    assert all(g.draw_rect.w == 48 for g in context.layer_dock.frame.item_geometries)
