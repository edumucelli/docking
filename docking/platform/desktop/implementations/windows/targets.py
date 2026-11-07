"""Default Shell activation using the canonical Windows path adapter."""

from urllib.parse import urlparse

from docking.core.paths import local_path_from_uri_or_path
from docking.platform.native.windows.shell import open_default_target


def open_uri(uri: str) -> bool:
    native_target = uri
    if urlparse(uri).scheme == "file":
        path = local_path_from_uri_or_path(uri)
        if path is None:
            return False
        native_target = str(path)
    return open_default_target(native_target)
