import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from importlib import util
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts" / "install.py"
V020_FIXTURE = ROOT / "tests" / "fixtures" / "v0.2.0"
V021_FIXTURE = ROOT / "tests" / "fixtures" / "v0.2.1"
ROLE_NAMES = {
    "default",
    "worker",
    "explorer",
    "code_locator",
    "cross_module_architect",
    "systems_safety",
    "semantic_reviewer",
    "release_operator",
}

def canonical_root(path):
    return path.parent.resolve(strict=False) / path.name

def run(path, *args, env=None, ok=True):
    result = subprocess.run([sys.executable, str(path), *args], text=True, capture_output=True, env=env)
    if ok: assert result.returncode == 0, result.stderr
    return result

def isolated(temp):
    raw_home = Path(temp) / "home"; raw_codex = Path(temp) / "codex"
    return canonical_root(raw_home), canonical_root(raw_codex), {**os.environ, "HOME":str(raw_home), "CODEX_HOME":str(raw_codex)}

def state_bytes(home, codex):
    roots = [home / ".agents" / "skills" / "govern-agent-system", codex]
    result = {}
    for root in roots:
        if root.is_symlink():
            result[str(root)] = ("symlink", os.readlink(root))
        elif root.exists():
            for path in sorted(root.rglob("*")):
                relative = str(path.relative_to(root))
                parts = path.relative_to(root).parts
                if root == codex and len(parts) >= 2 and parts[0] == "agent-system" and parts[1] in {"install.lock", "rollback-journal.json", "snapshots"}:
                    continue
                if "__pycache__" in parts:
                    continue
                if path.is_symlink(): result[f"{root}:{relative}"] = ("symlink", os.readlink(path))
                elif path.is_file(): result[f"{root}:{relative}"] = ("file", path.read_bytes())
    return result

def private_mode(path, mode):
    return stat.S_IMODE(path.lstat().st_mode) == mode

def mode_byte_state(root):
    result = {}
    if not root.exists() and not root.is_symlink():
        return result
    for path in [root, *sorted(root.rglob("*"))]:
        relative = "." if path == root else path.relative_to(root).as_posix()
        info = path.lstat()
        if path.is_symlink():
            payload = ("symlink", os.readlink(path))
        elif path.is_file():
            payload = ("file", path.read_bytes())
        else:
            payload = ("directory", None)
        result[relative] = (stat.S_IMODE(info.st_mode), payload)
    return result

def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()

