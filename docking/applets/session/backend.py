"""Semantic session actions independent of OS command representations."""

from abc import ABC, abstractmethod
from collections.abc import Collection


class SessionBackend(ABC):
    @abstractmethod
    def available_actions(self) -> Collection[str]: ...
    @abstractmethod
    def perform(self, action: str) -> bool: ...

    @abstractmethod
    def stop(self) -> None: ...
