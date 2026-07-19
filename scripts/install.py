#!/usr/bin/env python3
"""Install, inspect, snapshot, or roll back govern-agent-system without MCP changes."""
from __future__ import annotations

import argparse
import hashlib
import json
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
from typing import Any

import agent_system as core
import managed_lock

SOURCE = Path(__file__).resolve().parents[1]
IDENTITY = "govern-agent-system"
INSTALL_VERSION = "0.1.0"
MANIFEST_SCHEMA = 1
SNAPSHOT_SCHEMA = 2
ROLE_NAMES = tuple(sorted(core.catalog()[0]))
SOURCE_IGNORED_PARTS = {".git", "__pycache__", ".pytest_cache", "build", "dist"}
RUNTIME_IGNORED_PARTS = {"__pycache__", ".pytest_cache"}
SHA256 = core.SHA256
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


class InstallError(Exception):
    pass


def canonical(path: Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def trusted_root(raw: Path) -> Path:
    absolute = canonical(raw)
    root = absolute.parent.resolve(strict=False) / absolute.name
    info = lstat_or_none(root)
    if info is not None and is_link_or_reparse(info):
        raise InstallError(f"trusted managed root may not be a symlink or reparse point: {root}")
    return root


def paths() -> dict[str, Path]:
    home = trusted_root(core.configured_home())
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


def existing_lexical_symlink_target(path: Path) -> Path:
    if not path.is_symlink():
        raise InstallError(f"managed Skill reparse point is not a symlink: {path}")
    try:
        raw = os.readlink(path)
        if raw.startswith("\\\\?\\UNC\\"):
            raw = "\\\\" + raw[8:]
        elif raw.startswith("\\\\?\\"):
            raw = raw[4:]
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
        (p["skill"], p["skills"]),
    ):
        if not lexically_contained(child, root):
            raise InstallError(f"destination escapes its root: {child}")
    for key in ("codex", "state", "agents", "config", "snapshots", "lock", "manifest", "journal", "skills"):
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


def expected_destinations(p: dict[str, Path]) -> dict[str, str]:
    return {
        "agents": str(p["agents"]),
        "config": str(p["config"]),
        "manifest": str(p["manifest"]),
        "skill": str(p["skill"]),
    }


def validate_manifest_document(document: Any, p: dict[str, Path], *, verify_content: bool, allow_managed_skill_link: bool = False) -> dict[str, Any]:
    manifest = exact_keys(
        document,
        {"schema_version", "identity", "installer_version", "destinations", "link", "skill", "adapters", "config"},
        "managed manifest",
    )
    if manifest["schema_version"] != MANIFEST_SCHEMA or manifest["identity"] != IDENTITY or not isinstance(manifest["installer_version"], str) or not VERSION.fullmatch(manifest["installer_version"]):
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
    if config["path"] != str(p["config"]) or not isinstance(config["managed"], dict) or not config["managed"] or any(not isinstance(key, str) or not core.SAFE_ID.fullmatch(key) for key in config["managed"]):
        raise InstallError("invalid managed config record")
    if not isinstance(config["managed_sha256"], str) or not SHA256.fullmatch(config["managed_sha256"]) or config["managed_sha256"] != managed_config_hash(config["managed"]):
        raise InstallError("invalid managed config hash")
    if not verify_content:
        return manifest
    if manifest["link"]:
        if not allow_managed_skill_link:
            raise InstallError("managed Skill link is not authorized for this operation")
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


def load_managed_manifest(p: dict[str, Path], *, verify_content: bool = True, allow_managed_skill_link: bool = False) -> dict[str, Any] | None:
    info = lstat_or_none(p["manifest"])
    if info is None:
        return None
    if is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
        raise InstallError("managed manifest must be a regular file")
    try:
        document = json.loads(p["manifest"].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError(f"malformed managed manifest: {exc}") from exc
    return validate_manifest_document(document, p, verify_content=verify_content, allow_managed_skill_link=allow_managed_skill_link)


def build_generation_plan(p: dict[str, Path]) -> tuple[dict[str, Any], list[tuple[Path, str]]]:
    validate_destinations(p, skip_skill_final=True)
    try:
        metadata, writes = core.generation_plan(Path.cwd())
    except ValueError as exc:
        raise InstallError(str(exc)) from exc
    expected = {p["agents"] / f"{name}.toml" for name in ROLE_NAMES} | {p["config"]}
    if {canonical(path) for path, _ in writes} != expected:
        raise InstallError("generation plan has unexpected destinations")
    return metadata, [(canonical(path), content) for path, content in writes]


def inspect(p: dict[str, Path], writes: list[tuple[Path, str]], *, allow_managed_skill_link: bool = False) -> dict[str, Any]:
    validate_destinations(p, skip_skill_final=True)
    manifest = load_managed_manifest(p, allow_managed_skill_link=allow_managed_skill_link)
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
        "agent_conflicts": conflicts,
        "mcp_touched": False,
    }