def seed_managed_v02(home, codex, fixture, expected_revision, config_bytes):
    release = json.loads((fixture / "release.json").read_text(encoding="utf-8"))
    version = release["installer_version"]
    if release["source_revision"] != expected_revision:
        raise AssertionError(f"v{version} fixture revision drift")

    skill_raw = (fixture / "SKILL.md").read_bytes()
    if sha256_bytes(skill_raw) != release["skill"]["file_sha256"]:
        raise AssertionError(f"v{version} Skill fixture hash mismatch")
    skill_tree_sha256 = sha256_bytes(b"F\0SKILL.md\0" + hashlib.sha256(skill_raw).digest())
    if skill_tree_sha256 != release["skill"]["tree_sha256"]:
        raise AssertionError(f"v{version} Skill tree hash mismatch")

    fixture_agents = fixture / "agents"
    actual_roles = {path.stem for path in fixture_agents.glob("*.toml")}
    if actual_roles != set(release["adapters"]):
        raise AssertionError(f"v{version} adapter fixture inventory mismatch")
    skill = home / ".agents/skills/govern-agent-system"
    agents = codex / "agents"
    state = codex / "agent-system"
    skill.mkdir(parents=True)
    agents.mkdir(parents=True)
    state.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(skill_raw)

    adapter_records = {}
    for name, expected_sha256 in release["adapters"].items():
        raw = (fixture_agents / f"{name}.toml").read_bytes()
        if sha256_bytes(raw) != expected_sha256:
            raise AssertionError(f"v{version} adapter fixture hash mismatch: {name}")
        destination = agents / f"{name}.toml"
        destination.write_bytes(raw)
        adapter_records[name] = {"path": str(destination), "sha256": expected_sha256}

    managed = release["config"]["managed"]
    managed_sha256 = sha256_bytes(json.dumps(managed, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if managed_sha256 != release["config"]["managed_sha256"]:
        raise AssertionError(f"v{version} managed config fixture hash mismatch")
    parsed_config = tomllib.loads(config_bytes.decode("utf-8"))
    if not isinstance(parsed_config.get("agents"), dict) or any(parsed_config["agents"].get(key) != value for key, value in managed.items()):
        raise AssertionError(f"v{version} fixture config lacks managed values")
    config = codex / "config.toml"
    config.write_bytes(config_bytes)

    manifest_path = state / "managed-install.json"
    manifest = {
        "schema_version": release["schema_version"],
        "identity": release["identity"],
        "installer_version": release["installer_version"],
        "destinations": {
            "agents": str(agents),
            "config": str(config),
            "manifest": str(manifest_path),
            "skill": str(skill),
        },
        "link": False,
        "skill": {"kind": "directory", "content_sha256": skill_tree_sha256, "target": None},
        "adapters": adapter_records,
        "config": {"path": str(config), "managed": managed, "managed_sha256": managed_sha256},
    }
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    snapshot = state / "snapshots" / release["snapshot_name"]
    snapshot.mkdir(parents=True)
    entries = [
        {"label": "skill", "path": str(skill), "kind": "missing", "sha256": None, "target": None},
        {"label": "config", "path": str(config), "kind": "missing", "sha256": None, "target": None},
        {"label": "managed-manifest", "path": str(manifest_path), "kind": "missing", "sha256": None, "target": None},
        *[
            {"label": f"agent-{name}", "path": str(agents / f"{name}.toml"), "kind": "missing", "sha256": None, "target": None}
            for name in sorted(release["adapters"])
        ],
    ]
    snapshot_manifest = {
        "schema_version": 2,
        "identity": release["identity"],
        "installer_version": release["installer_version"],
        "purpose": "install",
        "entries": entries,
    }
    (snapshot / "manifest.json").write_text(json.dumps(snapshot_manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    for directory in [skill.parent.parent, skill.parent, skill, codex, agents, state, snapshot.parent, snapshot]:
        directory.chmod(0o700)
    for path in [skill / "SKILL.md", *agents.glob("*.toml"), config, manifest_path, snapshot / "manifest.json"]:
        path.chmod(0o600)
    return snapshot

def seed_managed_v020(home, codex, config_bytes=b"[agents]\nmax_threads = 4\nmax_depth = 1\n"):
    return seed_managed_v02(
        home,
        codex,
        V020_FIXTURE,
        "def07224be678695090359b355e40f033f419041",
        config_bytes,
    )

def seed_managed_v021(home, codex, config_bytes=b"[agents]\nmax_threads = 4\nmax_depth = 1\n"):
    return seed_managed_v02(
        home,
        codex,
        V021_FIXTURE,
        "c9ea1cd979367ae218580fd52566716753cd5800",
        config_bytes,
    )

class GovernanceTests(unittest.TestCase):
    def test_snapshot_sync_fences_journal_and_destination_promotion(self):
        spec = util.spec_from_file_location("governance_installer_snapshot_sync_test", INSTALL)
        installer = util.module_from_spec(spec); sys.path.insert(0, str(INSTALL.parent))
        try:
            spec.loader.exec_module(installer)
        finally:
            sys.path.pop(0)

        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp)
            codex.mkdir()
            config = codex / "config.toml"
            config.write_bytes(b"[other]\nkeep = true\n")
            before = state_bytes(home, codex)
            snapshot_root = codex / "agent-system" / "snapshots"

            def fail_snapshot_sync(path):
                if path.parent == snapshot_root and path.name.startswith("snapshot-"):
                    raise installer.InstallError("injected snapshot sync failure")
                return real_fsync_directory(path)

            real_fsync_directory = installer.fsync_directory
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(installer, "fsync_directory", side_effect=fail_snapshot_sync):
                with self.assertRaisesRegex(installer.InstallError, "injected snapshot sync failure"):
                    installer.install()

            self.assertEqual(state_bytes(home, codex), before)
            self.assertFalse((codex / "agent-system" / "rollback-journal.json").exists())
            self.assertEqual(config.read_bytes(), b"[other]\nkeep = true\n")
            self.assertFalse((home / ".agents" / "skills" / "govern-agent-system").exists())

        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp)
            codex.mkdir()
            (codex / "config.toml").write_bytes(b"[other]\nkeep = true\n")
            p = {
                "state": codex / "agent-system",
                "snapshots": codex / "agent-system" / "snapshots",
                "skill": home / ".agents" / "skills" / "govern-agent-system",
                "config": codex / "config.toml",
                "manifest": codex / "agent-system" / "managed-install.json",
                "agents": codex / "agents",
            }
            synced_directories = []
            synced_files = []
            promoted = []
            real_fsync_directory = installer.fsync_directory
            real_fsync_file = installer.fsync_file
            real_replace = installer.os.replace
            destinations = {
                p["skill"], p["config"], p["manifest"],
                *(p["agents"] / f"{name}.toml" for name in ROLE_NAMES),
            }

            def record_sync(path):
                synced_directories.append(path)
                return real_fsync_directory(path)

            def record_file_sync(path):
                synced_files.append(path)
                return real_fsync_file(path)

            def require_snapshot_sync(source, destination):
                if Path(destination) in destinations:
                    snapshot_files = {
                        path
                        for snapshot in p["snapshots"].glob("snapshot-*")
                        for path in snapshot.rglob("*")
                        if path.is_file()
                    }
                    self.assertTrue(snapshot_files)
                    self.assertTrue(snapshot_files.issubset(synced_files))
                    self.assertIn(p["snapshots"], synced_directories)
                    self.assertIn(p["state"], synced_directories)
                    promoted.append(Path(destination))
                return real_replace(source, destination)

            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(installer, "fsync_directory", side_effect=record_sync), \
                 mock.patch.object(installer, "fsync_file", side_effect=record_file_sync), \
                 mock.patch.object(installer.os, "replace", side_effect=require_snapshot_sync):
                self.assertTrue(installer.install()["ok"])

            self.assertEqual(set(promoted), destinations)

    def test_installer_collision_update_and_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"; codex = Path(temp) / "codex"; target = home / ".agents" / "skills" / "govern-agent-system"; target.mkdir(parents=True); (target / "foreign").write_text("x")
            env = {**os.environ, "HOME":str(home), "CODEX_HOME":str(codex)}
            self.assertNotEqual(run(INSTALL, "install", env=env, ok=False).returncode, 0)
            import shutil; shutil.rmtree(target)
            result = json.loads(run(INSTALL, "install", env=env).stdout); self.assertTrue(target.is_dir())
            run(INSTALL, "install", env=env); run(INSTALL, "rollback", "--snapshot", result["snapshot"], env=env)
            self.assertFalse(target.exists())
    def test_installer_failure_recovers_byte_state_and_lock_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"; codex = Path(temp) / "codex"; agents = codex / "agents"; agents.mkdir(parents=True)
            config = codex / "config.toml"; config.write_bytes(b"[other]\nkeep = true\n")
            env = {**os.environ, "HOME":str(home), "CODEX_HOME":str(codex), "CAG_FAIL_AFTER_SKILL":"1"}
            failed = run(INSTALL, "install", env=env, ok=False)
            report = json.loads(failed.stdout); self.assertFalse(report["ok"]); self.assertTrue(report["recovery"]); self.assertTrue(Path(report["snapshot"]).is_dir())
            self.assertEqual(config.read_bytes(), b"[other]\nkeep = true\n"); self.assertEqual(list(agents.iterdir()), [])
            self.assertFalse((home / ".agents" / "skills" / "govern-agent-system").exists())
            lock = codex / "agent-system" / "install.lock"; lock.parent.mkdir(parents=True, exist_ok=True); lock.write_text(f"{os.getpid()}\n")
            before = config.read_bytes(); locked = run(INSTALL, "install", env={k:v for k,v in env.items() if k != "CAG_FAIL_AFTER_SKILL"}, ok=False)
            self.assertIn("INSTALL_LOCKED", locked.stderr); self.assertEqual(before, config.read_bytes())
            lock.unlink(); success = json.loads(run(INSTALL, "install", env={k:v for k,v in env.items() if k != "CAG_FAIL_AFTER_SKILL"}).stdout)
            self.assertTrue(success["ok"]); self.assertFalse(lock.exists()); self.assertTrue(json.loads(run(INSTALL, "install", env={k:v for k,v in env.items() if k != "CAG_FAIL_AFTER_SKILL"}).stdout)["ok"])
    def test_installer_rejects_forged_ownership_and_unsafe_links(self):
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp); target = home / ".agents/skills/govern-agent-system"
            target.mkdir(parents=True); (target / ".codex-agent-governance-install.json").write_text('{"managed":true}\n'); (target / "foreign").write_text("keep")
            self.assertNotEqual(run(INSTALL, "install", env=env, ok=False).returncode, 0); self.assertEqual((target / "foreign").read_text(), "keep")
            shutil.rmtree(target); outside = Path(temp) / "outside"; outside.mkdir()
            try:
                target.symlink_to(outside / "missing-skill", target_is_directory=True)
                self.assertNotEqual(run(INSTALL, "install", env=env, ok=False).returncode, 0)
                target.unlink()
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            agents = codex / "agents"; agents.mkdir(parents=True)
            forged = agents / "worker.toml"; forged.write_text("# Generated by govern-agent-system; do not edit manually.\nforeign\n")
            self.assertNotEqual(run(INSTALL, "install", env=env, ok=False).returncode, 0); self.assertIn("foreign", forged.read_text())
            shutil.rmtree(agents)
            codex.mkdir(parents=True, exist_ok=True)
            try:
                (codex / "config.toml").symlink_to(outside / "config.toml")
                self.assertNotEqual(run(INSTALL, "install", env=env, ok=False).returncode, 0)
                (codex / "config.toml").unlink()
                agents.mkdir(); (agents / "worker.toml").symlink_to(outside / "missing.toml")
                self.assertNotEqual(run(INSTALL, "install", env=env, ok=False).returncode, 0)
                shutil.rmtree(agents); snapshots = codex / "agent-system/snapshots"; snapshots.parent.mkdir(parents=True, exist_ok=True); snapshots.symlink_to(outside, target_is_directory=True)
                self.assertNotEqual(run(INSTALL, "install", env=env, ok=False).returncode, 0); self.assertEqual(list(outside.iterdir()), [])
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp); manifest = codex / "agent-system/managed-install.json"; manifest.parent.mkdir(parents=True)
            manifest.write_text('{"schema_version":1,"identity":"govern-agent-system"}\n')
            before = manifest.read_bytes(); self.assertNotEqual(run(INSTALL, "install", env=env, ok=False).returncode, 0); self.assertEqual(manifest.read_bytes(), before)
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp); outside = Path(temp) / "outside"; outside.mkdir(); home.mkdir()
            try:
                (home / ".agents").symlink_to(outside, target_is_directory=True)
                self.assertNotEqual(run(INSTALL, "install", env=env, ok=False).returncode, 0); self.assertEqual(list(outside.iterdir()), [])
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp); json.loads(run(INSTALL, "install", env=env).stdout)
            adapter = codex / "agents/worker.toml"; adapter.write_text(adapter.read_text() + "forged = true\n")
            before = state_bytes(home, codex)
            self.assertNotEqual(run(INSTALL, "install", env=env, ok=False).returncode, 0)
            self.assertEqual(state_bytes(home, codex), before)
    def test_rollback_lock_recovery_and_concurrent_install(self):
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp); codex.mkdir(); config = codex / "config.toml"; config.write_text('[other]\nkeep = true\n')
            first = json.loads(run(INSTALL, "install", env=env).stdout); snapshot = first["snapshot"]; installed = state_bytes(home, codex)
            lock = codex / "agent-system/install.lock"; lock.write_text("held\n")
            blocked = run(INSTALL, "rollback", "--snapshot", snapshot, env=env, ok=False)
            self.assertIn("INSTALL_LOCKED", blocked.stderr); self.assertEqual(state_bytes(home, codex), installed)
            lock.unlink()
            failed_env = {**env, "CAG_FAIL_ROLLBACK_AFTER":"1"}
            failed = run(INSTALL, "rollback", "--snapshot", snapshot, env=failed_env, ok=False)
            report = json.loads(failed.stdout); self.assertFalse(report["ok"]); self.assertTrue(report["recovery"]); self.assertEqual(state_bytes(home, codex), installed)
            hold_env = {**env, "CAG_HOLD_LOCK_SECONDS":"1"}
            process = subprocess.Popen([sys.executable, str(INSTALL), "install"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=hold_env)
            for _ in range(100):
                if lock.exists(): break
                time.sleep(0.01)
            concurrent = run(INSTALL, "rollback", "--snapshot", snapshot, env=env, ok=False)
            self.assertIn("INSTALL_LOCKED", concurrent.stderr); stdout, stderr = process.communicate(timeout=10); self.assertEqual(process.returncode, 0, stderr)
            run(INSTALL, "rollback", "--snapshot", snapshot, env=env); self.assertEqual(config.read_text(), '[other]\nkeep = true\n')
    def test_recovery_failure_fences_until_explicit_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp)
            installed = json.loads(run(INSTALL, "install", env=env).stdout)
            adapter = codex / "agents/worker.toml"; good_adapter = adapter.read_bytes()
            failed = run(INSTALL, "rollback", "--snapshot", installed["snapshot"], env={**env, "CAG_FAIL_ROLLBACK_AFTER":"1", "CAG_FAIL_RECOVERY":"1"}, ok=False)
            report = json.loads(failed.stdout); self.assertFalse(report["recovery"]); self.assertFalse(report["committed"])
            journal = codex / "agent-system/rollback-journal.json"
            original_journal = journal.read_bytes(); self.assertEqual(json.loads(original_journal)["status"], "recovery_failed")
            adapter.write_bytes(b"corrupted pre-retry adapter\n")
            before = state_bytes(home, codex)
            rejected = run(INSTALL, "install", env=env, ok=False)
            self.assertIn("RECOVERY_REQUIRED", rejected.stderr); self.assertFalse((codex / "agent-system/install.lock").exists()); self.assertEqual(state_bytes(home, codex), before)
            ordinary = run(INSTALL, "rollback", "--snapshot", report["recovery_snapshot"], env=env, ok=False)
            self.assertIn("RECOVERY_REQUIRED", ordinary.stderr)
            retry = run(INSTALL, "rollback", "--recover", "--snapshot", report["recovery_snapshot"], env={**env, "CAG_FAIL_ROLLBACK_AFTER":"1"}, ok=False)
            retry_report = json.loads(retry.stdout)
            retry_journal = json.loads(journal.read_text(encoding="utf-8"))
            self.assertFalse(retry_report["recovery"])
            self.assertEqual(retry_journal["status"], "recovery_failed")
            self.assertEqual(retry_journal["recovery_snapshot"], report["recovery_snapshot"])
            self.assertIn("artifacts", retry_journal)
            self.assertNotEqual(journal.read_bytes(), original_journal)
            self.assertEqual(adapter.read_bytes(), b"corrupted pre-retry adapter\n")
            recovered = json.loads(run(INSTALL, "rollback", "--recover", "--snapshot", report["recovery_snapshot"], env=env).stdout)
            self.assertTrue(recovered["ok"]); self.assertFalse(journal.exists()); self.assertEqual(adapter.read_bytes(), good_adapter); self.assertTrue(json.loads(run(INSTALL, "install", env=env).stdout)["ok"])
    def test_installer_rechecks_recovery_fence_under_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp); installed = json.loads(run(INSTALL, "install", env=env).stdout)
            before = state_bytes(home, codex); lock = codex / "agent-system/install.lock"
            process = subprocess.Popen([sys.executable, str(INSTALL), "install"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**env, "CAG_HOLD_LOCK_SECONDS":"1"})
            for _ in range(100):
                if lock.exists(): break
                time.sleep(0.01)
            self.assertTrue(lock.exists())
            journal = codex / "agent-system/rollback-journal.json"
            journal.write_text(json.dumps({"schema_version":1,"identity":"govern-agent-system","operation":"rollback","status":"recovery_failed","recovery_snapshot":installed["snapshot"],"destinations":[],"error":"injected race"}) + "\n")
            stdout, stderr = process.communicate(timeout=10)
            self.assertNotEqual(process.returncode, 0); self.assertIn("RECOVERY_REQUIRED", stderr); self.assertEqual(state_bytes(home, codex), before)
    def test_trusted_root_alias_acceptance_and_managed_link_rejection(self):
        install_spec = util.spec_from_file_location("governance_installer_link_test", INSTALL)
        installer = util.module_from_spec(install_spec); sys.path.insert(0, str(INSTALL.parent))
        try:
            install_spec.loader.exec_module(installer)
        finally:
            sys.path.pop(0)
        requested = Path("configured-home"); fallback = Path("platform-home")
        self.assertEqual(installer.configured_home({"HOME":str(requested), "USERPROFILE":str(fallback)}, fallback), requested)
        self.assertEqual(installer.configured_home({"USERPROFILE":str(requested)}, fallback), fallback)
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"; skill = home / ".agents/skills/govern-agent-system"
            source = Path(temp) / "source"; source.mkdir(); skill.parent.mkdir(parents=True); skill.symlink_to(source, target_is_directory=True)
            self.assertEqual(installer.normalize_windows_link_target(r"\\?\C:/Users/RUNNER~1/AppData/Release"), r"c:\users\runner~1\appdata\release")
            self.assertEqual(installer.normalize_windows_link_target(r"\\?\UNC\Server\Share\Release"), r"\\server\share\release")
            self.assertEqual(installer.normalize_windows_link_target(r"\??\C:\Release\Skill"), r"\??\c:\release\skill")
            with self.assertRaises(installer.InstallError):
                installer.validate_chain(skill, home)
            skill.unlink()
            fake_reparse = object()
            with mock.patch.object(installer, "lstat_or_none", side_effect=lambda path: fake_reparse if path == skill else None), \
                 mock.patch.object(installer, "is_link_or_reparse", side_effect=lambda info: info is fake_reparse), \
                 mock.patch.object(installer.Path, "is_symlink", return_value=False):
                with self.assertRaises(installer.InstallError):
                    installer.validate_chain(skill, home)
        with tempfile.TemporaryDirectory() as temp:
            actual = Path(temp) / "actual"; actual.mkdir(); alias = Path(temp) / "alias"
            try:
                alias.symlink_to(actual, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            raw_home = actual / "home"; raw_home.mkdir(); home = canonical_root(raw_home)
            env = {**os.environ, "HOME":str(alias / "home"), "CODEX_HOME":str(alias / "codex")}
            result = json.loads(run(INSTALL, "install", env=env).stdout)
            self.assertEqual(Path(result["installed"]), home / ".agents/skills/govern-agent-system")
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp); outside = Path(temp) / "outside"; outside.mkdir(); home.mkdir()
            try:
                (home / ".agents").symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            self.assertNotEqual(run(INSTALL, "install", env=env, ok=False).returncode, 0); self.assertEqual(list(outside.iterdir()), [])
    def test_pinned_v020_update_replaces_roles_and_rolls_back_exactly(self):
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp)
            old_snapshot = seed_managed_v020(home, codex)
            old_snapshot_state = mode_byte_state(old_snapshot)
            before = state_bytes(home, codex)
            installed_agent = codex / "agents/default.toml"
            before_agent = installed_agent.read_bytes()
            updated = json.loads(run(INSTALL, "install", env=env).stdout)
            target = home / ".agents/skills/govern-agent-system"
            manifest = json.loads((codex / "agent-system/managed-install.json").read_text())
            installed_runtime = tomllib.loads(installed_agent.read_text())
            self.assertTrue(updated["ok"])
            self.assertEqual(manifest["installer_version"], "0.2.3")
            self.assertEqual(manifest["config"]["managed"], {"max_threads": 6, "max_depth": 1})
            self.assertEqual(tomllib.loads((codex / "config.toml").read_text())["agents"], {"max_threads": 6, "max_depth": 1})
            self.assertIn("Use one child by default.", (target / "SKILL.md").read_text(encoding="utf-8"))
            self.assertEqual(installed_runtime["model"], "gpt-5.6-luna")
            self.assertEqual(installed_runtime["model_reasoning_effort"], "high")
            self.assertNotEqual(installed_agent.read_bytes(), before_agent)
            self.assertEqual(mode_byte_state(old_snapshot), old_snapshot_state)
            self.assertTrue(json.loads(run(INSTALL, "rollback", "--snapshot", updated["snapshot"], env=env).stdout)["ok"])
            self.assertEqual(installed_agent.read_bytes(), before_agent)
            self.assertEqual(state_bytes(home, codex), before)
            self.assertEqual(mode_byte_state(old_snapshot), old_snapshot_state)
    def test_pinned_v021_update_replaces_roles_and_rolls_back_exactly(self):
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp)
            old_snapshot = seed_managed_v021(home, codex)
            old_snapshot_state = mode_byte_state(old_snapshot)
            before = state_bytes(home, codex)
            installed_agent = codex / "agents/default.toml"
            before_agent = installed_agent.read_bytes()
            updated = json.loads(run(INSTALL, "install", env=env).stdout)
            manifest = json.loads((codex / "agent-system/managed-install.json").read_text())
            installed_runtime = tomllib.loads(installed_agent.read_text())
            self.assertTrue(updated["ok"])
            self.assertEqual(manifest["installer_version"], "0.2.3")
            self.assertEqual(manifest["config"]["managed"], {"max_threads": 6, "max_depth": 1})
            self.assertEqual(tomllib.loads((codex / "config.toml").read_text())["agents"], {"max_threads": 6, "max_depth": 1})
            self.assertEqual(installed_runtime["model"], "gpt-5.6-luna")
            self.assertEqual(installed_runtime["model_reasoning_effort"], "high")
            self.assertNotEqual(installed_agent.read_bytes(), before_agent)
            self.assertEqual(mode_byte_state(old_snapshot), old_snapshot_state)
            self.assertTrue(json.loads(run(INSTALL, "rollback", "--snapshot", updated["snapshot"], env=env).stdout)["ok"])
            self.assertEqual(installed_agent.read_bytes(), before_agent)
            self.assertEqual(state_bytes(home, codex), before)
            self.assertEqual(mode_byte_state(old_snapshot), old_snapshot_state)
    def test_higher_version_same_format_updates_without_version_gate(self):
        def seed_future_managed_state(
            home,
            codex,
            config_text="[agents]\nmax_threads = 8\nmax_depth = 1\n",
        ):
            seed_managed_v021(home, codex)
            config_path = codex / "config.toml"
            config_path.write_text(config_text, encoding="utf-8")
            managed = {"max_threads": 8, "max_depth": 1}
            manifest_path = codex / "agent-system/managed-install.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["installer_version"] = "999.999.999"
            manifest["config"]["managed"] = managed
            manifest["config"]["managed_sha256"] = sha256_bytes(
                json.dumps(managed, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            manifest_path.chmod(0o600)
            return manifest_path

        compatible_renderings = (
            "[agents]\nmax_threads = 8\nmax_depth = 1\n",
            '["agents"]\nmax_threads = 8\nmax_depth = 1\n',
            "[agents]\n\"max_threads\" = 8\n'max_depth' = 1\n",
        )
        for config_text in compatible_renderings:
            with self.subTest(config_text=config_text), tempfile.TemporaryDirectory() as temp:
                home, codex, env = isolated(temp)
                manifest_path = seed_future_managed_state(home, codex, config_text)
                checked = json.loads(run(INSTALL, "check", env=env).stdout)
                updated = json.loads(run(INSTALL, "install", env=env).stdout)
                installed_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertTrue(checked["ok"] and checked["managed"])
                self.assertTrue(updated["ok"])
                self.assertEqual(installed_manifest["installer_version"], "0.2.3")
                self.assertEqual(installed_manifest["config"]["managed"], {"max_threads": 6, "max_depth": 1})
                self.assertEqual(tomllib.loads((codex / "config.toml").read_text())["agents"], {"max_threads": 6, "max_depth": 1})

        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp)
            manifest_path = seed_future_managed_state(home, codex)
            removed = json.loads(run(INSTALL, "uninstall", env=env).stdout)
            self.assertTrue(removed["ok"])
            self.assertFalse(manifest_path.exists())
            self.assertEqual(tomllib.loads((codex / "config.toml").read_text())["agents"], {})

    def test_manifest_schema_and_managed_provenance_require_exact_scalar_types(self):
        cases = (
            ("boolean schema", True, None),
            ("floating managed values", 1, {"max_threads": 4.0, "max_depth": 1.0}),
            ("boolean managed value", 1, {"max_threads": 4, "max_depth": True}),
        )
        for label, schema_version, managed_values in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                home, codex, env = isolated(temp)
                seed_managed_v021(home, codex)
                manifest_path = codex / "agent-system/managed-install.json"
                config_path = codex / "config.toml"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["schema_version"] = schema_version
                if managed_values is not None:
                    manifest["config"]["managed"] = managed_values
                    manifest["config"]["managed_sha256"] = sha256_bytes(
                        json.dumps(managed_values, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    )
                    if isinstance(managed_values["max_depth"], bool):
                        config_path.write_text("[agents]\nmax_threads = 4\nmax_depth = true\n", encoding="utf-8")
                    else:
                        config_path.write_text("[agents]\nmax_threads = 4.0\nmax_depth = 1.0\n", encoding="utf-8")
                manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                before = state_bytes(home, codex)

                checked = run(INSTALL, "check", env=env, ok=False)
                installed = run(INSTALL, "install", env=env, ok=False)

                self.assertNotEqual(checked.returncode, 0)
                self.assertIn("managed", checked.stdout + checked.stderr)
                self.assertNotEqual(installed.returncode, 0)
                self.assertEqual(state_bytes(home, codex), before)

    @unittest.skipIf(os.name == "nt", "forced KeyboardInterrupt exit codes are platform-specific")
    def test_interrupted_install_and_uninstall_are_fenced_and_exactly_recoverable(self):
        step_count = len(ROLE_NAMES) + 3
        install_boundaries = [(index, "destination") for index in range(1, step_count + 1)] + [(len(ROLE_NAMES) + 2, "backup")]
        uninstall_boundaries = [(index, "backup") for index in range(1, step_count + 1)] + [(len(ROLE_NAMES) + 2, "destination")]

        for operation, boundaries in (("install", install_boundaries), ("uninstall", uninstall_boundaries)):
            for index, phase in boundaries:
                with self.subTest(operation=operation, index=index, phase=phase), tempfile.TemporaryDirectory() as temp:
                    home, codex, env = isolated(temp)
                    codex.mkdir()
                    (codex / "config.toml").write_text("[other]\nkeep = true\n", encoding="utf-8")
                    if operation == "uninstall":
                        self.assertTrue(json.loads(run(INSTALL, "install", env=env).stdout)["ok"])
                    before = state_bytes(home, codex)
                    interrupted = run(
                        INSTALL,
                        operation,
                        env={**env, f"CAG_INTERRUPT_{operation.upper()}_AFTER": f"{index}:{phase}"},
                        ok=False,
                    )
                    self.assertNotEqual(interrupted.returncode, 0)

                    journal_path = codex / "agent-system/rollback-journal.json"
                    lock_path = codex / "agent-system/install.lock"
                    self.assertTrue(journal_path.is_file())
                    self.assertFalse(lock_path.exists())
                    journal = json.loads(journal_path.read_text(encoding="utf-8"))
                    self.assertEqual(journal["status"], "promoting")
                    self.assertIsNone(journal["error"])
                    self.assertEqual(len(journal["steps"]), step_count)

                    fenced = run(INSTALL, "install", env=env, ok=False)
                    self.assertIn("RECOVERY_REQUIRED", fenced.stderr)
                    recovered = json.loads(
                        run(
                            INSTALL,
                            "rollback",
                            "--recover",
                            "--snapshot",
                            journal["recovery_snapshot"],
                            env=env,
                        ).stdout
                    )
                    self.assertTrue(recovered["ok"])
                    self.assertEqual(state_bytes(home, codex), before)
                    self.assertFalse(journal_path.exists())
                    debris = [
                        path
                        for root in (home, codex)
                        if root.exists()
                        for path in root.rglob("*")
                        if ".backup-" in path.name or path.name.startswith(".govern-agent-system.")
                    ]
                    self.assertEqual(debris, [])

    def test_process_death_leaves_a_durable_recoverable_transaction(self):
        cases = (("install", "1:destination"), ("uninstall", "1:backup"))
        for operation, boundary in cases:
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as temp:
                home, codex, env = isolated(temp)
                codex.mkdir()
                (codex / "config.toml").write_text("[other]\nkeep = true\n", encoding="utf-8")
                if operation == "uninstall":
                    self.assertTrue(json.loads(run(INSTALL, "install", env=env).stdout)["ok"])
                before = state_bytes(home, codex)

                terminated = run(
                    INSTALL,
                    operation,
                    env={**env, f"CAG_TERMINATE_{operation.upper()}_AFTER": boundary},
                    ok=False,
                )
                self.assertEqual(terminated.returncode, 86)
                journal_path = codex / "agent-system/rollback-journal.json"
                lock_path = codex / "agent-system/install.lock"
                self.assertTrue(journal_path.is_file())
                self.assertTrue(lock_path.is_file())
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
                self.assertEqual(journal["status"], "promoting")
                self.assertIsNone(journal["error"])

                fenced = run(INSTALL, "install", env=env, ok=False)
                self.assertIn("RECOVERY_REQUIRED", fenced.stderr)
                recovered = json.loads(
                    run(
                        INSTALL,
                        "rollback",
                        "--recover",
                        "--snapshot",
                        journal["recovery_snapshot"],
                        env=env,
                    ).stdout
                )
                self.assertTrue(recovered["ok"])
                self.assertEqual(state_bytes(home, codex), before)
                self.assertFalse(journal_path.exists())
                self.assertFalse(lock_path.exists())
                debris = [
                    path
                    for root in (home, codex)
                    if root.exists()
                    for path in root.rglob("*")
                    if ".backup-" in path.name or path.name.startswith(".govern-agent-system.")
                ]
                self.assertEqual(debris, [])

    def test_dead_owner_lock_without_a_journal_is_reclaimed_at_transaction_edges(self):
        cases = (
            ("before journal", "CAG_TERMINATE_INSTALL_BEFORE_JOURNAL", 87, False),
            ("after journal close", "CAG_TERMINATE_INSTALL_AFTER_JOURNAL_CLOSE", 88, True),
        )
        for label, variable, exit_code, committed in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                home, codex, env = isolated(temp)
                codex.mkdir()
                config = codex / "config.toml"
                config.write_text("[other]\nkeep = true\n", encoding="utf-8")
                before = state_bytes(home, codex)

                terminated = run(INSTALL, "install", env={**env, variable: "1"}, ok=False)
                self.assertEqual(terminated.returncode, exit_code)
                lock_path = codex / "agent-system/install.lock"
                journal_path = codex / "agent-system/rollback-journal.json"
                self.assertTrue(lock_path.is_file())
                self.assertFalse(journal_path.exists())
                if committed:
                    self.assertNotEqual(state_bytes(home, codex), before)
                    self.assertTrue(json.loads(run(INSTALL, "check", env=env).stdout)["managed"])
                else:
                    self.assertEqual(config.read_text(encoding="utf-8"), "[other]\nkeep = true\n")
                    self.assertFalse((home / ".agents/skills/govern-agent-system").exists())
                    self.assertFalse((codex / "agent-system/managed-install.json").exists())

                resumed = json.loads(run(INSTALL, "install", env=env).stdout)
                self.assertTrue(resumed["ok"])
                self.assertFalse(lock_path.exists())
                self.assertFalse(journal_path.exists())
                self.assertTrue(json.loads(run(INSTALL, "check", env=env).stdout)["managed"])
                debris = [
                    path
                    for root in (home, codex)
                    if root.exists()
                    for path in root.rglob("*")
                    if path.name.startswith(".govern-agent-system.")
                ]
                self.assertEqual(debris, [])

    def test_uninstall_preserves_user_state_and_rollback_restores_managed_state(self):
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp)
            codex.mkdir()
            config = codex / "config.toml"
            config.write_bytes(
                b'top = "keep"\n\n[agents]\nfuture_key = "keep"\n\n[mcp]\nendpoint = "keep"\n'
            )
            installed = json.loads(run(INSTALL, "install", env=env).stdout)
            user_agent = codex / "agents/user-owned-agent.toml"
            user_agent.write_text('name = "user-owned"\n', encoding="utf-8")
            before_uninstall = state_bytes(home, codex)

            removed = json.loads(run(INSTALL, "uninstall", env=env).stdout)
            snapshot = Path(removed["snapshot"])
            snapshot_manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
            parsed = tomllib.loads(config.read_text(encoding="utf-8"))

            self.assertTrue(installed["ok"] and removed["ok"])
            self.assertEqual(snapshot_manifest["purpose"], "uninstall")
            self.assertFalse((home / ".agents/skills/govern-agent-system").exists())
            self.assertFalse((codex / "agent-system/managed-install.json").exists())
            self.assertFalse(any((codex / "agents" / f"{name}.toml").exists() for name in ROLE_NAMES))
            self.assertEqual(user_agent.read_text(encoding="utf-8"), 'name = "user-owned"\n')
            self.assertEqual(parsed["agents"], {"future_key": "keep"})
            self.assertEqual(parsed["mcp"], {"endpoint": "keep"})
            checked = json.loads(run(INSTALL, "check", env=env).stdout)
            self.assertTrue(checked["ok"])
            self.assertFalse(checked["managed"])

            restored = json.loads(run(INSTALL, "rollback", "--snapshot", removed["snapshot"], env=env).stdout)
            self.assertTrue(restored["ok"])
            self.assertEqual(state_bytes(home, codex), before_uninstall)

    def test_uninstall_failure_recovers_and_unmanaged_uninstall_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp)
            codex.mkdir()
            (codex / "config.toml").write_text('[other]\nkeep = true\n', encoding="utf-8")
            before_unmanaged = state_bytes(home, codex)
            rejected = run(INSTALL, "uninstall", env=env, ok=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("NOT_MANAGED", rejected.stdout + rejected.stderr)
            self.assertEqual(state_bytes(home, codex), before_unmanaged)

        for failure_index in range(1, len(ROLE_NAMES) + 4):
            with self.subTest(failure_index=failure_index), tempfile.TemporaryDirectory() as temp:
                home, codex, env = isolated(temp)
                self.assertTrue(json.loads(run(INSTALL, "install", env=env).stdout)["ok"])
                before_failure = state_bytes(home, codex)
                failed_env = {**env, "CAG_FAIL_UNINSTALL_AFTER": str(failure_index)}
                failed = run(INSTALL, "uninstall", env=failed_env, ok=False)
                result = json.loads(failed.stdout)
                self.assertFalse(result["ok"])
                self.assertTrue(result["recovery"])
                self.assertEqual(state_bytes(home, codex), before_failure)
    def test_pinned_v020_update_preserves_unknown_config_and_mcp(self):
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp)
            seed_managed_v020(
                home,
                codex,
                b'top = "preserved"\n\n[agents]\nfuture_key = "preserved"\n\nmax_threads = 4\nmax_depth = 1\n[mcp]\nendpoint = "preserved"\n',
            )
            config = codex / "config.toml"
            mcp_config = codex / "mcp.toml"; before = b"[mcp]\nendpoint = 'unchanged'\n"; mcp_config.write_bytes(before)
            installed_config = config.read_bytes()
            installed_state = state_bytes(home, codex)
            updated = json.loads(run(INSTALL, "install", env=env).stdout)
            self.assertEqual(
                tomllib.loads(config.read_text(encoding="utf-8"))["agents"],
                {"future_key": "preserved", "max_threads": 6, "max_depth": 1},
            )
            restored = json.loads(run(INSTALL, "rollback", "--snapshot", updated["snapshot"], env=env).stdout)
            self.assertTrue(updated["ok"] and restored["ok"])
            self.assertFalse(updated["mcp_touched"]); self.assertFalse(restored["mcp_touched"])
            self.assertEqual(config.read_bytes(), installed_config)
            self.assertEqual(mcp_config.read_bytes(), before)
            self.assertEqual(state_bytes(home, codex), installed_state)
    @unittest.skipIf(os.name == "nt", "POSIX modes are not Windows ACL guarantees")
    def test_snapshot_permissions_diagnose_and_remediate_existing_state(self):
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp); codex.mkdir(mode=0o755)
            config = codex / "config.toml"; baseline = b"[other]\nkeep = true\n"; config.write_bytes(baseline); config.chmod(0o644)
            mcp_config = codex / "mcp.toml"; mcp_before = b"[mcp]\nendpoint = 'unchanged'\n"; mcp_config.write_bytes(mcp_before)
            first = json.loads(run(INSTALL, "install", env=env).stdout)
            state = codex / "agent-system"; snapshots = state / "snapshots"; existing = Path(first["snapshot"])
            for path in [state, snapshots, existing, *existing.rglob("*")]:
                if path.is_dir(): path.chmod(0o755)
                elif path.is_file(): path.chmod(0o644)
            before_modes = {str(path): stat.S_IMODE(path.lstat().st_mode) for path in [state, snapshots, existing, *existing.rglob("*")]}
            diagnosis = json.loads(run(INSTALL, "check", env=env).stdout)
            self.assertTrue(diagnosis["permission_problems"]); self.assertEqual(before_modes, {str(path): stat.S_IMODE(path.lstat().st_mode) for path in [state, snapshots, existing, *existing.rglob("*")]})
            self.assertTrue(json.loads(run(INSTALL, "install", env=env).stdout)["ok"])
            checked = json.loads(run(INSTALL, "check", env=env).stdout); self.assertEqual(checked["permission_problems"], [])
            for path in [state, snapshots, *snapshots.rglob("*")]:
                if path.is_dir(): self.assertTrue(private_mode(path, 0o700), path)
                elif path.is_file(): self.assertTrue(private_mode(path, 0o600), path)
            self.assertEqual(mcp_config.read_bytes(), mcp_before)
            self.assertTrue(json.loads(run(INSTALL, "rollback", "--snapshot", first["snapshot"], env=env).stdout)["ok"])
            self.assertEqual(config.read_bytes(), baseline); self.assertEqual(mcp_config.read_bytes(), mcp_before)
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp); first = json.loads(run(INSTALL, "install", env=env).stdout)
            snapshot = Path(first["snapshot"]); outside = Path(temp) / "outside-manifest"; outside.write_text("foreign\n")
            manifest = snapshot / "manifest.json"; manifest.unlink(); manifest.symlink_to(outside)
            diagnosis = json.loads(run(INSTALL, "check", env=env).stdout)
            self.assertTrue(any(problem["reason"] == "unsafe_link_or_reparse" for problem in diagnosis["permission_problems"]))
            before = state_bytes(home, codex); blocked = run(INSTALL, "install", env=env, ok=False)
            self.assertIn("invalid snapshot manifest path", blocked.stderr); self.assertEqual(state_bytes(home, codex), before); self.assertEqual(outside.read_text(), "foreign\n")
    @unittest.skipIf(os.name == "nt", "POSIX modes are not Windows ACL guarantees")
    def test_inert_ledger_update_and_lock_failure_permission_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp); json.loads(run(INSTALL, "install", env=env).stdout)
            state = codex / "agent-system"; ledger = state / "ledger.jsonl"
            ledger_bytes = b'{"compact":"event"}\n'; ledger.write_bytes(ledger_bytes)
            for path in [codex / "config.toml", state, state / "snapshots", ledger, *state.rglob("snapshot-*")]:
                if path.is_dir(): path.chmod(0o755)
                elif path.is_file(): path.chmod(0o644)
            stale = state / "install.lock"; stale.write_bytes(b"stale-lock-bytes\n"); stale.chmod(0o644)
            before = mode_byte_state(codex)
            blocked = run(INSTALL, "install", env=env, ok=False)
            self.assertIn("INSTALL_LOCKED", blocked.stderr)
            self.assertEqual(mode_byte_state(codex), before)
            stale.unlink()
            self.assertTrue(json.loads(run(INSTALL, "install", env=env).stdout)["ok"])
            self.assertEqual(ledger.read_bytes(), ledger_bytes)
            self.assertTrue(private_mode(ledger, 0o600))
            self.assertEqual(json.loads(run(INSTALL, "check", env=env).stdout)["permission_problems"], [])
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp); json.loads(run(INSTALL, "install", env=env).stdout)
            state = codex / "agent-system"; lock = state / "install.lock"
            holder_code = (
                "import sys,time; from pathlib import Path; "
                f"sys.path.insert(0,{str(INSTALL.parent)!r}); import managed_lock; "
                "held=managed_lock.acquire(Path(sys.argv[1]),Path(sys.argv[2])); "
                "print('ready',flush=True); time.sleep(2); managed_lock.release(held)"
            )
            holder = subprocess.Popen([sys.executable, "-c", holder_code, str(lock), str(codex)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            self.assertEqual(holder.stdout.readline().strip(), "ready")
            for path in [codex / "config.toml", state, state / "snapshots", *state.rglob("snapshot-*")]:
                if path.is_dir(): path.chmod(0o755)
                elif path.is_file(): path.chmod(0o644)
            before = mode_byte_state(codex)
            blocked = run(INSTALL, "install", env=env, ok=False)
            self.assertIn("INSTALL_LOCKED", blocked.stderr)
            self.assertEqual(mode_byte_state(codex), before)
            _, stderr = holder.communicate(timeout=10)
            self.assertEqual(holder.returncode, 0, stderr)
    @unittest.skipIf(os.name == "nt", "POSIX descriptor and hard-link guarantees")
    def test_managed_permission_state_rejects_unknown_links_and_hard_links(self):
        for kind in ("unknown", "ledger-symlink", "ledger-hardlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                home, codex, env = isolated(temp); json.loads(run(INSTALL, "install", env=env).stdout)
                state = codex / "agent-system"; outside = Path(temp) / "outside"
                outside.write_bytes(b"outside-bytes\n")
                if kind == "unknown":
                    (state / "foreign.bin").write_bytes(b"foreign\n")
                elif kind == "ledger-symlink":
                    (state / "ledger.jsonl").symlink_to(outside)
                else:
                    os.link(outside, state / "ledger.jsonl")
                state.chmod(0o755)
                before_state, before_outside = mode_byte_state(state), mode_byte_state(outside)
                blocked = run(INSTALL, "install", env=env, ok=False)
                self.assertNotEqual(blocked.returncode, 0)
                self.assertEqual(mode_byte_state(state), before_state)
                self.assertEqual(mode_byte_state(outside), before_outside)
    def test_windows_permission_diagnostic_is_explicit_and_non_mutating(self):
        spec = util.spec_from_file_location("governance_installer_windows_permission_test", INSTALL)
        installer = util.module_from_spec(spec); sys.path.insert(0, str(INSTALL.parent))
        try:
            spec.loader.exec_module(installer)
        finally:
            sys.path.pop(0)
        with mock.patch.object(installer.os, "name", "nt"):
            self.assertEqual(installer.private_permission_enforcement(), "not_available")
            self.assertEqual(installer.permission_problems({}), [])
    @unittest.skipIf(os.name == "nt", "POSIX descriptor-rooted mutation seam")
    def test_permission_mutation_stays_on_opened_parent_during_component_replacement(self):
        spec = util.spec_from_file_location("governance_installer_descriptor_test", INSTALL)
        installer = util.module_from_spec(spec); sys.path.insert(0, str(INSTALL.parent))
        try:
            spec.loader.exec_module(installer)
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); managed = root / "managed"; managed.mkdir()
            target = managed / "secret"; target.write_bytes(b"managed\n"); target.chmod(0o644)
            outside = root / "outside"; outside.mkdir(); outside_target = outside / "secret"
            outside_target.write_bytes(b"outside\n"); outside_target.chmod(0o644)
            original_open = installer.managed_lock.open_directory
            def swap_after_open(path, *, create=False):
                fd, created = original_open(path, create=create)
                moved = root / "managed-opened"
                managed.rename(moved); managed.symlink_to(outside, target_is_directory=True)
                return fd, created
            with mock.patch.object(installer.managed_lock, "open_directory", side_effect=swap_after_open):
                installer.restrict_path(target, 0o600, "file")
            self.assertEqual((root / "managed-opened/secret").read_bytes(), b"managed\n")
            self.assertTrue(private_mode(root / "managed-opened/secret", 0o600))
            self.assertEqual(outside_target.read_bytes(), b"outside\n")
            self.assertEqual(stat.S_IMODE(outside_target.lstat().st_mode), 0o644)
    def test_config_order_and_canonical_skill_identity(self):
        rendered = []
        for seed in ("1", "2", "3", "4"):
            with tempfile.TemporaryDirectory() as temp:
                home, codex, env = isolated(temp); config = codex / "config.toml"; config.parent.mkdir(parents=True); config.write_text('[agents]\nfuture_key = "keep"\ninterrupt_message = false\n')
                self.assertTrue(json.loads(run(INSTALL, "install", env={**env, "PYTHONHASHSEED":seed}).stdout)["ok"])
                rendered.append(config.read_bytes())
                self.assertEqual(tomllib.loads(config.read_text())["agents"], {"future_key":"keep", "interrupt_message":False, "max_depth":1, "max_threads":6})
        self.assertEqual(len(set(rendered)), 1)
        frontmatter = (ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        self.assertIn("\nname: govern-agent-system\n", "\n" + frontmatter)
        self.assertIn("Automatically govern native Codex custom-agent delegation", frontmatter)
        self.assertIn("Load before spawning or reusing agents", frontmatter)
        self.assertNotIn("verified parent", frontmatter)
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Native delegation is an optimization, not a prerequisite", skill)
        self.assertIn("missing optional delegation capability is never by itself `STOP`, a goal blocker", skill)
        self.assertIn("The main agent holds the writer lease until it explicitly grants", skill)
        self.assertIn("If a native spawn returns `unknown agent_type`", skill)
        self.assertIn("Do not retry the same unavailable role in that task", skill)
        self.assertIn("an already-running task may retain its prior role set", skill)
        self.assertIn("Some task surfaces expose native `spawn_agent` but omit its `agent_type` parameter", skill)
        self.assertIn("profile compatibility mode", skill)
        self.assertIn("does **not** load the TOML sandbox or developer profile", skill)
        self.assertIn("do not emit user-facing dispatch or model-binding logs", skill)
        self.assertIn("$govern-agent-system", (ROOT / "agents/openai.yaml").read_text())
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp); result = json.loads(run(INSTALL, "install", env=env).stdout)
            installed = home / ".agents/skills/govern-agent-system"
            self.assertEqual([path.name for path in installed.iterdir()], ["SKILL.md"])
            self.assertTrue(result["ok"])
        with tempfile.TemporaryDirectory() as temp:
            renamed = Path(temp) / "renamed governance checkout"
            shutil.copytree(ROOT, renamed, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "build", "dist"))
            home = Path(temp) / "home with spaces"; codex = Path(temp) / "codex with spaces"
            env = {**os.environ, "HOME":str(home), "CODEX_HOME":str(codex)}
            result = json.loads(run(renamed / "scripts/install.py", "install", env=env).stdout)
            installed = canonical_root(home) / ".agents/skills/govern-agent-system"
            self.assertEqual(Path(result["installed"]), installed)
            self.assertEqual([path.name for path in installed.iterdir()], ["SKILL.md"])

    def test_skill_reads_explicitly_use_utf8_for_windows(self):
        source = Path(__file__).read_text(encoding="utf-8")
        self.assertIn('(target / "SKILL.md").read_text(encoding="utf-8")', source)
        self.assertIn('(ROOT / "SKILL.md").read_text(encoding="utf-8")', source)
        self.assertIn("→", (ROOT / "SKILL.md").read_text(encoding="utf-8"))
    def test_release_version_diagnostics_are_consistent(self):
        expected = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        self.assertEqual(run(INSTALL, "--version").stdout.strip(), expected)
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp)
            self.assertEqual(json.loads(run(INSTALL, "check", env=env).stdout)["release_version"], expected)

    @unittest.skipIf(os.name == "nt", "POSIX hard-link payload behavior")
    def test_packaged_payload_accepts_uv_cache_hardlinks_but_rejects_symlinks(self):
        spec = util.spec_from_file_location("governance_installer_payload_link_test", INSTALL)
        installer = util.module_from_spec(spec); sys.path.insert(0, str(INSTALL.parent))
        try:
            spec.loader.exec_module(installer)
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "payload.toml"
            alias = root / "cache-alias.toml"
            linked = root / "payload-link.toml"
            source.write_bytes(b'name = "payload"\n')
            os.link(source, alias)
            self.assertEqual(installer.packaged_file(source), b'name = "payload"\n')
            linked.symlink_to(source)
            with self.assertRaises(installer.InstallError):
                installer.packaged_file(linked)
    def test_public_runtime_has_no_controller_interface(self):
        self.assertFalse((ROOT / "scripts/agent_system.py").exists())
        runtime = (ROOT / "SKILL.md").read_text(encoding="utf-8") + "\n".join(
            path.read_text(encoding="utf-8") for path in (ROOT / ".codex/agents").glob("*.toml")
        )
        self.assertNotIn("agent_system.py", runtime)
        self.assertNotIn("mechanical_luna", runtime)

if __name__ == "__main__": unittest.main()
