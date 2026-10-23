"""Pixel regressions for rounded stack thumbnails and their rotating shadows."""

import math
from io import BytesIO

import cairo
import pytest
from gi.repository import GdkPixbuf
from PIL import Image

from docking.ui.stack import (
    StackCard,
    StackCardGeometry,
    StackPopupController,
)


def _render(size=96, angle=0.0, reveal=1.0, transparent=False, source_shape=None):
    width, height = source_shape or (size, size)
    pixbuf = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB, transparent, 8, width, height
    )
    pixbuf.fill(0x4684DCFF)
    if transparent:
        pixbuf.new_subpixbuf(width // 4, height // 4, width // 2, height // 2).fill(0)

    def unexpected_resize(*_args):
        pytest.fail("Thumbnail was resized before its final rendering transform")

    pixbuf.scale_simple = unexpected_resize
    geometry = StackCardGeometry(
        reveal=reveal,
        hover_value=0.0,
        rotation_radians=angle,
        icon_x=100 - size / 2,
        icon_y=100 - size / 2,
        icon_size=size,
        icon_center_x=100,
        icon_center_y=100,
        label_x=0,
        label_y=300,
    )
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 200)
    cr = cairo.Context(surface)
    controller = object.__new__(StackPopupController)
    controller._stack_card_geometry = lambda **_kwargs: geometry
    controller._draw_stack_card(
        cr=cr,
        card=StackCard(
            target="photo",
            label="Photo",
            icon=pixbuf,
            icon_x=0,
            icon_y=0,
            icon_size=96,
            label_x=0,
            label_y=300,
            label_w=48,
            label_h=24,
            thumbnail_style=True,
        ),
        sequence_index=0,
        now_us=0,
    )
    payload = BytesIO()
    surface.write_to_png(payload)
    payload.seek(0)
    return Image.open(payload).convert("RGBA")


def test_opaque_image_has_transparent_rounded_corners_and_faint_black_shadow():
    image = _render()

    for point in ((52, 52), (147, 52), (52, 147)):
        assert image.getpixel(point)[3] == 0
    assert image.getpixel((100, 52)) == (70, 132, 220, 255)
    assert image.getpixel((100, 100)) == (70, 132, 220, 255)
    assert image.getpixel((150, 100)) == (0, 0, 0, 25)


@pytest.mark.parametrize("size", [32, 48, 55, 96, 128])
@pytest.mark.parametrize("angle", [0.0, math.radians(-5.5), math.radians(5.5)])
@pytest.mark.parametrize("reveal", [0.4, 1.0])
def test_image_and_shadow_stay_within_rotated_bounds(size, angle, reveal):
    # 55px also exercises the rounded hover size of a 48px thumbnail.
    image = _render(size=size, angle=angle, reveal=reveal)
    half = size / 2
    for y in range(image.height):
        for x in range(image.width):
            red, green, blue, alpha = image.getpixel((x, y))
            if not alpha:
                continue
            dx, dy = x + 0.5 - 100, y + 0.5 - 100
            local_x = dx * math.cos(angle) + dy * math.sin(angle)
            local_y = -dx * math.sin(angle) + dy * math.cos(angle)
            # Allow filtering at the edge, plus the shadow's scaled offset.
            assert -half - 1 <= local_x <= half + size / 32 + 1
            assert -half - 1 <= local_y <= half + size / 32 + 1
            if alpha > 26:
                assert -half - 1 <= local_x <= half + 1
                assert -half - 1 <= local_y <= half + 1
                if alpha > 64:
                    assert blue > green > red
    center_alpha = image.getpixel((100, 100))[3]
    if reveal == 1.0:
        assert center_alpha == 255
    else:
        assert 180 <= center_alpha <= 200


@pytest.mark.parametrize("angle", [0.0, math.radians(5.5)])
def test_transparent_image_and_shadow_preserve_internal_holes(angle):
    image = _render(angle=angle, transparent=True, source_shape=(384, 384))

    assert image.getpixel((100, 100))[3] == 0
    assert image.getpixel((100, 65))[3] > 200


@pytest.mark.parametrize("shape", [(384, 192), (192, 384)])
def test_large_sources_keep_their_proportions_inside_the_icon_cell(shape):
    image = _render(source_shape=shape)

    assert image.getpixel((100, 100)) == (70, 132, 220, 255)
    if shape[0] > shape[1]:
        assert image.getpixel((100, 52))[3] == 0
        assert image.getpixel((100, 125))[3] <= 26
        assert image.getpixel((60, 100))[3] == 255
        assert image.getpixel((100, 80))[3] == 255
    else:
        assert image.getpixel((52, 100))[3] == 0
        assert image.getpixel((125, 100))[3] <= 26
        assert image.getpixel((100, 60))[3] == 255
        assert image.getpixel((80, 100))[3] == 255
