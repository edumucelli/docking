"""Optional native activation lifecycle for a reusable Search window."""

from collections.abc import Callable
from typing import Protocol


class WindowFocus(Protocol):
    def before_present(self) -> None: ...

    def after_present(self, *, title: str, visible: Callable[[], bool]) -> None: ...

    def cancel(self) -> None: ...
