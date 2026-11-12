"""POSIX advisory writer lock on an already open file."""

import fcntl
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def writer_lock(fd: int, *, path: Path) -> Generator[None, None, None]:
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
