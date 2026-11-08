"""Power Profiles contract and platform selection."""

from abc import ABC, abstractmethod

from docking.applets.powerprofiles.state import PowerProfilesState, unavailable_state


class PowerProfilesControlBackend(ABC):
    @abstractmethod
    def get_state(self) -> PowerProfilesState: ...

    @abstractmethod
    def set_active_profile(self, profile: str) -> bool: ...

    def stop(self) -> None:
        """Release backend-owned subscriptions during applet shutdown."""
        return


class NullPowerProfilesBackend(PowerProfilesControlBackend):
    """Unavailable backend placeholder.

    This backend preserves a stable contract when no real backend exists.
    The applet can still render an informative unavailable tooltip/menu.
    """

    def get_state(self) -> PowerProfilesState:
        return unavailable_state(error="No supported backend found")

    def set_active_profile(self, profile: str) -> bool:
        _ = profile
        return False
