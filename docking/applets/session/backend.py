"""Semantic session actions independent of OS command representations."""

from abc import ABC, abstractmethod


class SessionBackend(ABC):
    @abstractmethod
    def available_actions(self) -> tuple[str, ...]: ...
    @abstractmethod
    def perform(self, action: str) -> bool: ...
