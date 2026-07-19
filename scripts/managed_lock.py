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
    parent_fd: int | None = None
    lock_fd: int | None = None


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


def _private_directory(path: Path) -> None:
    if os.name == "nt":
        return
    info = _lstat(path)
    if info is None or _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise LockError(f"unsafe managed lock directory: {path}")
    try:
        os.chmod(path, 0o700, follow_symlinks=False)
    except (NotImplementedError, OSError) as exc:
        raise LockError(f"cannot restrict managed lock directory: {exc}") from exc
    if stat.S_IMODE(path.lstat().st_mode) & 0o077:
        raise LockError(f"managed lock directory remains accessible: {path}")


def _directory_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def open_directory(path: Path, *, create: bool = False) -> tuple[int, tuple[Path, ...]]:
    """Open an absolute POSIX directory one no-follow component at a time."""
    path = _canonical(path)
    if not path.is_absolute():
        raise LockError(f"unsafe managed lock directory: {path}")
    created: list[Path] = []
    current = Path(path.anchor)
    try:
        fd = os.open(path.anchor, _directory_flags())
    except OSError as exc:
        raise LockError(f"cannot open managed lock root: {exc}") from exc
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, _directory_flags(), dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise LockError(f"managed lock directory component is missing: {current / part}")
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    created.append(current / part)
                    child = os.open(part, _directory_flags(), dir_fd=fd)
                except OSError as exc:
                    raise LockError(f"cannot create managed lock directory: {current / part}: {exc}") from exc
            except OSError as exc:
                raise LockError(f"unsafe managed lock directory component: {current / part}: {exc}") from exc
            os.close(fd)
            fd = child
            current /= part
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise LockError(f"unsafe managed lock directory: {path}")
        return fd, tuple(created)
    except Exception:
        os.close(fd)
        raise


def verify(handle: LockHandle) -> None:
    """Verify that the held POSIX lock and its rooted parent are still reachable."""
    if handle.parent_fd is None or handle.lock_fd is None:
        _validate_chain(handle.path, handle.path.parent.parent)
        return
    current_fd, _ = open_directory(handle.path.parent)
    try:
        anchored_parent = os.fstat(handle.parent_fd)
        current_parent = os.fstat(current_fd)
        if (anchored_parent.st_dev, anchored_parent.st_ino) != (current_parent.st_dev, current_parent.st_ino):
            raise LockError("managed lock directory was replaced while the lock was held")
        held = os.fstat(handle.lock_fd)
        visible = os.stat(handle.path.name, dir_fd=handle.parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(held.st_mode) or held.st_nlink != 1 or (held.st_dev, held.st_ino) != handle.identity or (visible.st_dev, visible.st_ino) != handle.identity:
            raise LockError("managed lock was replaced while held")
    except FileNotFoundError as exc:
        raise LockError("managed lock disappeared while held") from exc
    finally:
        os.close(current_fd)


def acquire(path: Path, root: Path) -> LockHandle:
    path, root = _canonical(path), _canonical(root)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise LockError(f"unsafe managed lock path: {path}") from exc
    if not relative.parts:
        raise LockError(f"unsafe managed lock path: {path}")
    if os.name != "nt":
        parent_fd, created = open_directory(path.parent, create=True)
        lock_fd: int | None = None
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            try:
                lock_fd = os.open(path.name, flags, 0o600, dir_fd=parent_fd)
            except FileExistsError as exc:
                raise LockError("INSTALL_LOCKED: another managed writer or stale crash lock is present; fail closed") from exc
            info = os.fstat(lock_fd)
            parent_info = os.fstat(parent_fd)
            current_uid = os.geteuid()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != current_uid:
                raise LockError("unsafe managed lock file")
            if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != current_uid:
                raise LockError("unsafe managed lock directory ownership")
            os.write(lock_fd, (str(os.getpid()) + "\n").encode("ascii"))
            os.fsync(lock_fd)
            os.fchmod(lock_fd, 0o600)
            parent_was_created = path.parent in created
            if parent_was_created and stat.S_IMODE(os.fstat(parent_fd).st_mode) & 0o077:
                raise LockError("new managed lock directory is not private")
            if stat.S_IMODE(os.fstat(lock_fd).st_mode) & 0o077:
                raise LockError("managed lock permissions remain accessible")
            identity = (info.st_dev, info.st_ino)
            handle = LockHandle(path, identity, tuple(reversed(created)), parent_fd, lock_fd)
            verify(handle)
            return handle
        except Exception:
            if lock_fd is not None:
                try:
                    visible = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                    held = os.fstat(lock_fd)
                    if (visible.st_dev, visible.st_ino) == (held.st_dev, held.st_ino):
                        os.unlink(path.name, dir_fd=parent_fd)
                except OSError:
                    pass
                os.close(lock_fd)
            os.close(parent_fd)
            raise
    _validate_chain(path, root)
    missing: list[Path] = []
    cursor = path.parent
    while _lstat(cursor) is None:
        missing.append(cursor)
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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
    _private_directory(path.parent)
    return LockHandle(path, (info.st_dev, info.st_ino), tuple(missing))


def release(handle: LockHandle) -> None:
    if handle.parent_fd is not None and handle.lock_fd is not None:
        try:
            held = os.fstat(handle.lock_fd)
            visible = os.stat(handle.path.name, dir_fd=handle.parent_fd, follow_symlinks=False)
            if (held.st_dev, held.st_ino) == handle.identity and (visible.st_dev, visible.st_ino) == handle.identity:
                os.unlink(handle.path.name, dir_fd=handle.parent_fd)
        except OSError:
            pass
        finally:
            os.close(handle.lock_fd)
            os.close(handle.parent_fd)
        for directory in handle.created_directories:
            try:
                parent_fd, _ = open_directory(directory.parent)
                try:
                    os.rmdir(directory.name, dir_fd=parent_fd)
                finally:
                    os.close(parent_fd)
            except (LockError, OSError):
                pass
        return
    info = _lstat(handle.path)
    if info is not None and not _is_link_or_reparse(info) and (info.st_dev, info.st_ino) == handle.identity:
        handle.path.unlink()
    for directory in handle.created_directories:
        try:
            directory.rmdir()
        except OSError:
            pass
