"""Native keyboard facts that GDK cannot represent consistently across desktops."""

from docking.platform.environment import is_windows


def text_modifier_active() -> bool:
    """Distinguish Win32 AltGr from GDK's identical Ctrl+Alt event mask."""
    if is_windows():
        from docking.platform.backends.windows.modifiers import altgr_pressed

        return altgr_pressed()
    return False
