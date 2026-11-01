"""Native modifier facts supplied by the selected desktop integration."""

from docking.platform.desktop.selection import desktop_services


def text_modifier_active() -> bool:
    """Distinguish text composition modifiers from shortcut chords."""
    return desktop_services().text_modifier_active()
