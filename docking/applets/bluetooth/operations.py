"""Typed native operations used by this applet."""

from __future__ import annotations

from abc import ABC, abstractmethod


class BluetoothOperations(ABC):
    @abstractmethod
    def send_files_command(self) -> list[str] | None: ...

    @abstractmethod
    def devices_command(self) -> list[str] | None: ...

    @abstractmethod
    def adapters_command(self) -> list[str] | None: ...

    @abstractmethod
    def local_services_command(self) -> list[str] | None: ...

    @abstractmethod
    def open_send_files(self) -> bool: ...

    @abstractmethod
    def open_devices(self) -> bool: ...

    @abstractmethod
    def open_adapters(self) -> bool: ...

    @abstractmethod
    def open_local_services(self) -> bool: ...
