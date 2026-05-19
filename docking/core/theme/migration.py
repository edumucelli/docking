# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Theme schema migration helpers."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docking.log import get_logger

_THEME_MIGRATION_BACKUP_SUFFIX = ".pre-nested-schema.bak"
_THEME_SAVE_LOCK = threading.RLock()
log = get_logger("theme")

# Future migrations should add entries like:
#     "h_padding": "layout.horizontal_padding"
# Keep this empty until a PR introduces the first real rename.
DEPRECATED_THEME_KEYS: dict[str, str] = {}


@dataclass(frozen=True)
class ThemeMigrationChange:
    """One legacy theme key moved or removed during schema migration."""

    old_path: str
    new_path: str
    conflict: bool = False


@dataclass(frozen=True)
class ThemeMigrationResult:
    """Pure result of applying schema migration rules to a theme payload."""

    data: dict[str, Any]
    changes: tuple[ThemeMigrationChange, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.changes)


def migrate_theme_dict(
    data: dict[str, Any],
    deprecated_keys: Mapping[str, str] | None = None,
) -> ThemeMigrationResult:
    """Return a copy of ``data`` with registered legacy keys moved.

    The function is deliberately pure: it does not mutate the caller's dict and
    does not write to disk. File persistence is handled by theme loading only
    after it knows the source path is a user theme.
    """
    migrated = copy.deepcopy(data)
    changes: list[ThemeMigrationChange] = []
    warnings: list[str] = []
    registry = DEPRECATED_THEME_KEYS if deprecated_keys is None else deprecated_keys

    for old_path, new_path in registry.items():
        if old_path == new_path:
            continue
        old_found, old_value = _theme_path_value(migrated, old_path)
        if not old_found:
            continue

        new_found, _new_value = _theme_path_value(migrated, new_path)
        _theme_path_pop(migrated, old_path)
        if new_found:
            changes.append(
                ThemeMigrationChange(
                    old_path=old_path,
                    new_path=new_path,
                    conflict=True,
                )
            )
            warnings.append(
                "Deprecated theme key "
                f"{old_path!r} ignored because {new_path!r} is already set"
            )
            continue

        section_warnings = _theme_path_set(migrated, new_path, old_value)
        warnings.extend(section_warnings)
        changes.append(ThemeMigrationChange(old_path=old_path, new_path=new_path))

    return ThemeMigrationResult(
        data=migrated,
        changes=tuple(changes),
        warnings=tuple(warnings),
    )


def migrate_loaded_theme_data(
    *,
    data: dict[str, Any],
    path: Path,
    user_theme_dir: Path,
) -> dict[str, Any]:
    """Migrate loaded theme data and persist user-theme migrations."""
    migration = migrate_theme_dict(data)
    for warning in migration.warnings:
        log.warning("%s in %s", warning, path)
    if not migration.changed:
        return migration.data

    for change in migration.changes:
        if change.conflict:
            log.warning(
                "Theme %s has both deprecated key %r and replacement %r; "
                "keeping replacement",
                path,
                change.old_path,
                change.new_path,
            )
        else:
            log.info(
                "Migrated theme key for %s: %s -> %s",
                path,
                change.old_path,
                change.new_path,
            )

    if _is_user_theme_path(path=path, user_theme_dir=user_theme_dir):
        try:
            _persist_migrated_user_theme(path=path, data=migration.data)
        except Exception as exc:
            log.warning(
                "Failed to rewrite migrated user theme %s; using in-memory "
                "migration for this session: %s",
                path,
                exc,
            )
    return migration.data


def _theme_path_parts(path: str) -> tuple[str, ...]:
    return tuple(part for part in path.split(".") if part)


def _theme_path_value(data: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    parts = _theme_path_parts(path)
    if not parts:
        return False, None
    current: Any = data
    for part in parts[:-1]:
        if not isinstance(current, Mapping):
            return False, None
        current = current.get(part)
    if not isinstance(current, Mapping):
        return False, None
    leaf = parts[-1]
    if leaf not in current:
        return False, None
    return True, current[leaf]


def _theme_path_pop(data: dict[str, Any], path: str) -> None:
    parts = _theme_path_parts(path)
    if not parts:
        return
    current: Any = data
    for part in parts[:-1]:
        if not isinstance(current, dict):
            return
        current = current.get(part)
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def _theme_path_set(data: dict[str, Any], path: str, value: Any) -> tuple[str, ...]:
    parts = _theme_path_parts(path)
    if not parts:
        return ()
    warnings: list[str] = []
    current = data
    for index, part in enumerate(parts[:-1]):
        if part in current:
            existing = current[part]
            if isinstance(existing, dict):
                current = existing
                continue
            section = ".".join(parts[: index + 1])
            warnings.append(f"Theme section {section!r} is not an object; replacing it")
        child: dict[str, Any] = {}
        current[part] = child
        current = child
    current[parts[-1]] = value
    return tuple(warnings)


def _is_user_theme_path(*, path: Path, user_theme_dir: Path) -> bool:
    try:
        return path.resolve().parent == user_theme_dir.resolve()
    except OSError:
        return path.parent == user_theme_dir


def _persist_migrated_user_theme(*, path: Path, data: dict[str, Any]) -> None:
    with _THEME_SAVE_LOCK:
        _create_theme_migration_backup(path=path)
        _write_theme_json_atomic(path=path, payload=data)


def _theme_migration_backup_path(path: Path) -> Path:
    return path.with_name(f"{path.name}{_THEME_MIGRATION_BACKUP_SUFFIX}")


def _create_theme_migration_backup(*, path: Path) -> Path:
    backup_path = _theme_migration_backup_path(path)
    if backup_path.exists() or not path.exists():
        return backup_path
    backup_tmp = _new_theme_tmp_path(path=backup_path)
    try:
        data = path.read_bytes()
        with backup_tmp.open(mode="wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        backup_tmp.replace(backup_path)
        _fsync_directory(backup_path.parent)
    except Exception:
        try:
            backup_tmp.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            log.warning(
                "Failed to clean up theme backup temp file %s: %s",
                backup_tmp,
                cleanup_exc,
            )
        raise
    return backup_path


def _write_theme_json_atomic(*, path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _new_theme_tmp_path(path=path)
    try:
        with tmp_path.open(mode="w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        with tmp_path.open(encoding="utf-8") as handle:
            candidate = json.load(handle)
        if not isinstance(candidate, dict):
            raise ValueError("migrated theme payload is not a JSON object")
        tmp_path.replace(path)
        _fsync_directory(path.parent)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            log.warning(
                "Failed to clean up theme temp file %s: %s",
                tmp_path,
                cleanup_exc,
            )
        raise


def _new_theme_tmp_path(*, path: Path) -> Path:
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    os.close(fd)
    return Path(tmp_name)


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
