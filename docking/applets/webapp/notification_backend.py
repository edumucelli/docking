"""Desktop publication contract for engine-owned browser notifications."""

from abc import ABC, abstractmethod
from typing import Any


class NotificationBackend(ABC):
    # GI and WebView2 expose different native notification objects. Each engine
    # adapter owns its objects; none of these handles may cross a worker boundary.
    @abstractmethod
    def show(self, notification: Any) -> bool: ...

    @abstractmethod
    def clear(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...
