"""Typed native operations used by this applet."""

from __future__ import annotations

from abc import ABC, abstractmethod


class NetworkOperations(ABC):
    @abstractmethod
    def connection_info_command(self) -> list[str] | None: ...

    @abstractmethod
    def edit_connections_command(self) -> list[str] | None: ...

    @abstractmethod
    def open_connection_info(self) -> bool: ...

    @abstractmethod
    def open_edit_connections(self) -> bool: ...

    @abstractmethod
    def open_hidden_wifi_settings(self) -> bool: ...

    @abstractmethod
    def open_new_wifi_settings(self) -> bool: ...
