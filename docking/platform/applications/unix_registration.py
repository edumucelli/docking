"""Unix executable registration through the existing XDG desktop directory."""

from pathlib import Path

from . import entries
from .types import ApplicationInfo


class UnixExecutableRegistration:
    def create(self, target: str | Path, *, startup_wm_class: str) -> str | None:
        generated = entries.create_desktop_entry_for_executable(
            target, startup_wm_class=startup_wm_class
        )
        return generated.desktop_id if generated is not None else None

    def applications(self) -> tuple[ApplicationInfo, ...]:
        # Normal desktop discovery owns the generated files and Gio handles.
        return ()

    def directories(self) -> tuple[Path, ...]:
        return ()
