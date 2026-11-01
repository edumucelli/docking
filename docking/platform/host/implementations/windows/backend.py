"""Windows host paths, durability, hook commands and advisory writer locks."""

from pathlib import Path

from docking.platform.host.base import HostFamily, HostServices

from . import locking, paths, process


class WindowsHostServices(HostServices):
    platform_name = "win32"
    family = HostFamily.WINDOWS
    mount_points = staticmethod(paths.mount_points)
    prepare_process = staticmethod(process.prepare_process)
    local_path = staticmethod(paths.local_path_from_uri_or_path)
    is_explicit_path = staticmethod(paths.is_explicit_path)
    config_home = staticmethod(paths.xdg_config_home)
    cache_home = staticmethod(paths.xdg_cache_home)
    data_home = staticmethod(paths.xdg_data_home)
    state_home = staticmethod(paths.xdg_state_home)
    writer_lock = staticmethod(locking.writer_lock)
    hook_argv = staticmethod(process.hook_argv)
    hook_command = staticmethod(process.hook_command)

    def flush_directory(self, path: Path) -> None:
        # The Windows CRT cannot open directories as file descriptors. File
        # contents remain flushed by their existing persistence orchestrators.
        return
