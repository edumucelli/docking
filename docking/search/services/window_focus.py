"""Optional native activation lifecycle for a reusable Search window."""

from abc import ABC, abstractmethod
from collections.abc import Callable


class WindowFocus(ABC):
    @abstractmethod
    def before_present(self) -> None: ...

    @abstractmethod
    def after_present(self, *, title: str, visible: Callable[[], bool]) -> None: ...

    @abstractmethod
    def cancel(self) -> None: ...