def acquire_lock(p: dict[str, Path]) -> managed_lock.LockHandle:
    try:
        return managed_lock.acquire(p["lock"], p["codex"])
    except managed_lock.LockError as exc:
        raise InstallError(str(exc)) from exc


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
    if file_hash(target) != expected_hash:
        raise InstallError(f"staged file hash mismatch: {source}")


def snapshot_entries(p: dict[str, Path]) -> list[tuple[str, Path]]:
    return [
        ("skill", p["skill"]),
        ("config", p["config"]),
        ("managed-manifest", p["manifest"]),
        *((f"agent-{name}", p["agents"] / f"{name}.toml") for name in ROLE_NAMES),
    ]


def create_snapshot(p: dict[str, Path], purpose: str) -> Path:
    mkdir_safe(p["snapshots"], p)
    target = p["snapshots"] / f"snapshot-{uuid.uuid4().hex}"
    target.mkdir()
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
                copied_hash = tree_hash(target / label)
                if copied_hash != expected_hash:
                    raise InstallError(f"snapshot directory changed while copying: {path}")
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
        (target / "manifest.json").write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return target
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def read_snapshot(raw: str, p: dict[str, Path]) -> tuple[Path, list[dict[str, Any]]]:
    source = canonical(Path(raw))
    if not lexically_contained(source, p["snapshots"]):
        raise InstallError("snapshot is outside current snapshot root")
    validate_chain(source, p["codex"])
    try:
        document = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError(f"invalid snapshot: {exc}") from exc
    document = exact_keys(document, {"schema_version", "identity", "installer_version", "purpose", "entries"}, "snapshot")
    if document["schema_version"] != SNAPSHOT_SCHEMA or document["identity"] != IDENTITY or not isinstance(document["installer_version"], str) or not VERSION.fullmatch(document["installer_version"]) or document["purpose"] not in {"install", "rollback-recovery"}:
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
    raw = (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temp = stage_file(p["state"], raw, p)
    os.replace(temp, p["journal"])


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
    if not isinstance(document, dict) or not required.issubset(document) or document.get("schema_version") != 1 or document.get("identity") != IDENTITY:
        raise InstallError("RECOVERY_REQUIRED: invalid recovery journal schema")
    return document


def ensure_mutation_allowed(p: dict[str, Path], *, recover: bool = False, snapshot: str | None = None) -> None:
    document = recovery_journal(p)
    fenced = document is not None and document.get("status") == "recovery_failed"
    if not fenced:
        if recover:
            raise InstallError("NO_RECOVERY_REQUIRED: no recovery_failed journal is present")
        return
    expected = canonical(Path(document["recovery_snapshot"])) if isinstance(document.get("recovery_snapshot"), str) else None
    supplied = canonical(Path(snapshot)) if snapshot is not None else None
    if not recover or expected is None or supplied != expected:
        raise InstallError(f"RECOVERY_REQUIRED: run rollback --recover --snapshot {document.get('recovery_snapshot')}")


def promote(
    staged: list[tuple[Path, Path | None]],
    *,
    p: dict[str, Path],
    operation: str,
    recovery_snapshot: Path,
    failure_variable: str | None,
    fenced_anchor: Path | None = None,
) -> tuple[bool, bool, str | None]:
    token = uuid.uuid4().hex
    backups: list[tuple[Path, Path | None]] = []
    backup_paths = [(destination, destination.parent / f".{destination.name}.backup-{token}") for destination, _ in staged]
    for _, backup in backup_paths:
        validate_chain(backup, managed_root(p, backup))
        if lstat_or_none(backup) is not None:
            raise InstallError(f"rollback backup collision: {backup}")
    journal = recovery_journal(p) if fenced_anchor is not None else {
        "schema_version": 1,
        "identity": IDENTITY,
        "operation": operation,
        "status": "promoting",
        "recovery_snapshot": str(recovery_snapshot),
        "destinations": [str(destination) for destination, _ in staged],
    }
    if fenced_anchor is None:
        write_journal(p, journal)
    try:
        for index, ((destination, item), (_, candidate_backup)) in enumerate(zip(staged, backup_paths), 1):
            validate_chain(destination, managed_root(p, destination), skip_final=destination == p["skill"])
            if lstat_or_none(destination) is not None:
                os.replace(destination, candidate_backup)
                backup: Path | None = candidate_backup
            else:
                backup = None
            backups.append((destination, backup))
            if item is not None:
                os.replace(item, destination)
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
        else:
            journal["status"] = "recovered" if recovered else "recovery_failed"
            journal["error"] = str(exc)
            try:
                write_journal(p, journal)
            except Exception:
                recovered = False
        return False, recovered, str(exc)
    else:
        cleanup_errors: list[str] = []
        for _, backup in backups:
            if backup is not None:
                try:
                    remove_path(backup)
                except Exception as exc:
                    cleanup_errors.append(str(exc))
        if cleanup_errors:
            message = "promotion committed but backup cleanup failed: " + "; ".join(cleanup_errors)
            if fenced_anchor is None:
                journal["status"] = "recovery_failed"
                journal["error"] = message
                write_journal(p, journal)
            return True, False, message
        p["journal"].unlink(missing_ok=True)
        return True, False, None
    finally:
        cleanup_staged(staged)


def build_install_staging(p: dict[str, Path], writes: list[tuple[Path, str]], link: bool) -> tuple[list[tuple[Path, Path | None]], dict[str, Any]]:
    staged: list[tuple[Path, Path | None]] = []
    try:
        mkdir_safe(p["skill"].parent, p)
        expected_source_hash = tree_hash(SOURCE, source_tree=True)
        if link:
            staged_skill = p["skill"].parent / f".govern-agent-system.{uuid.uuid4().hex}"
            os.symlink(SOURCE, staged_skill, target_is_directory=True)
            staged.append((p["skill"], staged_skill))
            skill_hash = expected_source_hash
        else:
            container = Path(tempfile.mkdtemp(prefix=".govern-agent-system.", dir=p["skill"].parent))
            staged_skill = container / "payload"
            staged.append((p["skill"], staged_skill))
            shutil.copytree(SOURCE, staged_skill, ignore=shutil.ignore_patterns(*SOURCE_IGNORED_PARTS, "*.pyc"))
            skill_hash = tree_hash(staged_skill)
            if skill_hash != expected_source_hash:
                raise InstallError("staged Skill does not match source")
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
        managed_values = {key: parsed_config["agents"][key] for key in core.CONFIG_KEY_ORDER}
        manifest = {
            "schema_version": MANIFEST_SCHEMA,
            "identity": IDENTITY,
            "installer_version": INSTALL_VERSION,
            "destinations": expected_destinations(p),
            "link": link,
            "skill": {
                "kind": "symlink" if link else "directory",
                "content_sha256": skill_hash,
                "target": str(canonical(SOURCE)) if link else None,
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


def install(link: bool) -> dict[str, Any]:
    p = paths()
    ensure_mutation_allowed(p)
    _, preliminary = build_generation_plan(p)
    lock = acquire_lock(p)
    try:
        hold_lock_for_test()
        ensure_mutation_allowed(p)
        _, writes = build_generation_plan(p)
        if writes != preliminary:
            raise InstallError("configuration changed while acquiring install lock")
        status = inspect(p, writes, allow_managed_skill_link=link)
        if not status["ok"]:
            raise InstallError("refusing unmanaged collision or unsafe destination")
        staged, _ = build_install_staging(p, writes, link)
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
        )
        if error is not None:
            return {"ok": False, "error": error, "snapshot": str(saved), "committed": committed, "recovery": recovered, "journal": str(p["journal"]), "mcp_touched": False}
        return {"ok": True, "installed": str(p["skill"]), "snapshot": str(saved), "link": link, "mcp_touched": False}
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
    lock = acquire_lock(p)
    try:
        hold_lock_for_test()
        ensure_mutation_allowed(p, recover=recover, snapshot=raw)
        return rollback_locked(raw, p, recover)
    finally:
        release_lock(lock)


def check() -> dict[str, Any]:
    p = paths()
    try:
        _, writes = build_generation_plan(p)
        return inspect(p, writes, allow_managed_skill_link=True)
    except InstallError as exc:
        return {"ok": False, "error": str(exc), "skill": str(p["skill"]), "managed": False, "agent_conflicts": [], "mcp_touched": False}


def fail(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}, sort_keys=True), file=sys.stderr)
    raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    item = sub.add_parser("install")
    item.add_argument("--link", action="store_true")
    item = sub.add_parser("rollback")
    item.add_argument("--snapshot", required=True)
    item.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    try:
        result = check() if args.command == "check" else install(args.link) if args.command == "install" else rollback(args.snapshot, args.recover)
    except InstallError as exc:
        fail(str(exc))
    print(json.dumps(result, sort_keys=True))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
