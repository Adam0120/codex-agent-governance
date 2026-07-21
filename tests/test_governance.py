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

def seed_managed_v020(home, codex, config_bytes=b"[agents]\nmax_threads = 4\nmax_depth = 1\n"):
    release = json.loads((V020_FIXTURE / "release.json").read_text(encoding="utf-8"))
    if release["source_revision"] != "def07224be678695090359b355e40f033f419041":
        raise AssertionError("v0.2.0 fixture revision drift")

    skill_raw = (V020_FIXTURE / "SKILL.md").read_bytes()
    if sha256_bytes(skill_raw) != release["skill"]["file_sha256"]:
        raise AssertionError("v0.2.0 Skill fixture hash mismatch")
    skill_tree_sha256 = sha256_bytes(b"F\0SKILL.md\0" + hashlib.sha256(skill_raw).digest())
    if skill_tree_sha256 != release["skill"]["tree_sha256"]:
        raise AssertionError("v0.2.0 Skill tree hash mismatch")

    fixture_agents = V020_FIXTURE / "agents"
    actual_roles = {path.stem for path in fixture_agents.glob("*.toml")}
    if actual_roles != set(release["adapters"]):
        raise AssertionError("v0.2.0 adapter fixture inventory mismatch")
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
            raise AssertionError(f"v0.2.0 adapter fixture hash mismatch: {name}")
        destination = agents / f"{name}.toml"
        destination.write_bytes(raw)
        adapter_records[name] = {"path": str(destination), "sha256": expected_sha256}

    managed = release["config"]["managed"]
    managed_sha256 = sha256_bytes(json.dumps(managed, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if managed_sha256 != release["config"]["managed_sha256"]:
        raise AssertionError("v0.2.0 managed config fixture hash mismatch")
    parsed_config = tomllib.loads(config_bytes.decode("utf-8"))
    if not isinstance(parsed_config.get("agents"), dict) or any(parsed_config["agents"].get(key) != value for key, value in managed.items()):
        raise AssertionError("v0.2.0 fixture config lacks managed values")
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

class GovernanceTests(unittest.TestCase):
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
            lock = codex / "agent-system" / "install.lock"; lock.parent.mkdir(parents=True, exist_ok=True); lock.write_text("999999\n")
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
            self.assertFalse(json.loads(retry.stdout)["recovery"]); self.assertEqual(journal.read_bytes(), original_journal); self.assertEqual(adapter.read_bytes(), b"corrupted pre-retry adapter\n")
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
            self.assertEqual(manifest["installer_version"], "0.2.1")
            self.assertIn("Use one child by default.", (target / "SKILL.md").read_text(encoding="utf-8"))
            self.assertEqual(
                (installed_runtime["model"], installed_runtime["model_reasoning_effort"]),
                ("gpt-5.6-terra", "medium"),
            )
            self.assertNotEqual(installed_agent.read_bytes(), before_agent)
            self.assertEqual(mode_byte_state(old_snapshot), old_snapshot_state)
            self.assertTrue(json.loads(run(INSTALL, "rollback", "--snapshot", updated["snapshot"], env=env).stdout)["ok"])
            self.assertEqual(installed_agent.read_bytes(), before_agent)
            self.assertEqual(state_bytes(home, codex), before)
            self.assertEqual(mode_byte_state(old_snapshot), old_snapshot_state)
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
            self.assertEqual(config.read_bytes(), installed_config)
            restored = json.loads(run(INSTALL, "rollback", "--snapshot", updated["snapshot"], env=env).stdout)
            self.assertTrue(updated["ok"] and restored["ok"])
            self.assertFalse(updated["mcp_touched"]); self.assertFalse(restored["mcp_touched"])
            self.assertEqual(config.read_bytes(), installed_config)
            self.assertEqual(mcp_config.read_bytes(), before)
            self.assertEqual(state_bytes(home, codex), installed_state)
    @unittest.skipIf(os.name == "nt", "POSIX modes are not Windows ACL guarantees")
    def test_snapshot_permissions_diagnose_and_remediate_legacy_state(self):
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp); codex.mkdir(mode=0o755)
            config = codex / "config.toml"; baseline = b"[other]\nkeep = true\n"; config.write_bytes(baseline); config.chmod(0o644)
            mcp_config = codex / "mcp.toml"; mcp_before = b"[mcp]\nendpoint = 'unchanged'\n"; mcp_config.write_bytes(mcp_before)
            first = json.loads(run(INSTALL, "install", env=env).stdout)
            state = codex / "agent-system"; snapshots = state / "snapshots"; legacy = Path(first["snapshot"])
            for path in [state, snapshots, legacy, *legacy.rglob("*")]:
                if path.is_dir(): path.chmod(0o755)
                elif path.is_file(): path.chmod(0o644)
            before_modes = {str(path): stat.S_IMODE(path.lstat().st_mode) for path in [state, snapshots, legacy, *legacy.rglob("*")]}
            diagnosis = json.loads(run(INSTALL, "check", env=env).stdout)
            self.assertTrue(diagnosis["permission_problems"]); self.assertEqual(before_modes, {str(path): stat.S_IMODE(path.lstat().st_mode) for path in [state, snapshots, legacy, *legacy.rglob("*")]})
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
    def test_legacy_ledger_upgrade_and_lock_failure_permission_boundary(self):
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
                self.assertEqual(tomllib.loads(config.read_text())["agents"], {"future_key":"keep", "interrupt_message":False, "max_depth":1, "max_threads":4})
        self.assertEqual(len(set(rendered)), 1)
        frontmatter = (ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        self.assertIn("\nname: govern-agent-system\n", "\n" + frontmatter)
        self.assertIn("Automatically govern native Codex custom-agent delegation", frontmatter)
        self.assertIn("Load before spawning or reusing agents", frontmatter)
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
    def test_public_runtime_has_no_controller_interface(self):
        self.assertFalse((ROOT / "scripts/agent_system.py").exists())
        runtime = (ROOT / "SKILL.md").read_text(encoding="utf-8") + "\n".join(
            path.read_text(encoding="utf-8") for path in (ROOT / ".codex/agents").glob("*.toml")
        )
        self.assertNotIn("agent_system.py", runtime)
        self.assertNotIn("mechanical_luna", runtime)

if __name__ == "__main__": unittest.main()
