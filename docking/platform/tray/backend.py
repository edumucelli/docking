"""Tray inventory and activation contract."""

from abc import ABC, abstractmethod

from .menu import TrayMenuClient
from .state import StatusTrayState


class TrayBackend(ABC):
    @abstractmethod
    def initial_state(self) -> StatusTrayState: ...
    @abstractmethod
    def close(self) -> None: ...
    @abstractmethod
    def get_state(self) -> StatusTrayState: ...
    @abstractmethod
    def activate(self, identifier: str) -> bool: ...
    @abstractmethod
    def context_menu(self, identifier: str) -> bool: ...
    @abstractmethod
    def menu_client(self, identifier: str) -> TrayMenuClient | None: ...
