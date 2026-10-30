"""Coalesce native Start Menu and AppsFolder changes into the shared registry."""

from collections.abc import Callable
from contextlib import ExitStack

from .shell_changes import ShellChangeMonitor


class CatalogChangeMonitor:
    def __init__(self, changed: Callable[[], None]) -> None:
        self._subscriptions = ExitStack()
        try:
            for folder in (
                "a77f5d77-2e2b-44c3-a6a2-aba601054a51",  # Programs
                "0139d44e-6afe-49f2-8690-3dafcae6ffb8",  # CommonPrograms
                "1e87508d-89c2-42f0-8a7e-645a0f50ca58",  # AppsFolder
            ):
                subscription = ShellChangeMonitor(
                    changed,
                    folder_id=folder,
                    title="Docking application catalog monitor",
                )
                self._subscriptions.callback(subscription.close)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self._subscriptions.close()
