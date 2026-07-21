#!/usr/bin/env python3
"""Safely install, inspect, uninstall, snapshot, or roll back govern-agent-system."""
from __future__ import annotations

import argparse
import hashlib
import json
import ntpath
import os
import shutil
import stat
import sys
import tempfile
import time
import tomllib
import uuid
import re
from pathlib import Path
from collections.abc import Mapping
from typing import Any

import managed_lock

SOURCE = Path(os.path.abspath(__file__)).parents[1]
IDENTITY = "govern-agent-system"
INSTALL_VERSION = "0.2.3"
MANIFEST_SCHEMA = 1
SNAPSHOT_SCHEMA = 2
SNAPSHOT_PURPOSES = {"install", "uninstall", "rollback-recovery"}
ROLE_RUNTIME = {
    "default": ("gpt-5.6-luna", "high", "read-only"),
    "worker": ("gpt-5.6-luna", "high", "workspace-write"),
    "explorer": ("gpt-5.6-luna", "high", "read-only"),
    "code_locator": ("gpt-5.3-codex-spark", "high", "read-only"),
    "cross_module_architect": ("gpt-5.6-terra", "medium", "read-only"),
    "systems_safety": ("gpt-5.6-terra", "medium", "workspace-write"),
    "semantic_reviewer": ("gpt-5.6-sol", "medium", "read-only"),
    "release_operator": ("gpt-5.6-terra", "medium", "workspace-write"),
}
ROLE_SANDBOX = {name: runtime[2] for name, runtime in ROLE_RUNTIME.items()}
ROLE_NAMES = tuple(sorted(ROLE_RUNTIME))
SKILL_SOURCE = SOURCE / "SKILL.md"
ADAPTER_SOURCE = SOURCE / ".codex" / "agents"
CONFIG_KEY_ORDER = ("max_threads", "max_depth")
MANAGED_AGENTS = {"max_threads": 6, "max_depth": 1}
MANAGED_KEY_LINE = re.compile(
    r'''^\s*(?P<key>max_depth|max_threads|"max_depth"|"max_threads"|'max_depth'|'max_threads')\s*='''
)
MIN_MODERN_INSTALL_VERSION = (0, 2, 0)
SOURCE_IGNORED_PARTS = {".git", "__pycache__", ".pytest_cache", "build", "dist"}
RUNTIME_IGNORED_PARTS = {"__pycache__", ".pytest_cache"}
SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SNAPSHOT_NAME = re.compile(r"^snapshot-[a-f0-9]{32}$")
TRANSACTION_TOKEN = re.compile(r"^[a-f0-9]{32}$")
STAGING_NAME = re.compile(r"^\.govern-agent-system\.[A-Za-z0-9_-]{6,64}$")
JOURNAL_OPERATIONS = {"install", "uninstall", "rollback"}
JOURNAL_STATUSES = {"promoting", "recovering", "recovery_failed", "recovered"}


class InstallError(Exception):
    pass


def private_permission_enforcement() -> str:
    return "posix_mode" if os.name != "nt" else "not_available"


def _descriptor_flags(*, directory: bool = False) -> int:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    return flags | (getattr(os, "O_DIRECTORY", 0) if directory else 0)


def _owned_descriptor(fd: int, path: Path, kind: str, *, sensitive: bool = True) -> os.stat_result:
    info = os.fstat(fd)
    expected = stat.S_ISDIR(info.st_mode) if kind == "directory" else stat.S_ISREG(info.st_mode)
    if not expected or info.st_uid != os.geteuid() or (kind == "file" and sensitive and info.st_nlink != 1):
        raise InstallError(f"unsafe managed permission target: {path}")
    return info


def _open_owned_at(parent_fd: int, name: str, path: Path, kind: str, *, sensitive: bool = True) -> int:
    try:
        fd = os.open(name, _descriptor_flags(directory=kind == "directory"), dir_fd=parent_fd)
    except OSError as exc:
        raise InstallError(f"unsafe managed permission target: {path}: {exc}") from exc
    try:
        info = _owned_descriptor(fd, path, kind, sensitive=sensitive)
        visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (visible.st_dev, visible.st_ino) != (info.st_dev, info.st_ino):
            raise InstallError(f"managed permission target changed while opening: {path}")
        return fd
    except Exception:
        os.close(fd)
        raise


def _apply_private_modes(targets: list[tuple[int, int, str, Path, int]]) -> None:
    changed: list[tuple[int, int]] = []
    try:
        for fd, parent_fd, name, path, mode in targets:
            before = os.fstat(fd)
            visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (visible.st_dev, visible.st_ino) != (before.st_dev, before.st_ino):
                raise InstallError(f"managed permission target changed before remediation: {path}")
            original = stat.S_IMODE(before.st_mode)
            os.fchmod(fd, mode)
            changed.append((fd, original))
            after = os.fstat(fd)
            visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (visible.st_dev, visible.st_ino) != (after.st_dev, after.st_ino) or stat.S_IMODE(after.st_mode) & 0o077:
                raise InstallError(f"managed permission target remains unsafe: {path}")
    except (InstallError, OSError) as exc:
        rollback_error: OSError | None = None
        for fd, original in reversed(changed):
            try:
                os.fchmod(fd, original)
            except OSError as restore_exc:
                rollback_error = restore_exc
        if rollback_error is not None:
            raise InstallError(f"permission remediation and rollback failed: {exc}; {rollback_error}") from exc
        if isinstance(exc, InstallError):
            raise
        raise InstallError(f"cannot restrict managed permission target: {exc}") from exc


