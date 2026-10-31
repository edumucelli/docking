"""Linux/BSD host operations without runtime or toolkit imports."""

from docking.platform.host.base import HostServices

from . import locking, paths, process, storage


class UnixHostServices(HostServices):
    prepare_process = staticmethod(process.prepare_process)
    local_path = staticmethod(paths.local_path_from_uri_or_path)
    config_home = staticmethod(paths.xdg_config_home)
    cache_home = staticmethod(paths.xdg_cache_home)
    data_home = staticmethod(paths.xdg_data_home)
    state_home = staticmethod(paths.xdg_state_home)
    flush_directory = staticmethod(storage.flush_directory)
    writer_lock = staticmethod(locking.writer_lock)
    hook_argv = staticmethod(process.hook_argv)
    hook_command = staticmethod(process.hook_command)
