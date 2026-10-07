"""Observe real stack allocation; corner marks provide a screen-space oracle.

Only the application-launch boundary is intercepted. Native pointer input opens
the popup, scrolls it and dispatches its real callbacks. Marks do not change
allocation or positioning, and are exclusive to this isolated test launcher.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CORNER_COLORS = ((255, 0, 255), (0, 255, 255), (255, 255, 0), (0, 255, 0))
ICON_COLOR = (255, 0, 0)


def locate(path: Path, color: tuple[int, int, int]) -> tuple[int, int, int, int] | None:
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    image = GdkPixbuf.Pixbuf.new_from_file(str(path))
    pixels = image.get_pixels()
    channels, stride = image.get_n_channels(), image.get_rowstride()
    width, height = image.get_width(), image.get_height()
    xs, ys = [], []
    for y in range(height):
        row = y * stride
        for x in range(width):
            i = row + x * channels
            if tuple(pixels[i : i + 3]) == color:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def popup_bounds(
    path: Path, size: list[int], scale: int = 1
) -> tuple[int, int, int, int]:
    marks = [locate(path, color) for color in CORNER_COLORS]
    if not all(mark is not None for mark in marks):
        raise ValueError(f"popup corner clipped: {marks}")
    px, py = marks[0][:2]
    if px % scale or py % scale:
        raise ValueError(f"popup origin is not on the logical pixel grid: {marks}")
    x, y = px // scale, py // scale
    width, height = size
    expected = (
        (x, y),
        (x + width - 4, y),
        (x, y + height - 4),
        (x + width - 4, y + height - 4),
    )
    if [mark[:2] for mark in marks] != [
        (mx * scale, my * scale) for mx, my in expected
    ]:
        raise ValueError(f"popup extent differs from screenshot: {marks}, {expected}")
    return x, y, width, height


def run() -> None:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk

    from docking import app

    state_path = Path(os.environ["LAB_STACK_STATE"])
    original = app.build_dock_window
    activations = []

    def build(*args, **kwargs):
        ui = original(*args, **kwargs)
        window = ui.window
        stack = ui.input_controller._interactions._folder_stack
        stack._target_service.open_target = lambda target: activations.append(target)
        marked_window = None

        def mark_icon(widget, cr):
            frame = window.geometry.build_frame()
            rect = frame.item_geometries[0].draw_rect
            cr.set_source_rgb(1, 0, 0)
            cr.rectangle(
                round(rect.x + rect.w / 2) - 2, round(rect.y + rect.h / 2) - 2, 4, 4
            )
            cr.fill()
            return False

        def mark_popup(widget, cr):
            width, height = widget.get_size()
            for (x, y), color in zip(
                ((0, 0), (width - 4, 0), (0, height - 4), (width - 4, height - 4)),
                CORNER_COLORS,
                strict=True,
            ):
                cr.set_source_rgb(*(value / 255 for value in color))
                cr.rectangle(x, y, 4, 4)
                cr.fill()
            return False

        original_draw = window.renderer.draw

        def draw_with_marker(cr, widget, *args, **kwargs):
            original_draw(cr, widget, *args, **kwargs)
            cr.save()
            mark_icon(widget, cr)
            cr.restore()

        window.renderer.draw = draw_with_marker
        window.queue_draw()

        def sample():
            nonlocal marked_window
            popup = stack._folder_stack_window
            state = {
                "visible": bool(popup and popup.get_visible()),
                "activations": activations,
            }
            if popup is not None:
                if marked_window is not popup:
                    popup.connect_after("draw", mark_popup)
                    popup.queue_draw()
                    marked_window = popup
                state["size"] = list(popup.get_size())
                child = stack._folder_stack_revealer.get_child()
                if isinstance(child, Gtk.ScrolledWindow):
                    adjustment = child.get_vadjustment()
                    state["scroll"] = [
                        adjustment.get_value(),
                        adjustment.get_upper(),
                        adjustment.get_page_size(),
                    ]
                area = stack._folder_stack_area
                if area is not None:
                    state["cards"] = [
                        {
                            "key": card.key,
                            "point": list(
                                area.translate_coordinates(
                                    popup,
                                    card.label_x + card.label_w // 2,
                                    card.label_y + card.label_h // 2,
                                )
                            ),
                        }
                        for card in stack._folder_stack_cards
                    ]
            temporary = state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state))
            temporary.replace(state_path)
            return True

        GLib.timeout_add(50, sample)
        return ui

    app.build_dock_window = build
    app.main()


def seed(config_path: Path, folder: Path) -> None:
    folder.mkdir(exist_ok=True)
    for i in range(9):
        (folder / f"{i + 1:02d} Entry {i + 1}").mkdir(exist_ok=True)
    config = json.loads(config_path.read_text())
    config["pinned"] = [{"kind": "folder", "target": folder.as_uri()}]
    config.update(
        update_check_enabled=False,
        startup_tips_enabled=False,
        global_search_enabled=False,
    )
    config_path.write_text(json.dumps(config))


if __name__ == "__main__":
    if sys.argv[1] == "run":
        run()
    elif sys.argv[1] == "seed":
        seed(Path(sys.argv[2]), Path(sys.argv[3]))
