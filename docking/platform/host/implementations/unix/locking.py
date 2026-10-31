"""POSIX advisory writer lock on an already open file."""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def writer_lock(fd: int) -> Iterator[None]:
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