def _descriptor_bytes(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _collect_open_tree(fd: int, path: Path, targets: list[tuple[int, int, str, Path, int]], *, source_tree: bool = False) -> str:
    ignored = SOURCE_IGNORED_PARTS if source_tree else RUNTIME_IGNORED_PARTS
    records: list[tuple[str, bytes]] = []

    def walk(directory_fd: int, relative: Path) -> None:
        try:
            names = sorted(os.listdir(directory_fd))
        except OSError as exc:
            raise InstallError(f"cannot inspect managed permission tree: {path / relative}: {exc}") from exc
        for child_name in names:
            child_relative = relative / child_name
            child_path = path / child_relative
            try:
                child_fd = os.open(child_name, _descriptor_flags(), dir_fd=directory_fd)
            except OSError as exc:
                raise InstallError(f"unsafe managed permission tree entry: {child_path}: {exc}") from exc
            try:
                info = os.fstat(child_fd)
                visible = os.stat(child_name, dir_fd=directory_fd, follow_symlinks=False)
                if (visible.st_dev, visible.st_ino) != (info.st_dev, info.st_ino) or info.st_uid != os.geteuid():
                    raise InstallError(f"managed permission tree entry changed or is not owned: {child_path}")
                skipped = any(part in ignored for part in child_relative.parts) or child_relative.suffix == ".pyc"
                encoded = child_relative.as_posix().encode("utf-8")
                if stat.S_ISDIR(info.st_mode):
                    targets.append((child_fd, directory_fd, child_name, child_path, 0o700))
                    owned_fd = child_fd
                    child_fd = -1
                    if not skipped:
                        records.append((child_relative.as_posix(), b"D\0" + encoded + b"\0"))
                    walk(owned_fd, child_relative)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    targets.append((child_fd, directory_fd, child_name, child_path, 0o600))
                    if not skipped:
                        records.append((child_relative.as_posix(), b"F\0" + encoded + b"\0" + hashlib.sha256(_descriptor_bytes(child_fd)).digest()))
                    child_fd = -1
                else:
                    raise InstallError(f"unsupported or hard-linked managed permission tree entry: {child_path}")
            finally:
                if child_fd >= 0:
                    os.close(child_fd)
    walk(fd, Path())
    digest = hashlib.sha256()
    for _, record in sorted(records):
        digest.update(record)
    return digest.hexdigest()


def _collect_tree_targets(parent_fd: int, name: str, path: Path, targets: list[tuple[int, int, str, Path, int]]) -> str:
    fd = _open_owned_at(parent_fd, name, path, "directory")
    targets.append((fd, parent_fd, name, path, 0o700))
    return _collect_open_tree(fd, path, targets)


def restrict_path(path: Path, mode: int, kind: str) -> None:
    if os.name == "nt":
        return
    try:
        parent_fd, _ = managed_lock.open_directory(path.parent)
    except managed_lock.LockError as exc:
        raise InstallError(str(exc)) from exc
    fd: int | None = None
    try:
        fd = _open_owned_at(parent_fd, path.name, path, kind)
        _apply_private_modes([(fd, parent_fd, path.name, path, mode)])
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def restrict_tree(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        parent_fd, _ = managed_lock.open_directory(path.parent)
    except managed_lock.LockError as exc:
        raise InstallError(str(exc)) from exc
    targets: list[tuple[int, int, str, Path, int]] = []
    try:
        _collect_tree_targets(parent_fd, path.name, path, targets)
        _apply_private_modes(targets)
    finally:
        for fd, _, _, _, _ in reversed(targets):
            os.close(fd)
        os.close(parent_fd)


def permission_problem(path: Path, kind: str) -> dict[str, str] | None:
    if os.name == "nt":
        return None
    info = lstat_or_none(path)
    if info is None:
        return None
    if is_link_or_reparse(info):
        return {"path": str(path), "kind": kind, "reason": "unsafe_link_or_reparse"}
    expected = 0o700 if kind == "directory" else 0o600
    if (kind == "directory" and not stat.S_ISDIR(info.st_mode)) or (kind == "file" and not stat.S_ISREG(info.st_mode)):
        return {"path": str(path), "kind": kind, "reason": "unexpected_type"}
    if info.st_uid != os.geteuid():
        return {"path": str(path), "kind": kind, "reason": "unexpected_owner"}
    if kind == "file" and info.st_nlink != 1:
        return {"path": str(path), "kind": kind, "reason": "unsafe_hard_link"}
    actual = stat.S_IMODE(info.st_mode)
    if actual & 0o077:
        return {"path": str(path), "kind": kind, "reason": "group_or_other_access", "mode": f"{actual:04o}", "expected_mode": f"{expected:04o}"}
    return None


def permission_problems(p: dict[str, Path]) -> list[dict[str, str]]:
    if os.name == "nt":
        return []
    problems: list[dict[str, str]] = []
    for path, kind in ((p["config"], "file"), (p["state"], "directory")):
        problem = permission_problem(path, kind)
        if problem:
            problems.append(problem)
    state = p["state"]
    info = lstat_or_none(state)
    if info is None or is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        return problems
    stack = [state]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                children = [Path(entry.path) for entry in entries]
        except OSError:
            problems.append({"path": str(current), "kind": "directory", "reason": "unreadable"})
            continue
        for child in children:
            child_info = lstat_or_none(child)
            kind = "directory" if child_info is not None and stat.S_ISDIR(child_info.st_mode) and not is_link_or_reparse(child_info) else "file"
            problem = permission_problem(child, kind)
            if problem:
                problems.append(problem)
            if child_info is not None and not is_link_or_reparse(child_info) and stat.S_ISDIR(child_info.st_mode):
                stack.append(child)
    return problems


def canonical(path: Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def trusted_root(raw: Path) -> Path:
    absolute = canonical(raw)
    root = absolute.parent.resolve(strict=False) / absolute.name
    info = lstat_or_none(root)
    if info is not None and is_link_or_reparse(info):
        raise InstallError(f"trusted managed root may not be a symlink or reparse point: {root}")
    return root


def configured_home(environ: Mapping[str, str] | None = None, fallback: Path | None = None) -> Path:
    source = os.environ if environ is None else environ
    raw = source.get("HOME")
    return Path(raw) if raw else (Path.home() if fallback is None else fallback)


def paths() -> dict[str, Path]:
    home = trusted_root(configured_home())
    codex = trusted_root(Path(os.environ.get("CODEX_HOME", str(home / ".codex"))))
    state = codex / "agent-system"
    skills = home / ".agents" / "skills"
    return {
        "home": home,
        "codex": codex,
        "state": state,
        "agents": codex / "agents",
        "config": codex / "config.toml",
        "skills": skills,
        "skill": skills / IDENTITY,
        "snapshots": state / "snapshots",
        "lock": state / "install.lock",
        "manifest": state / "managed-install.json",
        "journal": state / "rollback-journal.json",
        "ledger": state / "ledger.jsonl",
    }


def lexically_contained(path: Path, root: Path) -> bool:
    path_text, root_text = os.path.normcase(str(canonical(path))), os.path.normcase(str(canonical(root)))
    try:
        return os.path.commonpath((path_text, root_text)) == root_text and path_text != root_text
    except ValueError:
        return False


def lstat_or_none(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def is_link_or_reparse(info: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & reparse_flag)


def lexical_path(path: Path) -> Path:
    return Path(os.path.abspath(str(path)))


def lexical_path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(lexical_path(path))))


def normalize_windows_link_target(raw: str) -> str:
    if raw.startswith("\\\\?\\UNC\\"):
        raw = "\\\\" + raw[8:]
    elif raw.startswith("\\\\?\\"):
        raw = raw[4:]
    return ntpath.normcase(ntpath.normpath(raw))


def existing_lexical_symlink_target(path: Path) -> Path:
    if not path.is_symlink():
        raise InstallError(f"managed Skill reparse point is not a symlink: {path}")
    try:
        raw = os.readlink(path)
        if os.name == "nt":
            raw = normalize_windows_link_target(raw)
        target = Path(raw)
        if not target.is_absolute():
            target = path.parent / target
        target = lexical_path(target)
        target.resolve(strict=True)
        return target
    except (OSError, RuntimeError) as exc:
        raise InstallError(f"unsafe or broken link at {path}: {exc}") from exc


def managed_root(p: dict[str, Path], path: Path) -> Path:
    path = canonical(path)
    if path == p["codex"] or lexically_contained(path, p["codex"]):
        return p["codex"]
    if path == p["home"] or lexically_contained(path, p["home"]):
        return p["home"]
    raise InstallError(f"path is outside trusted managed roots: {path}")


def validate_chain(path: Path, root: Path, *, allow_final_symlink_to: Path | None = None, skip_final: bool = False) -> None:
    path, root = canonical(path), canonical(root)
    try: relative = path.relative_to(root)
    except ValueError as exc: raise InstallError(f"path escapes trusted managed root: {path}") from exc
    components = [root]
    for part in relative.parts: components.append(components[-1] / part)
    for component in components:
        info = lstat_or_none(component)
        if info is None or not is_link_or_reparse(info):
            continue
        if component == path and skip_final:
            continue
        if component == path and allow_final_symlink_to is not None:
            try:
                expected = lexical_path(Path(allow_final_symlink_to))
                expected.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise InstallError(f"unsafe recorded link target at {component}: {exc}") from exc
            if lexical_path_key(existing_lexical_symlink_target(component)) == lexical_path_key(expected):
                continue
        raise InstallError(f"symlink or reparse point is not allowed: {component}")


def validate_destinations(p: dict[str, Path], *, allow_skill_symlink_to: Path | None = None, skip_skill_final: bool = False) -> None:
    for child, root in (
        (p["state"], p["codex"]),
        (p["agents"], p["codex"]),
        (p["config"], p["codex"]),
        (p["snapshots"], p["codex"]),
        (p["lock"], p["codex"]),
        (p["manifest"], p["codex"]),
        (p["journal"], p["codex"]),
        (p["ledger"], p["codex"]),
        (p["skill"], p["skills"]),
    ):
        if not lexically_contained(child, root):
            raise InstallError(f"destination escapes its root: {child}")
    for key in ("codex", "state", "agents", "config", "snapshots", "lock", "manifest", "journal", "ledger", "skills"):
        validate_chain(p[key], managed_root(p, p[key]))
    validate_chain(p["skill"], p["home"], allow_final_symlink_to=allow_skill_symlink_to, skip_final=skip_skill_final)
    for name in ROLE_NAMES:
        item = p["agents"] / f"{name}.toml"
        validate_chain(item, p["codex"])


def mkdir_safe(path: Path, p: dict[str, Path]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    validate_chain(path, managed_root(p, path))


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_hash(path: Path) -> str:
    info = path.lstat()
    if is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
        raise InstallError(f"expected regular file: {path}")
    return sha256_bytes(path.read_bytes())


def tree_hash(root: Path, *, source_tree: bool = False) -> str:
    info = root.lstat()
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise InstallError(f"expected ordinary directory: {root}")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        ignored = SOURCE_IGNORED_PARTS if source_tree else RUNTIME_IGNORED_PARTS
        if any(part in ignored for part in relative.parts) or relative.suffix == ".pyc":
            continue
        item_info = path.lstat()
        if is_link_or_reparse(item_info):
            raise InstallError(f"managed trees may not contain links or reparse points: {path}")
        name = relative.as_posix().encode("utf-8")
        if stat.S_ISDIR(item_info.st_mode):
            digest.update(b"D\0" + name + b"\0")
        elif stat.S_ISREG(item_info.st_mode):
            digest.update(b"F\0" + name + b"\0" + hashlib.sha256(path.read_bytes()).digest())
        else:
            raise InstallError(f"unsupported managed tree entry: {path}")
    return digest.hexdigest()


def managed_config_hash(values: dict[str, Any]) -> str:
    return sha256_bytes(json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def exact_keys(value: Any, required: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        raise InstallError(f"invalid {context} schema")
    return value


def exact_integer(value: Any, expected: int) -> bool:
    """JSON booleans and floats must not impersonate integer schema values."""
    return type(value) is int and value == expected


def modern_format_compatible(installed: str) -> bool:
    """Use the manifest schema and provenance as the gate, not version ordering."""
    if not VERSION.fullmatch(installed):
        return False
    try:
        installed_version = tuple(int(part) for part in installed.split("."))
    except ValueError:
        return False
    return MIN_MODERN_INSTALL_VERSION <= installed_version


def compatible_managed_agents(managed: Any) -> bool:
    """Validate the stable v0.2 managed-config shape independently of release order."""
    return (
        isinstance(managed, dict)
        and set(managed) == set(MANAGED_AGENTS)
        and type(managed.get("max_threads")) is int
        and managed["max_threads"] > 0
        and type(managed.get("max_depth")) is int
        and managed["max_depth"] >= 0
    )


def expected_destinations(p: dict[str, Path]) -> dict[str, str]:
    return {
        "agents": str(p["agents"]),
        "config": str(p["config"]),
        "manifest": str(p["manifest"]),
        "skill": str(p["skill"]),
    }


def validate_manifest_document(document: Any, p: dict[str, Path], *, verify_content: bool) -> dict[str, Any]:
    manifest = exact_keys(
        document,
        {"schema_version", "identity", "installer_version", "destinations", "link", "skill", "adapters", "config"},
        "managed manifest",
    )
    if not exact_integer(manifest["schema_version"], MANIFEST_SCHEMA) or manifest["identity"] != IDENTITY or not isinstance(manifest["installer_version"], str) or not VERSION.fullmatch(manifest["installer_version"]):
        raise InstallError("managed manifest identity or version mismatch")
    if exact_keys(manifest["destinations"], set(expected_destinations(p)), "manifest destinations") != expected_destinations(p):
        raise InstallError("managed manifest canonical destination mismatch")
    if not isinstance(manifest["link"], bool):
        raise InstallError("invalid managed manifest link flag")
    skill = exact_keys(manifest["skill"], {"kind", "content_sha256", "target"}, "manifest skill")
    if skill["kind"] not in {"directory", "symlink"} or not isinstance(skill["content_sha256"], str) or not SHA256.fullmatch(skill["content_sha256"]):
        raise InstallError("invalid managed Skill record")
    if manifest["link"]:
        valid_target = isinstance(skill["target"], str) and Path(skill["target"]).is_absolute() and str(canonical(Path(skill["target"]))) == skill["target"]
    else:
        valid_target = skill["target"] is None
    if not valid_target or (manifest["link"] != (skill["kind"] == "symlink")):
        raise InstallError("managed Skill kind or target mismatch")
    adapters = exact_keys(manifest["adapters"], set(ROLE_NAMES), "manifest adapters")
    for name, record in adapters.items():
        record = exact_keys(record, {"path", "sha256"}, f"adapter {name}")
        if record["path"] != str(p["agents"] / f"{name}.toml") or not isinstance(record["sha256"], str) or not SHA256.fullmatch(record["sha256"]):
            raise InstallError(f"invalid managed adapter record: {name}")
    config = exact_keys(manifest["config"], {"path", "managed", "managed_sha256"}, "manifest config")
    if config["path"] != str(p["config"]) or not isinstance(config["managed"], dict) or not config["managed"] or any(not isinstance(key, str) or not SAFE_ID.fullmatch(key) for key in config["managed"]):
        raise InstallError("invalid managed config record")
    if not isinstance(config["managed_sha256"], str) or not SHA256.fullmatch(config["managed_sha256"]) or config["managed_sha256"] != managed_config_hash(config["managed"]):
        raise InstallError("invalid managed config hash")
    if not modern_format_compatible(manifest["installer_version"]):
        raise InstallError("unsupported managed installer version")
    if not compatible_managed_agents(config["managed"]):
        raise InstallError("managed config is incompatible with the current manifest format")
    if not verify_content:
        return manifest
    if manifest["link"]:
        recorded_target = Path(skill["target"])
        validate_chain(p["skill"], p["home"], allow_final_symlink_to=recorded_target)
        if lexical_path_key(existing_lexical_symlink_target(p["skill"])) != lexical_path_key(recorded_target):
            raise InstallError("managed Skill link mismatch")
        actual_skill_hash = tree_hash(recorded_target, source_tree=True)
    else:
        validate_chain(p["skill"], p["home"])
        actual_skill_hash = tree_hash(p["skill"])
    if actual_skill_hash != skill["content_sha256"]:
        raise InstallError("managed Skill content hash mismatch")
    for name, record in adapters.items():
        if file_hash(p["agents"] / f"{name}.toml") != record["sha256"]:
            raise InstallError(f"managed adapter content hash mismatch: {name}")
    try:
        parsed = tomllib.loads(p["config"].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise InstallError(f"managed config cannot be verified: {exc}") from exc
    agents = parsed.get("agents")
    if not isinstance(agents, dict) or any(type(agents.get(key)) is not type(value) or agents.get(key) != value for key, value in config["managed"].items()):
        raise InstallError("managed config values mismatch")
    return manifest


def load_managed_manifest(p: dict[str, Path], *, verify_content: bool = True) -> dict[str, Any] | None:
    info = lstat_or_none(p["manifest"])
    if info is None:
        return None
    if is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
        raise InstallError("managed manifest must be a regular file")
    try:
        document = json.loads(p["manifest"].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError(f"malformed managed manifest: {exc}") from exc
    return validate_manifest_document(document, p, verify_content=verify_content)


def packaged_file(path: Path) -> bytes:
    try:
        fd = os.open(path, _descriptor_flags())
    except OSError as exc:
        raise InstallError(f"cannot open packaged runtime file: {path}: {exc}") from exc
    try:
        info = os.fstat(fd)
        visible = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or is_link_or_reparse(visible)
            or (visible.st_dev, visible.st_ino) != (info.st_dev, info.st_ino)
        ):
            raise InstallError(f"unsafe packaged runtime file: {path}")
        return _descriptor_bytes(fd)
    except OSError as exc:
        raise InstallError(f"cannot read packaged runtime file: {path}: {exc}") from exc
    finally:
        os.close(fd)


def packaged_adapters() -> dict[str, str]:
    info = lstat_or_none(ADAPTER_SOURCE)
    if info is None or is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise InstallError("packaged adapter directory is unsafe or missing")
    actual = {path.name for path in ADAPTER_SOURCE.iterdir()}
    expected = {f"{name}.toml" for name in ROLE_NAMES}
    if actual != expected:
        raise InstallError("packaged adapter set must contain exactly eight role TOMLs")
    result: dict[str, str] = {}
    for name in ROLE_NAMES:
        raw = packaged_file(ADAPTER_SOURCE / f"{name}.toml")
        try:
            text = raw.decode("utf-8")
            document = tomllib.loads(text)
        except (UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise InstallError(f"invalid packaged adapter: {name}: {exc}") from exc
        document = exact_keys(
            document,
            {"name", "description", "model", "model_reasoning_effort", "sandbox_mode", "developer_instructions"},
            f"packaged adapter {name}",
        )
        if (
            document["name"] != name
            or (document["model"], document["model_reasoning_effort"], document["sandbox_mode"]) != ROLE_RUNTIME[name]
            or not isinstance(document["description"], str)
            or not document["description"]
            or not isinstance(document["developer_instructions"], str)
            or not document["developer_instructions"]
        ):
            raise InstallError(f"packaged adapter contract mismatch: {name}")
        result[name] = text
    return result


def reject_multiline_toml_strings(original: str) -> None:
    """Fail closed before line-based mutation can misread string content as TOML structure."""
    state = "bare"
    index = 0
    while index < len(original):
        char = original[index]
        if state == "bare":
            if char == "#":
                newline = original.find("\n", index)
                index = len(original) if newline < 0 else newline + 1
                continue
            if char == '"':
                if original.startswith('"""', index):
                    raise InstallError("multiline TOML strings are unsupported for safe [agents] merge")
                state = "basic"
            elif char == "'":
                if original.startswith("'''", index):
                    raise InstallError("multiline TOML strings are unsupported for safe [agents] merge")
                state = "literal"
        elif state == "basic":
            if char == "\\":
                index += 2
                continue
            if char == '"':
                state = "bare"
        elif char == "'":
            state = "bare"
        index += 1


def render_agents_config(original: str) -> str:
    try:
        parsed = tomllib.loads(original) if original else {}
    except tomllib.TOMLDecodeError as exc:
        raise InstallError(f"invalid TOML before mutation: {exc}") from exc
    if not isinstance(parsed, dict) or ("agents" in parsed and not isinstance(parsed["agents"], dict)):
        raise InstallError("agents configuration must be a table")
    agents = parsed.get("agents", {})
    if "enabled" in agents:
        raise InstallError("unmanaged [agents].enabled is unsupported; refusing to delete it")
    reject_multiline_toml_strings(original)
    if re.search(r'''(?m)^\s*(?:agents|"agents"|'agents')\s*\.''', original):
        raise InstallError("unsupported dotted agents keys; refuse ambiguous merge")
    values = {
        key: str(MANAGED_AGENTS[key]).lower() if isinstance(MANAGED_AGENTS[key], bool) else str(MANAGED_AGENTS[key])
        for key in CONFIG_KEY_ORDER
    }
    lines = original.splitlines(keepends=True)
    out: list[str] = []
    in_agents = False
    found_agents = False

    def append_managed() -> None:
        if out and not out[-1].endswith(("\n", "\r")):
            out.append("\n")
        for key in CONFIG_KEY_ORDER:
            out.append(f"{key} = {values[key]}\n")

    for line in lines:
        table = re.match(r"^\s*\[([^]]+)\]\s*(?:#.*)?$", line)
        if table:
            if in_agents:
                append_managed()
            table_name = table.group(1).strip()
            if len(table_name) >= 2 and table_name[0] == table_name[-1] and table_name[0] in {"'", '"'}:
                table_name = table_name[1:-1]
            in_agents = table_name == "agents"
            found_agents |= in_agents
            out.append(line)
            continue
        if in_agents and re.match(r"^\s*enabled\s*=", line):
            raise InstallError("unmanaged [agents].enabled is unsupported; refusing to delete it")
            continue
        if not (in_agents and MANAGED_KEY_LINE.match(line)):
            out.append(line)
    if in_agents:
        append_managed()
    elif not found_agents:
        if not original:
            out = ["[agents]\n"]
        else:
            if not original.endswith("\n"):
                out.append("\n")
            out += ["\n[agents]\n"]
        append_managed()
    rendered = "".join(out)
    try:
        tomllib.loads(rendered)
    except tomllib.TOMLDecodeError as exc:
        raise InstallError(f"invalid staged TOML: {exc}") from exc
    return rendered


def render_agents_config_without_managed(original: str, managed: dict[str, Any]) -> str:
    """Remove only this installer's proven keys while preserving TOML semantics."""
    if not compatible_managed_agents(managed):
        raise InstallError("managed config is incompatible with the current manifest format")
    try:
        parsed = tomllib.loads(original)
    except tomllib.TOMLDecodeError as exc:
        raise InstallError(f"invalid TOML before uninstall: {exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("agents"), dict):
        raise InstallError("managed agents configuration is missing")
    agents = parsed["agents"]
    if any(type(agents.get(key)) is not type(value) or agents.get(key) != value for key, value in managed.items()):
        raise InstallError("managed config values mismatch")
    reject_multiline_toml_strings(original)
    if re.search(r'''(?m)^\s*(?:agents|"agents"|'agents')\s*\.''', original):
        raise InstallError("unsupported dotted agents keys; refuse ambiguous uninstall")

    lines = original.splitlines(keepends=True)
    out: list[str] = []
    in_agents = False
    removed: set[str] = set()
    for line in lines:
        table = re.match(r"^\s*\[([^]]+)\]\s*(?:#.*)?$", line)
        if table:
            table_name = table.group(1).strip()
            if len(table_name) >= 2 and table_name[0] == table_name[-1] and table_name[0] in {"'", '"'}:
                table_name = table_name[1:-1]
            in_agents = table_name == "agents"
            out.append(line)
            continue
        match = MANAGED_KEY_LINE.match(line) if in_agents else None
        if match:
            removed.add(match.group("key").strip("'\""))
        else:
            out.append(line)
    if removed != set(managed):
        raise InstallError("cannot locate every managed config key for safe uninstall")

    rendered = "".join(out)
    try:
        rendered_document = tomllib.loads(rendered)
    except tomllib.TOMLDecodeError as exc:
        raise InstallError(f"invalid staged TOML after uninstall: {exc}") from exc
    expected_document = dict(parsed)
    expected_agents = dict(agents)
    for key in managed:
        expected_agents.pop(key)
    expected_document["agents"] = expected_agents
    if rendered_document != expected_document:
        raise InstallError("uninstall config rewrite would change unmanaged TOML semantics")
    return rendered


def build_install_plan(p: dict[str, Path]) -> tuple[dict[str, Any], list[tuple[Path, str]]]:
    validate_destinations(p, skip_skill_final=True)
    load_managed_manifest(p)
    try:
        original = p["config"].read_text(encoding="utf-8") if p["config"].exists() else ""
    except (OSError, UnicodeError) as exc:
        raise InstallError(f"cannot read Codex config: {exc}") from exc
    adapters = packaged_adapters()
    writes = [(p["agents"] / f"{name}.toml", adapters[name]) for name in ROLE_NAMES]
    writes.append((p["config"], render_agents_config(original)))
    return {"role_count": len(ROLE_NAMES), "config_migration": "none"}, writes


def build_uninstall_plan(p: dict[str, Path]) -> tuple[dict[str, Any], str]:
    validate_destinations(p, skip_skill_final=True)
    manifest = load_managed_manifest(p)
    if manifest is None:
        raise InstallError("NOT_MANAGED: no verified govern-agent-system installation is present")
    try:
        original = p["config"].read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise InstallError(f"cannot read Codex config: {exc}") from exc
    return manifest, render_agents_config_without_managed(original, manifest["config"]["managed"])


def inspect(p: dict[str, Path], writes: list[tuple[Path, str]]) -> dict[str, Any]:
    validate_destinations(p, skip_skill_final=True)
    manifest = load_managed_manifest(p)
    managed = manifest is not None
    skill_info = lstat_or_none(p["skill"])
    conflicts: list[str] = []
    if not managed and skill_info is not None:
        validate_chain(p["skill"], p["home"])
        conflicts.append("skill")
    for name in ROLE_NAMES:
        item = p["agents"] / f"{name}.toml"
        if not managed and lstat_or_none(item) is not None:
            conflicts.append(name)
    if managed:
        planned_adapters = {path.name: sha256_bytes(content.encode("utf-8")) for path, content in writes if path.parent == p["agents"]}
        if set(planned_adapters) != {f"{name}.toml" for name in ROLE_NAMES}:
            raise InstallError("incomplete adapter mutation plan")
    return {
        "ok": not conflicts,
        "skill": str(p["skill"]),
        "managed": managed,
        "config_migration": "none",
        "agent_conflicts": conflicts,
        "mcp_touched": False,
        "permission_enforcement": private_permission_enforcement(),
        "permission_problems": permission_problems(p),
    }


def acquire_lock(p: dict[str, Path]) -> managed_lock.LockHandle:
    try:
        return managed_lock.acquire(p["lock"], p["codex"])
    except managed_lock.LockError as exc:
        if "INSTALL_LOCKED" not in str(exc):
            raise InstallError(str(exc)) from exc
        locked_error = str(exc)
    try:
        managed_lock.reclaim_stale(p["lock"], p["codex"])
        handle = managed_lock.acquire(p["lock"], p["codex"])
    except managed_lock.LockError as exc:
        raise InstallError(f"{locked_error}; stale-lock reclamation refused: {exc}") from exc
    try:
        if recovery_journal(p) is None:
            cleanup_orphaned_staging(p)
        return handle
    except Exception:
        release_lock(handle)
        raise


def acquire_recovery_lock(p: dict[str, Path]) -> managed_lock.LockHandle:
    return acquire_lock(p)


def cleanup_orphaned_staging(p: dict[str, Path]) -> None:
    """Clean the reserved staging namespace only after a dead-owner lock was proven."""
    parents = {
        p["skill"].parent,
        p["agents"],
        p["config"].parent,
        p["manifest"].parent,
    }
    for parent in parents:
        info = lstat_or_none(parent)
        if info is None:
            continue
        validate_chain(parent, managed_root(p, parent))
        if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise InstallError(f"unsafe transaction staging parent: {parent}")
        changed = False
        for item in list(parent.iterdir()):
            if not STAGING_NAME.fullmatch(item.name):
                continue
            item_info = item.lstat()
            if os.name != "nt" and item_info.st_uid != os.geteuid():
                raise InstallError(f"orphaned transaction artifact is not owned by the current user: {item}")
            validate_chain(item, managed_root(p, item), skip_final=True)
            remove_path(item)
            changed = True
        if changed:
            fsync_directory(parent)


def release_lock(lock: managed_lock.LockHandle) -> None:
    managed_lock.release(lock)


def hold_lock_for_test() -> None:
    raw = os.environ.get("CAG_HOLD_LOCK_SECONDS")
    if raw:
        try:
            seconds = float(raw)
        except ValueError as exc:
            raise InstallError("invalid lock hold duration") from exc
        if not 0 <= seconds <= 5:
            raise InstallError("invalid lock hold duration")
        time.sleep(seconds)


def copy_file_verified(source: Path, target: Path, expected_hash: str) -> None:
    shutil.copy2(source, target)
    restrict_path(target, 0o600, "file")
    fsync_file(target)
    if file_hash(target) != expected_hash:
        raise InstallError(f"staged file hash mismatch: {source}")


def fsync_file(path: Path) -> None:
    info = lstat_or_none(path)
    if info is None or is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
        raise InstallError(f"cannot make non-regular transaction file durable: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise InstallError(f"cannot open transaction file for durability: {path}: {exc}") from exc
    try:
        os.fsync(fd)
    except OSError as exc:
        raise InstallError(f"cannot make transaction file durable: {path}: {exc}") from exc
    finally:
        os.close(fd)


def fsync_tree_bottom_up(root: Path) -> None:
    info = lstat_or_none(root)
    if info is None or is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise InstallError(f"cannot make non-directory snapshot tree durable: {root}")
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        child_info = lstat_or_none(child)
        if child_info is None or is_link_or_reparse(child_info):
            raise InstallError(f"unsafe snapshot tree entry during durability sync: {child}")
        if stat.S_ISREG(child_info.st_mode):
            fsync_file(child)
        elif stat.S_ISDIR(child_info.st_mode):
            fsync_tree_bottom_up(child)
        else:
            raise InstallError(f"unsupported snapshot tree entry during durability sync: {child}")
    fsync_directory(root)


def snapshot_entries(p: dict[str, Path]) -> list[tuple[str, Path]]:
    return [
        ("skill", p["skill"]),
        ("config", p["config"]),
        ("managed-manifest", p["manifest"]),
        *((f"agent-{name}", p["agents"] / f"{name}.toml") for name in ROLE_NAMES),
    ]


def verified_snapshot_directories_windows(p: dict[str, Path]) -> list[Path]:
    snapshots = p["snapshots"]
    info = lstat_or_none(snapshots)
    if info is None:
        return []
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise InstallError("unsafe managed snapshot root")
    result: list[Path] = []
    for snapshot in sorted(snapshots.iterdir(), key=lambda item: item.name):
        snapshot_info = lstat_or_none(snapshot)
        if snapshot_info is None or is_link_or_reparse(snapshot_info) or not stat.S_ISDIR(snapshot_info.st_mode) or not SNAPSHOT_NAME.fullmatch(snapshot.name):
            raise InstallError(f"cannot prove managed snapshot ownership: {snapshot}")
        _, entries = read_snapshot(str(snapshot), p)
        expected = {"manifest.json"} | {entry["label"] for entry in entries if entry["kind"] in {"file", "directory"}}
        actual = {item.name for item in snapshot.iterdir()}
        if actual != expected:
            raise InstallError(f"cannot prove managed snapshot contents: {snapshot}")
        result.append(snapshot)
    return result


def _collect_verified_snapshots(
    p: dict[str, Path],
    snapshots_fd: int,
    targets: list[tuple[int, int, str, Path, int]],
) -> None:
    for name in sorted(os.listdir(snapshots_fd)):
        snapshot = p["snapshots"] / name
        if not SNAPSHOT_NAME.fullmatch(name):
            raise InstallError(f"cannot prove managed snapshot ownership: {snapshot}")
        _, path_entries = read_snapshot(str(snapshot), p)
        snapshot_fd = _open_owned_at(snapshots_fd, name, snapshot, "directory")
        targets.append((snapshot_fd, snapshots_fd, name, snapshot, 0o700))
        manifest_path = snapshot / "manifest.json"
        manifest_fd = _open_owned_at(snapshot_fd, "manifest.json", manifest_path, "file")
        targets.append((manifest_fd, snapshot_fd, "manifest.json", manifest_path, 0o600))
        try:
            document = json.loads(_descriptor_bytes(manifest_fd).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise InstallError(f"invalid snapshot: {exc}") from exc
        document = exact_keys(document, {"schema_version", "identity", "installer_version", "purpose", "entries"}, "snapshot")
        if not exact_integer(document["schema_version"], SNAPSHOT_SCHEMA) or document["identity"] != IDENTITY or not isinstance(document["installer_version"], str) or not VERSION.fullmatch(document["installer_version"]) or document["purpose"] not in SNAPSHOT_PURPOSES or document["entries"] != path_entries:
            raise InstallError("snapshot changed during provenance validation")
        expected_names = {"manifest.json"} | {entry["label"] for entry in path_entries if entry["kind"] in {"file", "directory"}}
        if set(os.listdir(snapshot_fd)) != expected_names:
            raise InstallError(f"cannot prove managed snapshot contents: {snapshot}")
        for entry in path_entries:
            label, kind = entry["label"], entry["kind"]
            item_path = snapshot / label
            if kind == "file":
                item_fd = _open_owned_at(snapshot_fd, label, item_path, "file")
                targets.append((item_fd, snapshot_fd, label, item_path, 0o600))
                if sha256_bytes(_descriptor_bytes(item_fd)) != entry["sha256"]:
                    raise InstallError("snapshot file content changed during permission validation")
            elif kind == "directory":
                digest = _collect_tree_targets(snapshot_fd, label, item_path, targets)
                if digest != entry["sha256"]:
                    raise InstallError("snapshot directory content changed during permission validation")


def harden_existing_managed_permissions(p: dict[str, Path], lock: managed_lock.LockHandle) -> None:
    manifest_document = load_managed_manifest(p)
    if manifest_document is None:
        return
    if os.name == "nt":
        state = p["state"]
        validate_chain(state, p["codex"])
        info = lstat_or_none(state)
        if info is None or is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise InstallError("unsafe managed state root")
        allowed = {p["snapshots"].name, p["manifest"].name, p["journal"].name, p["lock"].name, p["ledger"].name}
        if any(item.name not in allowed for item in state.iterdir()):
            raise InstallError("cannot prove managed state ownership")
        for path in (p["manifest"], p["journal"], p["lock"], p["ledger"]):
            item = lstat_or_none(path)
            if item is not None and (is_link_or_reparse(item) or not stat.S_ISREG(item.st_mode)):
                raise InstallError(f"unsafe managed permission target: {path}")
        verified_snapshot_directories_windows(p)
        return
    try:
        managed_lock.verify(lock)
        codex_fd, _ = managed_lock.open_directory(p["codex"])
    except managed_lock.LockError as exc:
        raise InstallError(str(exc)) from exc
    if lock.parent_fd is None:
        os.close(codex_fd)
        raise InstallError("POSIX managed lock did not retain its rooted directory")
    state_fd = os.dup(lock.parent_fd)
    targets: list[tuple[int, int, str, Path, int]] = []
    state = p["state"]
    try:
        state_info = _owned_descriptor(state_fd, state, "directory")
        visible_state = os.stat(state.name, dir_fd=codex_fd, follow_symlinks=False)
        if (visible_state.st_dev, visible_state.st_ino) != (state_info.st_dev, state_info.st_ino):
            raise InstallError("managed state root was replaced while the lock was held")
        targets.append((state_fd, codex_fd, state.name, state, 0o700))
        state_fd = -1
        anchored_state_fd = targets[0][0]
        allowed = {p["snapshots"].name, p["manifest"].name, p["journal"].name, p["lock"].name, p["ledger"].name}
        actual = set(os.listdir(anchored_state_fd))
        if not {p["manifest"].name, p["lock"].name}.issubset(actual) or actual - allowed:
            raise InstallError("cannot prove managed state ownership")
        config_fd = _open_owned_at(codex_fd, p["config"].name, p["config"], "file")
        targets.append((config_fd, codex_fd, p["config"].name, p["config"], 0o600))
        opened_manifest: dict[str, Any] | None = None
        for path in (p["manifest"], p["journal"], p["lock"], p["ledger"]):
            if path.name not in actual:
                continue
            item_fd = _open_owned_at(anchored_state_fd, path.name, path, "file")
            targets.append((item_fd, anchored_state_fd, path.name, path, 0o600))
            if path == p["manifest"]:
                try:
                    opened_manifest = json.loads(_descriptor_bytes(item_fd).decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise InstallError(f"malformed managed manifest: {exc}") from exc
        if validate_manifest_document(opened_manifest, p, verify_content=True) != manifest_document:
            raise InstallError("managed manifest changed during permission validation")
        if p["snapshots"].name in actual:
            snapshots_fd = _open_owned_at(anchored_state_fd, p["snapshots"].name, p["snapshots"], "directory")
            targets.append((snapshots_fd, anchored_state_fd, p["snapshots"].name, p["snapshots"], 0o700))
            _collect_verified_snapshots(p, snapshots_fd, targets)
        _apply_private_modes(targets)
    finally:
        if state_fd >= 0:
            os.close(state_fd)
        for fd, _, _, _, _ in reversed(targets):
            os.close(fd)
        os.close(codex_fd)


def create_snapshot(p: dict[str, Path], purpose: str) -> Path:
    mkdir_safe(p["snapshots"], p)
    restrict_path(p["state"], 0o700, "directory")
    restrict_path(p["snapshots"], 0o700, "directory")
    target = p["snapshots"] / f"snapshot-{uuid.uuid4().hex}"
    target.mkdir(mode=0o700)
    restrict_path(target, 0o700, "directory")
    entries: list[dict[str, Any]] = []
    try:
        for label, path in snapshot_entries(p):
            record: dict[str, Any] = {"label": label, "path": str(path), "kind": "missing", "sha256": None, "target": None}
            info = lstat_or_none(path)
            if info is None:
                pass
            elif is_link_or_reparse(info):
                if label != "skill" or not path.is_symlink():
                    raise InstallError(f"unsafe snapshot source: {path}")
                record.update(kind="symlink", target=os.readlink(path), sha256=tree_hash(canonical(path.parent / os.readlink(path)), source_tree=True))
            elif stat.S_ISREG(info.st_mode):
                record.update(kind="file", sha256=file_hash(path))
                copy_file_verified(path, target / label, record["sha256"])
            elif stat.S_ISDIR(info.st_mode):
                expected_hash = tree_hash(path)
                shutil.copytree(path, target / label)
                restrict_tree(target / label)
                copied_hash = tree_hash(target / label)
                if copied_hash != expected_hash:
                    raise InstallError(f"snapshot directory changed while copying: {path}")
                fsync_tree_bottom_up(target / label)
                record.update(kind="directory", sha256=copied_hash)
            else:
                raise InstallError(f"unsupported snapshot source: {path}")
            entries.append(record)
        document = {
            "schema_version": SNAPSHOT_SCHEMA,
            "identity": IDENTITY,
            "installer_version": INSTALL_VERSION,
            "purpose": purpose,
            "entries": entries,
        }
        manifest = target / "manifest.json"
        manifest.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        restrict_path(manifest, 0o600, "file")
        fsync_file(manifest)
        fsync_directory(target)
        fsync_directory(p["snapshots"])
        fsync_directory(p["state"])
        return target
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def read_snapshot(raw: str, p: dict[str, Path]) -> tuple[Path, list[dict[str, Any]]]:
    source = canonical(Path(raw))
    if not lexically_contained(source, p["snapshots"]):
        raise InstallError("snapshot is outside current snapshot root")
    validate_chain(source, p["codex"])
    manifest_path = source / "manifest.json"
    manifest_info = lstat_or_none(manifest_path)
    if manifest_info is None or is_link_or_reparse(manifest_info) or not stat.S_ISREG(manifest_info.st_mode):
        raise InstallError("invalid snapshot manifest path")
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError(f"invalid snapshot: {exc}") from exc
    document = exact_keys(document, {"schema_version", "identity", "installer_version", "purpose", "entries"}, "snapshot")
    if not exact_integer(document["schema_version"], SNAPSHOT_SCHEMA) or document["identity"] != IDENTITY or not isinstance(document["installer_version"], str) or not VERSION.fullmatch(document["installer_version"]) or document["purpose"] not in SNAPSHOT_PURPOSES:
        raise InstallError("invalid snapshot identity or version")
    expected = dict(snapshot_entries(p))
    entries = document["entries"]
    if not isinstance(entries, list) or len(entries) != len(expected):
        raise InstallError("invalid snapshot entry count")
    checked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in entries:
        entry = exact_keys(value, {"label", "path", "kind", "sha256", "target"}, "snapshot entry")
        label, kind = entry["label"], entry["kind"]
        if label not in expected or label in seen or entry["path"] != str(expected[label]) or kind not in {"missing", "file", "directory", "symlink"}:
            raise InstallError("invalid snapshot entry identity")
        seen.add(label)
        if kind == "missing":
            if entry["sha256"] is not None or entry["target"] is not None:
                raise InstallError("invalid missing snapshot entry")
        elif not isinstance(entry["sha256"], str) or not SHA256.fullmatch(entry["sha256"]):
            raise InstallError("invalid snapshot content hash")
        elif kind == "file":
            if entry["target"] is not None or file_hash(source / label) != entry["sha256"]:
                raise InstallError("snapshot file content mismatch")
        elif kind == "directory":
            if entry["target"] is not None or tree_hash(source / label) != entry["sha256"]:
                raise InstallError("snapshot directory content mismatch")
        else:
            if label != "skill" or not isinstance(entry["target"], str):
                raise InstallError("invalid snapshot link")
            link_target = canonical(expected[label].parent / entry["target"])
            if not Path(entry["target"]).is_absolute() or str(link_target) != entry["target"] or tree_hash(link_target, source_tree=True) != entry["sha256"]:
                raise InstallError("snapshot link content mismatch")
        checked.append(entry)
    if seen != set(expected):
        raise InstallError("duplicate or missing snapshot labels")
    return source, checked


def snapshot_state_matches(entries: list[dict[str, Any]], p: dict[str, Path]) -> bool:
    destinations = dict(snapshot_entries(p))
    try:
        for entry in entries:
            destination, kind = destinations[entry["label"]], entry["kind"]
            info = lstat_or_none(destination)
            if kind == "missing":
                if info is not None:
                    return False
            elif info is None:
                return False
            elif kind == "file":
                if file_hash(destination) != entry["sha256"]:
                    return False
            elif kind == "directory":
                if tree_hash(destination) != entry["sha256"]:
                    return False
            elif not destination.is_symlink() or os.readlink(destination) != entry["target"]:
                return False
            elif tree_hash(canonical(destination.parent / entry["target"]), source_tree=True) != entry["sha256"]:
                return False
        return True
    except (InstallError, OSError):
        return False


def stage_file(parent: Path, content: bytes, p: dict[str, Path]) -> Path:
    mkdir_safe(parent, p)
    fd, raw = tempfile.mkstemp(prefix=".govern-agent-system.", dir=parent)
    path = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        restrict_path(path, 0o600, "file")
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def stage_snapshot(source: Path, entries: list[dict[str, Any]], p: dict[str, Path]) -> list[tuple[Path, Path | None]]:
    destinations = dict(snapshot_entries(p))
    staged: list[tuple[Path, Path | None]] = []
    try:
        for entry in entries:
            destination = destinations[entry["label"]]
            mkdir_safe(destination.parent, p)
            kind = entry["kind"]
            if kind == "missing":
                item = None
            elif kind == "file":
                item = stage_file(destination.parent, (source / entry["label"]).read_bytes(), p)
                if file_hash(item) != entry["sha256"]:
                    raise InstallError("staged restore file hash mismatch")
            elif kind == "directory":
                container = Path(tempfile.mkdtemp(prefix=".govern-agent-system.", dir=destination.parent))
                item = container / "payload"
                staged.append((destination, item))
                shutil.copytree(source / entry["label"], item)
                if tree_hash(item) != entry["sha256"]:
                    raise InstallError("staged restore directory hash mismatch")
            else:
                item = destination.parent / f".govern-agent-system.{uuid.uuid4().hex}"
                os.symlink(entry["target"], item, target_is_directory=True)
            if kind != "directory":
                staged.append((destination, item))
        return staged
    except Exception:
        cleanup_staged(staged)
        raise


def cleanup_staged(staged: list[tuple[Path, Path | None]]) -> None:
    for _, item in staged:
        if item is None:
            continue
        container = item.parent if item.name == "payload" and item.parent.name.startswith(".govern-agent-system.") else None
        try:
            if item.is_symlink() or item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        except OSError:
            pass
        if container is not None:
            shutil.rmtree(container, ignore_errors=True)


def remove_path(path: Path) -> None:
    info = lstat_or_none(path)
    if info is None:
        return
    if is_link_or_reparse(info) or stat.S_ISREG(info.st_mode):
        path.unlink()
    elif stat.S_ISDIR(info.st_mode):
        shutil.rmtree(path)
    else:
        raise InstallError(f"cannot replace unsupported destination: {path}")


def write_journal(p: dict[str, Path], document: dict[str, Any]) -> None:
    mkdir_safe(p["state"], p)
    restrict_path(p["state"], 0o700, "directory")
    raw = (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temp = stage_file(p["state"], raw, p)
    os.replace(temp, p["journal"])
    fsync_directory(p["state"])


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise InstallError(f"cannot open transaction directory for durability: {path}: {exc}") from exc
    try:
        os.fsync(fd)
    except OSError as exc:
        raise InstallError(f"cannot make transaction directory durable: {path}: {exc}") from exc
    finally:
        os.close(fd)


def transaction_cleanup_root(item: Path | None) -> Path | None:
    if item is None:
        return None
    if item.name == "payload" and item.parent.name.startswith(".govern-agent-system."):
        return item.parent
    return item


def transaction_steps(
    staged: list[tuple[Path, Path | None]],
    backup_paths: list[tuple[Path, Path]],
) -> tuple[list[dict[str, str | None]], list[str]]:
    steps: list[dict[str, str | None]] = []
    artifacts: list[str] = []
    for (destination, item), (_, backup) in zip(staged, backup_paths):
        steps.append({
            "destination": str(destination),
            "backup": str(backup),
            "staged": str(item) if item is not None else None,
        })
        artifacts.append(str(backup))
        cleanup_root = transaction_cleanup_root(item)
        if cleanup_root is not None:
            artifacts.append(str(cleanup_root))
    return steps, list(dict.fromkeys(artifacts))


def valid_transaction_artifact(path: Path, p: dict[str, Path]) -> bool:
    path = canonical(path)
    for _, destination in snapshot_entries(p):
        destination = canonical(destination)
        if path.parent != destination.parent:
            continue
        backup_prefix = f".{destination.name}.backup-"
        if path.name.startswith(backup_prefix) and TRANSACTION_TOKEN.fullmatch(path.name[len(backup_prefix):]):
            return True
        if STAGING_NAME.fullmatch(path.name):
            return True
    return False


def validate_transaction_artifact(raw: Any, p: dict[str, Path]) -> Path:
    if not isinstance(raw, str):
        raise InstallError("RECOVERY_REQUIRED: invalid transaction artifact path")
    path = canonical(Path(raw))
    if not Path(raw).is_absolute() or str(path) != raw or not valid_transaction_artifact(path, p):
        raise InstallError("RECOVERY_REQUIRED: invalid transaction artifact path")
    validate_chain(path, managed_root(p, path), skip_final=True)
    return path


def validate_transaction_steps(document: dict[str, Any], p: dict[str, Path]) -> None:
    steps = document.get("steps")
    artifacts = document.get("artifacts")
    if not isinstance(steps, list) or not isinstance(artifacts, list) or len(steps) != len(snapshot_entries(p)):
        raise InstallError("RECOVERY_REQUIRED: invalid recovery transaction plan")
    expected_destinations = {str(path) for _, path in snapshot_entries(p)}
    seen: set[str] = set()
    required_artifacts: set[str] = set()
    for value in steps:
        step = exact_keys(value, {"destination", "backup", "staged"}, "recovery transaction step")
        destination_raw = step["destination"]
        if not isinstance(destination_raw, str) or destination_raw not in expected_destinations or destination_raw in seen:
            raise InstallError("RECOVERY_REQUIRED: invalid recovery transaction destination")
        seen.add(destination_raw)
        destination = Path(destination_raw)
        backup = validate_transaction_artifact(step["backup"], p)
        backup_prefix = f".{destination.name}.backup-"
        if backup.parent != destination.parent or not backup.name.startswith(backup_prefix):
            raise InstallError("RECOVERY_REQUIRED: invalid recovery transaction backup")
        required_artifacts.add(str(backup))
        staged_raw = step["staged"]
        if staged_raw is not None:
            if not isinstance(staged_raw, str):
                raise InstallError("RECOVERY_REQUIRED: invalid recovery staged path")
            staged = canonical(Path(staged_raw))
            direct = staged.parent == destination.parent and STAGING_NAME.fullmatch(staged.name)
            payload = staged.name == "payload" and staged.parent.parent == destination.parent and STAGING_NAME.fullmatch(staged.parent.name)
            if not Path(staged_raw).is_absolute() or str(staged) != staged_raw or not (direct or payload):
                raise InstallError("RECOVERY_REQUIRED: invalid recovery staged path")
            cleanup_root = transaction_cleanup_root(staged)
            if cleanup_root is not None:
                required_artifacts.add(str(cleanup_root))
    if seen != expected_destinations or document["destinations"] != [step["destination"] for step in steps]:
        raise InstallError("RECOVERY_REQUIRED: incomplete recovery transaction plan")
    checked_artifacts = [str(validate_transaction_artifact(raw, p)) for raw in artifacts]
    if len(set(checked_artifacts)) != len(checked_artifacts) or not required_artifacts.issubset(checked_artifacts):
        raise InstallError("RECOVERY_REQUIRED: invalid recovery transaction artifacts")


def cleanup_transaction_artifacts(document: dict[str, Any], p: dict[str, Path]) -> list[str]:
    errors: list[str] = []
    for raw in reversed(document.get("artifacts", [])):
        try:
            path = validate_transaction_artifact(raw, p)
            remove_path(path)
            fsync_directory(path.parent)
        except Exception as exc:
            errors.append(str(exc))
    return errors


def close_journal(p: dict[str, Path]) -> None:
    p["journal"].unlink(missing_ok=True)
    fsync_directory(p["state"])


def recovery_journal(p: dict[str, Path]) -> dict[str, Any] | None:
    info = lstat_or_none(p["journal"])
    if info is None:
        return None
    validate_chain(p["journal"], p["codex"])
    if is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
        raise InstallError("RECOVERY_REQUIRED: recovery journal is unsafe")
    try:
        document = json.loads(p["journal"].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError(f"RECOVERY_REQUIRED: invalid recovery journal: {exc}") from exc
    required = {"schema_version", "identity", "operation", "status", "recovery_snapshot", "destinations", "error"}
    allowed = required | {"steps", "artifacts"}
    if not isinstance(document, dict) or not required.issubset(document) or not set(document).issubset(allowed) or not exact_integer(document.get("schema_version"), 1) or document.get("identity") != IDENTITY:
        raise InstallError("RECOVERY_REQUIRED: invalid recovery journal schema")
    if document.get("operation") not in JOURNAL_OPERATIONS or document.get("status") not in JOURNAL_STATUSES:
        raise InstallError("RECOVERY_REQUIRED: invalid recovery journal state")
    if document["error"] is not None and not isinstance(document["error"], str):
        raise InstallError("RECOVERY_REQUIRED: invalid recovery journal error")
    snapshot_raw = document.get("recovery_snapshot")
    if not isinstance(snapshot_raw, str):
        raise InstallError("RECOVERY_REQUIRED: invalid recovery snapshot path")
    snapshot = canonical(Path(snapshot_raw))
    if not Path(snapshot_raw).is_absolute() or str(snapshot) != snapshot_raw or not lexically_contained(snapshot, p["snapshots"]) or not SNAPSHOT_NAME.fullmatch(snapshot.name):
        raise InstallError("RECOVERY_REQUIRED: invalid recovery snapshot path")
    destinations = document.get("destinations")
    expected_destinations = {str(path) for _, path in snapshot_entries(p)}
    if not isinstance(destinations, list) or any(not isinstance(value, str) or value not in expected_destinations for value in destinations) or len(set(destinations)) != len(destinations):
        raise InstallError("RECOVERY_REQUIRED: invalid recovery destinations")
    has_plan = "steps" in document or "artifacts" in document
    if has_plan:
        validate_transaction_steps(document, p)
    elif document["status"] in {"promoting", "recovering"}:
        raise InstallError("RECOVERY_REQUIRED: interrupted transaction lacks a recovery plan")
    return document


def ensure_mutation_allowed(p: dict[str, Path], *, recover: bool = False, snapshot: str | None = None) -> None:
    document = recovery_journal(p)
    fenced = document is not None and document.get("status") != "recovered"
    if not fenced:
        if recover:
            raise InstallError("NO_RECOVERY_REQUIRED: no nonterminal recovery journal is present")
        return
    expected = canonical(Path(document["recovery_snapshot"])) if isinstance(document.get("recovery_snapshot"), str) else None
    supplied = canonical(Path(snapshot)) if snapshot is not None else None
    if not recover or expected is None or supplied != expected:
        raise InstallError(f"RECOVERY_REQUIRED: run rollback --recover --snapshot {document.get('recovery_snapshot')}")


def interrupt_for_test(
    variable: str | None,
    termination_variable: str | None,
    index: int,
    phase: str,
    operation: str,
) -> None:
    if termination_variable and os.environ.get(termination_variable) == f"{index}:{phase}":
        os._exit(86)
    if variable and os.environ.get(variable) == f"{index}:{phase}":
        raise KeyboardInterrupt(f"injected {operation} interruption after {index}:{phase}")


def promote(
    staged: list[tuple[Path, Path | None]],
    *,
    p: dict[str, Path],
    operation: str,
    recovery_snapshot: Path,
    failure_variable: str | None,
    interrupt_variable: str | None,
    termination_variable: str | None,
    fenced_anchor: Path | None = None,
) -> tuple[bool, bool, str | None]:
    token = uuid.uuid4().hex
    backups: list[tuple[Path, Path | None]] = []
    backup_paths = [(destination, destination.parent / f".{destination.name}.backup-{token}") for destination, _ in staged]
    for _, backup in backup_paths:
        validate_chain(backup, managed_root(p, backup))
        if lstat_or_none(backup) is not None:
            raise InstallError(f"rollback backup collision: {backup}")
    steps, artifacts = transaction_steps(staged, backup_paths)
    if fenced_anchor is not None:
        previous = recovery_journal(p)
        if previous is None:
            raise InstallError("RECOVERY_REQUIRED: recovery journal disappeared")
        journal = {
            **previous,
            "status": "recovering",
            "destinations": [str(destination) for destination, _ in staged],
            "error": None,
            "steps": steps,
            "artifacts": list(dict.fromkeys([*previous.get("artifacts", []), *artifacts])),
        }
    else:
        journal = {
            "schema_version": 1,
            "identity": IDENTITY,
            "operation": operation,
            "status": "promoting",
            "recovery_snapshot": str(recovery_snapshot),
            "destinations": [str(destination) for destination, _ in staged],
            "error": None,
            "steps": steps,
            "artifacts": artifacts,
        }
    validate_transaction_steps(journal, p)
    if os.environ.get(f"CAG_TERMINATE_{operation.upper()}_BEFORE_JOURNAL") == "1":
        os._exit(87)
    write_journal(p, journal)
    try:
        for index, ((destination, item), (_, candidate_backup)) in enumerate(zip(staged, backup_paths), 1):
            validate_chain(destination, managed_root(p, destination), skip_final=destination == p["skill"])
            if lstat_or_none(destination) is not None:
                os.replace(destination, candidate_backup)
                backup: Path | None = candidate_backup
                backups.append((destination, backup))
                fsync_directory(destination.parent)
                interrupt_for_test(interrupt_variable, termination_variable, index, "backup", operation)
            else:
                backup = None
                backups.append((destination, backup))
            if item is not None:
                os.replace(item, destination)
                fsync_directory(destination.parent)
                interrupt_for_test(interrupt_variable, termination_variable, index, "destination", operation)
            if failure_variable and os.environ.get(failure_variable) == str(index):
                raise RuntimeError(f"injected {operation} promotion failure after {index}")
        if fenced_anchor is not None:
            _, anchor_entries = read_snapshot(str(fenced_anchor), p)
            if not snapshot_state_matches(anchor_entries, p):
                raise RuntimeError("explicit recovery result does not match the fenced snapshot anchor")
    except Exception as exc:
        recovered = True
        for destination, backup in reversed(backups):
            try:
                remove_path(destination)
                if backup is not None:
                    os.replace(backup, destination)
            except Exception:
                recovered = False
        if recovered:
            try:
                _, entries = read_snapshot(str(recovery_snapshot), p)
                recovered = snapshot_state_matches(entries, p)
            except InstallError:
                recovered = False
        if recovered and os.environ.get("CAG_FAIL_RECOVERY") == "1":
            recovered = False
        if fenced_anchor is not None:
            recovered = False
        cleanup_errors: list[str] = []
        if recovered:
            cleanup_errors = cleanup_transaction_artifacts(journal, p)
            recovered = not cleanup_errors
        if recovered:
            try:
                close_journal(p)
            except Exception as close_exc:
                cleanup_errors.append(str(close_exc))
                recovered = False
        if not recovered:
            journal["status"] = "recovered" if recovered else "recovery_failed"
            detail = [str(exc), *cleanup_errors]
            journal["error"] = "; ".join(value for value in detail if value)
            try:
                write_journal(p, journal)
            except Exception:
                recovered = False
        return False, recovered, str(exc)
    else:
        cleanup_errors = cleanup_transaction_artifacts(journal, p)
        if cleanup_errors:
            message = "promotion committed but backup cleanup failed: " + "; ".join(cleanup_errors)
            journal["status"] = "recovery_failed"
            journal["error"] = message
            write_journal(p, journal)
            return True, False, message
        close_journal(p)
        if os.environ.get(f"CAG_TERMINATE_{operation.upper()}_AFTER_JOURNAL_CLOSE") == "1":
            os._exit(88)
        return True, False, None
    finally:
        cleanup_staged(staged)


def build_install_staging(p: dict[str, Path], writes: list[tuple[Path, str]]) -> tuple[list[tuple[Path, Path | None]], dict[str, Any]]:
    staged: list[tuple[Path, Path | None]] = []
    try:
        mkdir_safe(p["skill"].parent, p)
        skill_raw = packaged_file(SKILL_SOURCE)
        container = Path(tempfile.mkdtemp(prefix=".govern-agent-system.", dir=p["skill"].parent))
        staged_skill = container / "payload"
        staged_skill.mkdir(mode=0o700)
        (staged_skill / "SKILL.md").write_bytes(skill_raw)
        restrict_tree(staged_skill)
        staged.append((p["skill"], staged_skill))
        skill_hash = tree_hash(staged_skill)
        expected_skill_hash = hashlib.sha256(
            b"F\0SKILL.md\0" + hashlib.sha256(skill_raw).digest()
        ).hexdigest()
        if skill_hash != expected_skill_hash:
            raise InstallError("staged Skill does not match packaged Skill")
        write_map = dict(writes)
        config_content = write_map.pop(p["config"])
        adapters: dict[str, dict[str, str]] = {}
        for name in ROLE_NAMES:
            destination = p["agents"] / f"{name}.toml"
            content = write_map.pop(destination)
            raw = content.encode("utf-8")
            staged.append((destination, stage_file(destination.parent, raw, p)))
            adapters[name] = {"path": str(destination), "sha256": sha256_bytes(raw)}
        if write_map:
            raise InstallError("unexpected generation plan entries")
        staged.append((p["config"], stage_file(p["config"].parent, config_content.encode("utf-8"), p)))
        parsed_config = tomllib.loads(config_content)
        managed_values = {key: parsed_config["agents"][key] for key in CONFIG_KEY_ORDER}
        manifest = {
            "schema_version": MANIFEST_SCHEMA,
            "identity": IDENTITY,
            "installer_version": INSTALL_VERSION,
            "destinations": expected_destinations(p),
            "link": False,
            "skill": {
                "kind": "directory",
                "content_sha256": skill_hash,
                "target": None,
            },
            "adapters": adapters,
            "config": {
                "path": str(p["config"]),
                "managed": managed_values,
                "managed_sha256": managed_config_hash(managed_values),
            },
        }
        validate_manifest_document(manifest, p, verify_content=False)
        staged.append((p["manifest"], stage_file(p["manifest"].parent, (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8"), p)))
        return staged, manifest
    except Exception:
        cleanup_staged(staged)
        raise


def build_uninstall_staging(p: dict[str, Path], rendered_config: str) -> list[tuple[Path, Path | None]]:
    staged: list[tuple[Path, Path | None]] = [
        (p["skill"], None),
        *((p["agents"] / f"{name}.toml", None) for name in ROLE_NAMES),
    ]
    try:
        staged.append((p["config"], stage_file(p["config"].parent, rendered_config.encode("utf-8"), p)))
        staged.append((p["manifest"], None))
        return staged
    except Exception:
        cleanup_staged(staged)
        raise


def install() -> dict[str, Any]:
    p = paths()
    ensure_mutation_allowed(p)
    _, preliminary = build_install_plan(p)
    lock = acquire_lock(p)
    try:
        hold_lock_for_test()
        ensure_mutation_allowed(p)
        _, writes = build_install_plan(p)
        if writes != preliminary:
            raise InstallError("configuration changed while acquiring install lock")
        status = inspect(p, writes)
        if not status["ok"]:
            raise InstallError("refusing unmanaged collision or unsafe destination")
        if status["managed"]:
            harden_existing_managed_permissions(p, lock)
        staged, _ = build_install_staging(p, writes)
        try:
            saved = create_snapshot(p, "install")
        except Exception:
            cleanup_staged(staged)
            raise
        committed, recovered, error = promote(
            staged,
            p=p,
            operation="install",
            recovery_snapshot=saved,
            failure_variable="CAG_FAIL_AFTER_SKILL",
            interrupt_variable="CAG_INTERRUPT_INSTALL_AFTER",
            termination_variable="CAG_TERMINATE_INSTALL_AFTER",
        )
        if error is not None:
            return {"ok": False, "error": error, "snapshot": str(saved), "committed": committed, "recovery": recovered, "journal": str(p["journal"]), "mcp_touched": False}
        return {"ok": True, "installed": str(p["skill"]), "snapshot": str(saved), "link": False, "mcp_touched": False}
    finally:
        release_lock(lock)


def uninstall() -> dict[str, Any]:
    p = paths()
    ensure_mutation_allowed(p)
    preliminary = build_uninstall_plan(p)
    lock = acquire_lock(p)
    try:
        hold_lock_for_test()
        ensure_mutation_allowed(p)
        plan = build_uninstall_plan(p)
        if plan != preliminary:
            raise InstallError("managed state changed while acquiring uninstall lock")
        harden_existing_managed_permissions(p, lock)
        staged = build_uninstall_staging(p, plan[1])
        try:
            saved = create_snapshot(p, "uninstall")
        except Exception:
            cleanup_staged(staged)
            raise
        committed, recovered, error = promote(
            staged,
            p=p,
            operation="uninstall",
            recovery_snapshot=saved,
            failure_variable="CAG_FAIL_UNINSTALL_AFTER",
            interrupt_variable="CAG_INTERRUPT_UNINSTALL_AFTER",
            termination_variable="CAG_TERMINATE_UNINSTALL_AFTER",
        )
        if error is not None:
            return {
                "ok": False,
                "error": error,
                "snapshot": str(saved),
                "committed": committed,
                "recovery": recovered,
                "journal": str(p["journal"]),
                "mcp_touched": False,
            }
        return {"ok": True, "uninstalled": str(p["skill"]), "snapshot": str(saved), "mcp_touched": False}
    finally:
        release_lock(lock)


def rollback_locked(raw: str, p: dict[str, Path], recover: bool) -> dict[str, Any]:
    validate_destinations(p, skip_skill_final=True)
    source, entries = read_snapshot(raw, p)
    staged = stage_snapshot(source, entries, p)
    try:
        recovery = create_snapshot(p, "rollback-recovery")
    except Exception:
        cleanup_staged(staged)
        raise
    committed, recovered, error = promote(
        staged,
        p=p,
        operation="rollback",
        recovery_snapshot=recovery,
        failure_variable="CAG_FAIL_ROLLBACK_AFTER",
        interrupt_variable="CAG_INTERRUPT_ROLLBACK_AFTER",
        termination_variable="CAG_TERMINATE_ROLLBACK_AFTER",
        fenced_anchor=source if recover else None,
    )
    if error is not None:
        return {
            "ok": False,
            "error": error,
            "rolled_back": str(source),
            "committed": committed,
            "recovery": recovered,
            "recovery_snapshot": str(source if recover else recovery),
            "attempt_recovery_snapshot": str(recovery) if recover else None,
            "journal": str(p["journal"]),
            "mcp_touched": False,
        }
    return {"ok": True, "rolled_back": str(source), "recovery_snapshot": str(recovery), "mcp_touched": False}


def rollback(raw: str, recover: bool) -> dict[str, Any]:
    p = paths()
    ensure_mutation_allowed(p, recover=recover, snapshot=raw)
    validate_destinations(p, skip_skill_final=True)
    lock = acquire_recovery_lock(p) if recover else acquire_lock(p)
    try:
        hold_lock_for_test()
        ensure_mutation_allowed(p, recover=recover, snapshot=raw)
        return rollback_locked(raw, p, recover)
    finally:
        release_lock(lock)


def check() -> dict[str, Any]:
    p = paths()
    try:
        _, writes = build_install_plan(p)
        return {**inspect(p, writes), "release_version": INSTALL_VERSION}
    except InstallError as exc:
        return {"ok": False, "error": str(exc), "skill": str(p["skill"]), "managed": False, "agent_conflicts": [], "mcp_touched": False, "permission_enforcement": private_permission_enforcement(), "permission_problems": [], "release_version": INSTALL_VERSION}


def fail(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}, sort_keys=True), file=sys.stderr)
    raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=INSTALL_VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    sub.add_parser("install")
    sub.add_parser("uninstall")
    item = sub.add_parser("rollback")
    item.add_argument("--snapshot", required=True)
    item.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "check":
            result = check()
        elif args.command == "install":
            result = install()
        elif args.command == "uninstall":
            result = uninstall()
        else:
            result = rollback(args.snapshot, args.recover)
    except InstallError as exc:
        fail(str(exc))
    print(json.dumps(result, sort_keys=True))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
