"""Unix executable registration preserving generated desktop identities."""

from functools import lru_cache
from pathlib import Path

from docking.platform.applications.backend import ExecutableRegistrationBackend

from ...backend import GeneratedDesktopEntry
from . import entries


@lru_cache(maxsize=512)
def _is_native_executable(path: Path) -> bool:
    """Return whether *path* currently has the Linux ELF signature."""
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


class UnixExecutableRegistrationBackend(ExecutableRegistrationBackend):
    def clear_cache(self) -> None:
        _is_native_executable.cache_clear()

    def generated_id(self, path: Path) -> str:
        return entries.generated_desktop_id_for_path(path)

    def register(
        self, target: str | Path, *, startup_wm_class: str = ""
    ) -> GeneratedDesktopEntry | None:
        return entries.create_desktop_entry_for_executable(
            target, startup_wm_class=startup_wm_class
        )

    def desktop_id(self, target: str) -> str | None:
        return entries.desktop_id_from_uri_or_path(target)

    def appimage_needing_permission(self, target: str | Path) -> Path | None:
        return entries.appimage_path_needing_executable_permission(target)

    def make_executable(self, path: Path) -> bool:
        return entries.make_user_executable(path)

    def match_aliases(
        self, desktop_id: str, wm_class: str, exec_line: str
    ) -> list[str]:
        return entries.match_aliases(desktop_id, wm_class, exec_line)

    def is_native_executable(self, path: Path) -> bool:
        return _is_native_executable(path)
