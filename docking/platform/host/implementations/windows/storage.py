"""Windows CRT durability behavior; callers still flush file contents."""

from pathlib import Path


def flush_directory(path: Path) -> None:
    # The Windows CRT cannot open directories as file descriptors. File
    # contents remain flushed by their existing persistence orchestrators.
    return
