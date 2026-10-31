"""SNI adapter implementing the neutral tray operations."""

from docking.platform.tray.backend import TrayBackend

from .status_notifier import StatusNotifierBackend, unavailable_state


class UnixTrayBackend(StatusNotifierBackend, TrayBackend):
    """Existing SNI transport and menu conversion."""

    initial_state = staticmethod(unavailable_state)
