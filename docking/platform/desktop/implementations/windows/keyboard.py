"""Native keyboard modifier facts used by shared GTK shortcut capture."""

from docking.platform.native.windows.modifiers import altgr_pressed


def text_modifier_active() -> bool:
    return altgr_pressed()
