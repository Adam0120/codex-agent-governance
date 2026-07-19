#!/usr/bin/env python3
"""Shared exclusive no-follow lock for govern-agent-system managed writers."""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


class LockError(RuntimeError):
    pass


@dataclass(frozen=True)
class LockHandle:
    path: Path
    identity: tuple[int, int]
    created_directories: tuple[Path, ...]


def _canonical(path: Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _is_link_or_reparse(info: os.stat_result) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & reparse)


def _validate_chain(path: Path, root: Path) -> None:
    path, root = _canonical(path), _canonical(root)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise LockError(f"unsafe managed lock path: {path}") from exc
    if not relative.parts:
        raise LockError(f"unsafe managed lock path: {path}")
    components = [root]
    for part in relative.parts:
        components.append(components[-1] / part)
    for component in components:
        info = _lstat(component)
        if info is not None and _is_link_or_reparse(info):
            raise LockError(f"symlink or reparse point is not allowed in managed lock path: {component}")


def acquire(path: Path, root: Path) -> LockHandle:
    path, root = _canonical(path), _canonical(root)
    _validate_chain(path, root)
    missing: list[Path] = []
    cursor = path.parent
    while _lstat(cursor) is None:
        missing.append(cursor)
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    _validate_chain(path, root)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError as exc:
        raise LockError("INSTALL_LOCKED: another managed writer or stale crash lock is present; fail closed") from exc
    except OSError as exc:
        raise LockError(f"cannot acquire managed lock: {exc}") from exc
    try:
        os.write(fd, (str(os.getpid()) + "\n").encode("ascii"))
        os.fsync(fd)
        info = os.fstat(fd)
    finally:
        os.close(fd)
    return LockHandle(path, (info.st_dev, info.st_ino), tuple(missing))


def release(handle: LockHandle) -> None:
    info = _lstat(handle.path)
    if info is not None and not _is_link_or_reparse(info) and (info.st_dev, info.st_ino) == handle.identity:
        handle.path.unlink()
    for directory in handle.created_directories:
        try:
            directory.rmdir()
        except OSError:
            pass
