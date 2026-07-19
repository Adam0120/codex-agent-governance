import json
import os
import shutil
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
CORE = ROOT / "scripts" / "agent_system.py"
INSTALL = ROOT / "scripts" / "install.py"

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

def release_copy(target, version, marker):
    shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "build", "dist"))
    installer = target / "scripts/install.py"
    installer.write_text(installer.read_text().replace('INSTALL_VERSION = "0.1.0"', f'INSTALL_VERSION = "{version}"'))
    (target / "release-marker.txt").write_text(marker)

class GovernanceTests(unittest.TestCase):
    def test_schema_routing_and_locator_smoke(self):
        self.assertTrue(json.loads(run(CORE, "evaluate", "--cwd", str(ROOT)).stdout)["ok"])
        self.assertTrue(json.loads(run(CORE, "locator-smoke").stdout)["ok"])
        request = json.dumps({"parent_model":"gpt-5.6-sol","parent_reasoning_effort":"high","task_type":"implementation","known_target":False,"factual_uncertainty":[]})
        self.assertEqual(json.loads(run(CORE, "dispatch", "--cwd", str(ROOT), "--request", request).stdout)["role"], "code_locator")
        bad = json.dumps({"parent_model":"gpt-5.6-sol","parent_reasoning_effort":"high","task_type":"unknown","known_target":False,"factual_uncertainty":[]})
        self.assertNotEqual(run(CORE, "dispatch", "--request", bad, ok=False).returncode, 0)
    def test_generation_privacy_and_portable_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"; codex = Path(temp) / "codex"; env = {**os.environ, "HOME":str(home), "CODEX_HOME":str(codex)}
            first = json.loads(run(CORE, "generate", "--cwd", str(ROOT), env=env).stdout)
            second = json.loads(run(CORE, "generate", "--cwd", str(ROOT), env=env).stdout)
            self.assertEqual(first["role_count"], 8); self.assertEqual(second["changed"], [])
            profile = run(CORE, "profile", "--cwd", str(ROOT), "--role", "code_locator", env=env).stdout
            self.assertIn("PARTIAL", profile); self.assertIn("English", profile)
            bad = json.dumps({"task_id":"x","task_hash":"a"*64,"role":"worker","task_type":"x","result_status":"success","failure_class":"none","user_correction_category":"none","user_correction":False,"fallback":"none","config_hash":"a"*64,"prompt":"no"})
            self.assertNotEqual(run(CORE, "record", "--cwd", str(ROOT), "--event", bad, env=env, ok=False).returncode, 0)
            for field, value in (("user_correction_category", "prompt\nTool boundary:"), ("tool_counts", {"/tmp/path": 1}), ("tool_counts", {"shell": {"nested": 1}})):
                event = {"task_id":"x","task_hash":"a"*64,"role":"worker","task_type":"x","result_status":"success","failure_class":"none","user_correction_category":"none","user_correction":False,"fallback":"none","config_hash":"a"*64}
                event[field] = value
                self.assertNotEqual(run(CORE, "record", "--cwd", str(ROOT), "--event", json.dumps(event), env=env, ok=False).returncode, 0)
    def test_locator_first_for_all_unknown_targets_and_adapter_bootstrap(self):
        for task in ("implementation", "review", "release", "cross_module_contract"):
            request = json.dumps({"parent_model":"gpt-5.6-sol","parent_reasoning_effort":"high","task_type":task,"known_target":False,"factual_uncertainty":[]})
            self.assertEqual(json.loads(run(CORE, "dispatch", "--request", request).stdout)["role"], "code_locator")
        text = (ROOT / ".codex/agents/worker.toml").read_text()
        self.assertNotIn("<installed-skill>", text); self.assertNotIn("<current cwd>", text); self.assertIn("$govern-agent-system", text); self.assertIn('"$HOME/.agents/skills/govern-agent-system', text)
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
    def test_mechanical_luna_controller_smoke_and_dotted_agents_rejection(self):
        guards = {k: True for k in ("exact_target_known","expected_output_contract_known","deterministic_verification_available","single_repository_scope","no_material_ambiguity_or_judgment","no_security_auth_secret","no_persistence_migration","no_concurrency_unsafe","no_public_contract_protocol_schema","no_release_deploy_cloud_irreversible","no_incident_root_cause_diagnosis","no_cross_repository_decision","no_semantic_review")}
        req={"parent_model":"gpt-5.6-sol","parent_reasoning_effort":"high","task_type":"implementation","known_target":True,"factual_uncertainty":[],"mechanical_worker":{**guards,"task_category":"formatting","target":"one-file","deterministic_check":"format-check"}}
        result=json.loads(run(CORE,"dispatch","--request",json.dumps(req)).stdout); self.assertEqual((result["runtime_variant"],result["model"],result["reasoning_effort"]),("mechanical_luna","gpt-5.6-luna","high")); self.assertIn("Bounded target: one-file",result["assignment"])
        with tempfile.TemporaryDirectory() as temp:
            env={**os.environ,"HOME":str(Path(temp)/"home"),"CODEX_HOME":str(Path(temp)/"codex")}; config=Path(temp)/"codex/config.toml"; config.parent.mkdir(parents=True); before=b"agents.max_threads = 9\n"; config.write_bytes(before)
            self.assertNotEqual(run(CORE,"generate","--cwd",str(ROOT),env=env,ok=False).returncode,0); self.assertEqual(config.read_bytes(),before)
            self.assertFalse((Path(env["CODEX_HOME"]) / "agents").exists())
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
            direct = run(CORE, "generate", "--cwd", str(ROOT), env=env, ok=False)
            self.assertIn("RECOVERY_REQUIRED", direct.stderr); self.assertEqual(adapter.read_bytes(), b"corrupted pre-retry adapter\n")
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
            direct = run(CORE, "generate", "--cwd", str(ROOT), env=env, ok=False)
            self.assertIn("INSTALL_LOCKED", direct.stderr); self.assertEqual(state_bytes(home, codex), before)
            stdout, stderr = process.communicate(timeout=10)
            self.assertNotEqual(process.returncode, 0); self.assertIn("RECOVERY_REQUIRED", stderr); self.assertEqual(state_bytes(home, codex), before)
    def test_trusted_root_alias_acceptance_and_managed_link_rejection(self):
        spec = util.spec_from_file_location("governance_core_home_test", CORE)
        module = util.module_from_spec(spec); sys.path.insert(0, str(CORE.parent))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        requested = Path("configured-home"); fallback = Path("platform-home")
        self.assertEqual(module.configured_home({"HOME":str(requested), "USERPROFILE":str(fallback)}, fallback), requested)
        self.assertEqual(module.configured_home({"USERPROFILE":str(requested)}, fallback), fallback)
        install_spec = util.spec_from_file_location("governance_installer_link_test", INSTALL)
        installer = util.module_from_spec(install_spec); sys.path.insert(0, str(INSTALL.parent))
        try:
            install_spec.loader.exec_module(installer)
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"; skill = home / ".agents/skills/govern-agent-system"
            source = Path(temp) / "source"; source.mkdir(); skill.parent.mkdir(parents=True); skill.symlink_to(source, target_is_directory=True)
            self.assertEqual(installer.normalize_windows_link_target(r"\\?\C:/Release/Skill"), r"c:\release\skill")
            self.assertEqual(installer.normalize_windows_link_target(r"\\?\UNC\Server\Share\Release"), r"\\server\share\release")
            self.assertEqual(installer.normalize_windows_link_target(r"\??\C:\Release\Skill"), r"\??\c:\release\skill")
            installer.validate_chain(skill, home, allow_final_symlink_to=source.resolve())
            skill.unlink(); alternate = Path(temp) / "alternate"; alternate.symlink_to(source, target_is_directory=True)
            skill.symlink_to(alternate, target_is_directory=True)
            with self.assertRaises(installer.InstallError):
                installer.validate_chain(skill, home, allow_final_symlink_to=source)
            skill.unlink(); alternate.unlink()
            missing = Path(temp) / "missing"; skill.symlink_to(missing, target_is_directory=True)
            with self.assertRaises(installer.InstallError):
                installer.validate_chain(skill, home, allow_final_symlink_to=missing)
            skill.unlink()
            fake_reparse = object()
            with mock.patch.object(installer, "lstat_or_none", side_effect=lambda path: fake_reparse if path == skill else None), \
                 mock.patch.object(installer, "is_link_or_reparse", side_effect=lambda info: info is fake_reparse), \
                 mock.patch.object(installer.Path, "is_symlink", return_value=False):
                with self.assertRaises(installer.InstallError):
                    installer.validate_chain(skill, home, allow_final_symlink_to=source)
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
    def test_copy_and_link_updates_validate_recorded_release_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            release_a = Path(temp) / "release-a"; release_b = Path(temp) / "release-b"
            release_copy(release_a, "0.1.0", "A"); release_copy(release_b, "0.1.1", "B")
            for link in (False, True):
                with self.subTest(link=link):
                    home = Path(temp) / f"home-{link}"; codex = Path(temp) / f"codex-{link}"
                    env = {**os.environ, "HOME":str(home), "CODEX_HOME":str(codex)}
                    args = ("install", "--link") if link else ("install",)
                    self.assertTrue(json.loads(run(release_a / "scripts/install.py", *args, env=env).stdout)["ok"])
                    self.assertTrue(json.loads(run(release_b / "scripts/install.py", *args, env=env).stdout)["ok"])
                    target = home / ".agents/skills/govern-agent-system"
                    manifest = json.loads((codex / "agent-system/managed-install.json").read_text())
                    self.assertEqual(manifest["installer_version"], "0.1.1")
                    if link:
                        self.assertTrue(target.is_symlink()); self.assertEqual(target.resolve(), release_b.resolve())
                        self.assertNotEqual(run(release_b / "scripts/install.py", "install", env=env, ok=False).returncode, 0)
                        self.assertTrue(target.is_symlink()); self.assertEqual(target.resolve(), release_b.resolve())
                        target.unlink(); target.symlink_to(release_a, target_is_directory=True)
                        self.assertNotEqual(run(release_b / "scripts/install.py", "install", "--link", env=env, ok=False).returncode, 0)
                        target.unlink(); target.symlink_to(Path(temp) / "missing-release", target_is_directory=True)
                        self.assertNotEqual(run(release_b / "scripts/install.py", "install", "--link", env=env, ok=False).returncode, 0)
                    else:
                        self.assertFalse(target.is_symlink()); self.assertEqual((target / "release-marker.txt").read_text(), "B")
    def test_config_order_audit_and_canonical_skill_identity(self):
        rendered = []
        for seed in ("1", "2", "3", "4"):
            with tempfile.TemporaryDirectory() as temp:
                home, codex, env = isolated(temp); config = codex / "config.toml"; config.parent.mkdir(parents=True); config.write_text('[agents]\nfuture_key = "keep"\ninterrupt_message = false\n')
                run(CORE, "generate", "--cwd", str(ROOT), env={**env, "PYTHONHASHSEED":seed})
                rendered.append(config.read_bytes()); self.assertTrue(json.loads(run(CORE, "audit", "--cwd", str(ROOT), env=env).stdout)["ok"])
                self.assertEqual(tomllib.loads(config.read_text())["agents"], {"future_key":"keep", "interrupt_message":False, "enabled":True, "max_depth":1, "max_threads":4})
        self.assertEqual(len(set(rendered)), 1)
        frontmatter = (ROOT / "SKILL.md").read_text().split("---", 2)[1]
        self.assertIn("\nname: govern-agent-system\n", "\n" + frontmatter)
        self.assertIn("$govern-agent-system", (ROOT / "agents/openai.yaml").read_text())
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp); result = json.loads(run(INSTALL, "install", env=env).stdout)
            installed = home / ".agents/skills/govern-agent-system/scripts/agent_system.py"
            self.assertIn("Role: worker", run(installed, "profile", "--cwd", str(ROOT), "--role", "worker", env=env).stdout)
            dispatch = json.loads(run(installed, "dispatch", "--request", json.dumps({"parent_model":"gpt-5.6-sol","parent_reasoning_effort":"high","task_type":"implementation","known_target":True,"factual_uncertainty":[]}), env=env).stdout)
            self.assertIn('python3 "$HOME/.agents/skills/govern-agent-system', dispatch["assignment"]); self.assertTrue(result["ok"])
        with tempfile.TemporaryDirectory() as temp:
            renamed = Path(temp) / "renamed governance checkout"
            shutil.copytree(ROOT, renamed, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "build", "dist"))
            home = Path(temp) / "home with spaces"; codex = Path(temp) / "codex with spaces"
            env = {**os.environ, "HOME":str(home), "CODEX_HOME":str(codex)}
            result = json.loads(run(renamed / "scripts/install.py", "install", env=env).stdout)
            installed = canonical_root(home) / ".agents/skills/govern-agent-system"
            self.assertEqual(Path(result["installed"]), installed)
            assignment = json.loads(run(installed / "scripts/agent_system.py", "dispatch", "--cwd", str(renamed), "--request", json.dumps({"parent_model":"gpt-5.6-sol","parent_reasoning_effort":"high","task_type":"implementation","known_target":True,"factual_uncertainty":[]}), env=env).stdout)["assignment"]
            self.assertIn('python3 "$HOME/.agents/skills/govern-agent-system/scripts/agent_system.py"', assignment)
    def test_chart_determinism_and_public_scan(self):
        run(ROOT / "scripts" / "render_charts.py")
        before = (ROOT / "docs/assets/instruction-bytes.svg").read_bytes(); run(ROOT / "scripts" / "render_charts.py")
        self.assertEqual(before, (ROOT / "docs/assets/instruction-bytes.svg").read_bytes())
        text = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "references").rglob("*") if p.is_file())
        self.assertNotIn("rootkey.csv", text.lower())

if __name__ == "__main__": unittest.main()
