import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts" / "install.py"
ROLE_DIR = ROOT / ".codex" / "agents"
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
ROLE_MATRIX = {
    "default": ("gpt-5.6-terra", "medium", "read-only"),
    "worker": ("gpt-5.6-terra", "medium", "workspace-write"),
    "explorer": ("gpt-5.6-terra", "medium", "read-only"),
    "code_locator": ("gpt-5.3-codex-spark", "high", "read-only"),
    "cross_module_architect": ("gpt-5.6-terra", "medium", "read-only"),
    "systems_safety": ("gpt-5.6-terra", "medium", "workspace-write"),
    "semantic_reviewer": ("gpt-5.6-sol", "medium", "read-only"),
    "release_operator": ("gpt-5.6-terra", "medium", "workspace-write"),
}
BANNED_RUNTIME_TEXT = (
    "agent_system.py",
    "dispatch --request",
    "profile --role",
    "reuse_key",
    "mandatory overlay",
    "mandatory mcp",
)


def run(*args, env, ok=True):
    result = subprocess.run(
        [sys.executable, str(INSTALL), *args],
        text=True,
        capture_output=True,
        env=env,
    )
    if ok:
        assert result.returncode == 0, result.stderr
    return result


def canonical_root(path):
    return path.parent.resolve(strict=False) / path.name


def isolated(temp):
    raw_home = Path(temp) / "home"
    raw_codex = Path(temp) / "codex"
    home = canonical_root(raw_home)
    codex = canonical_root(raw_codex)
    env = {**os.environ, "HOME": str(raw_home), "CODEX_HOME": str(raw_codex)}
    return home, codex, env


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def tree_hash(root):
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        name = relative.as_posix().encode("utf-8")
        if path.is_dir():
            digest.update(b"D\0" + name + b"\0")
        else:
            digest.update(b"F\0" + name + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def destination_state(home, codex):
    targets = [
        home / ".agents/skills/govern-agent-system",
        codex / "agents",
        codex / "config.toml",
        codex / "agent-system/managed-install.json",
        codex / "agent-system/ledger.jsonl",
    ]
    result = {}
    for target in targets:
        if target.is_file():
            result[str(target)] = ("file", target.read_bytes(), stat.S_IMODE(target.stat().st_mode))
        elif target.is_dir():
            for path in [target, *sorted(target.rglob("*"))]:
                relative = "." if path == target else path.relative_to(target).as_posix()
                if path.is_file():
                    payload = path.read_bytes()
                    kind = "file"
                else:
                    payload = None
                    kind = "directory"
                result[f"{target}:{relative}"] = (kind, payload, stat.S_IMODE(path.stat().st_mode))
    return result


def seed_managed_legacy(home, codex, version="0.1.2"):
    skill = home / ".agents/skills/govern-agent-system"
    (skill / "scripts").mkdir(parents=True)
    (skill / "references").mkdir()
    (skill / "SKILL.md").write_text("legacy controller skill\n", encoding="utf-8")
    (skill / "scripts/agent_system.py").write_text("# legacy controller\n", encoding="utf-8")
    (skill / "references/roles.json").write_text('{"legacy":true}\n', encoding="utf-8")

    agents = codex / "agents"
    agents.mkdir(parents=True)
    adapter_records = {}
    for name in sorted(ROLE_NAMES):
        path = agents / f"{name}.toml"
        path.write_text(f'name = "{name}"\ndeveloper_instructions = "legacy bootstrap"\n', encoding="utf-8")
        adapter_records[name] = {"path": str(path), "sha256": sha256(path.read_bytes())}
    (agents / "user-owned-agent.toml").write_text('name = "user-owned"\n', encoding="utf-8")

    config = codex / "config.toml"
    config.write_bytes(
        b'top = "preserved"\n\n[agents]\nfuture_key = "preserved"\nenabled = true\nmax_depth = 1\nmax_threads = 4\n\n[other]\nflag = false\n'
    )
    managed = {"enabled": True, "max_depth": 1, "max_threads": 4}
    state = codex / "agent-system"
    state.mkdir()
    manifest = {
        "schema_version": 1,
        "identity": "govern-agent-system",
        "installer_version": version,
        "destinations": {
            "agents": str(agents),
            "config": str(config),
            "manifest": str(state / "managed-install.json"),
            "skill": str(skill),
        },
        "link": False,
        "skill": {"kind": "directory", "content_sha256": tree_hash(skill), "target": None},
        "adapters": adapter_records,
        "config": {
            "path": str(config),
            "managed": managed,
            "managed_sha256": sha256(json.dumps(managed, sort_keys=True, separators=(",", ":")).encode("utf-8")),
        },
    }
    (state / "managed-install.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    ledger = state / "ledger.jsonl"
    ledger.write_bytes(b'{"legacy":"inert bytes"}\n\x00opaque\n')
    snapshot = state / "snapshots/snapshot-0123456789abcdef0123456789abcdef"
    snapshot.mkdir(parents=True)
    snapshot_entries = [
        {"label": "skill", "path": str(skill), "kind": "missing", "sha256": None, "target": None},
        {"label": "config", "path": str(config), "kind": "missing", "sha256": None, "target": None},
        {"label": "managed-manifest", "path": str(state / "managed-install.json"), "kind": "missing", "sha256": None, "target": None},
        *[
            {"label": f"agent-{name}", "path": str(agents / f"{name}.toml"), "kind": "missing", "sha256": None, "target": None}
            for name in sorted(ROLE_NAMES)
        ],
    ]
    (snapshot / "manifest.json").write_text(json.dumps({
        "schema_version": 2,
        "identity": "govern-agent-system",
        "installer_version": version,
        "purpose": "install",
        "entries": snapshot_entries,
    }, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    for directory in [skill, skill / "scripts", skill / "references", state, state / "snapshots", snapshot]:
        directory.chmod(0o700)
    for path in [*skill.rglob("*"), *agents.glob("*.toml"), config, state / "managed-install.json", ledger, snapshot / "manifest.json"]:
        if path.is_file():
            path.chmod(0o600)
    return config.read_bytes(), ledger.read_bytes()


class V02RuntimeTests(unittest.TestCase):
    def test_packaged_runtime_is_direct_self_contained_and_exact(self):
        adapters = {path.stem: tomllib.loads(path.read_text(encoding="utf-8")) for path in ROLE_DIR.glob("*.toml")}
        self.assertEqual(set(adapters), ROLE_NAMES)
        self.assertEqual({name for name, runtime in ROLE_MATRIX.items() if runtime[1] == "high"}, {"code_locator"})
        self.assertEqual({name for name, runtime in ROLE_MATRIX.items() if runtime[0] == "gpt-5.6-sol"}, {"semantic_reviewer"})
        for name, document in adapters.items():
            self.assertEqual(
                (document["model"], document["model_reasoning_effort"], document["sandbox_mode"]),
                ROLE_MATRIX[name],
            )
            instructions = document["developer_instructions"].lower()
            self.assertIn("spawn child agents", instructions)
            self.assertRegex(instructions, r"do not[^.]*spawn child agents")
            self.assertIn("frozen", instructions)
            self.assertIn("stop", instructions)
            self.assertIn("parent owns", instructions)
            self.assertNotIn("english", instructions)
            self.assertIn("skills", instructions)
            self.assertIn("mcp", instructions)
            for banned in BANNED_RUNTIME_TEXT:
                self.assertNotIn(banned, instructions)
            if name == "code_locator":
                self.assertIn("lookup status is this role's terminal status", instructions)
            else:
                self.assertIn("terminal status (complete, partial, or stop)", instructions)
        narrowed_contracts = {
            "cross_module_architect": ("candidate options", "do not select product behavior"),
            "systems_safety": ("exact parent-approved", "accept risk"),
            "semantic_reviewer": (
                "findings are advisory",
                "do not approve, reject, merge, release, or claim final acceptance",
            ),
            "release_operator": ("revision-bound runbook", "idempotency precondition", "live state has drifted"),
        }
        for name, required in narrowed_contracts.items():
            instructions = adapters[name]["developer_instructions"].lower()
            for literal in required:
                self.assertIn(literal, instructions)
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8").lower()
        self.assertIn("one child by default", skill)
        self.assertIn("final acceptance", skill)
        self.assertIn("user decisions", skill)
        self.assertNotIn("english", skill)
        for banned in BANNED_RUNTIME_TEXT:
            self.assertNotIn(banned, skill)
        self.assertNotIn("mechanical_luna", "\n".join(path.read_text(encoding="utf-8") for path in ROLE_DIR.glob("*.toml")))
        self.assertEqual(
            tomllib.loads((ROOT / ".codex/config.toml").read_text(encoding="utf-8"))["agents"],
            {"max_threads": 4, "max_depth": 1},
        )

    def test_skill_requires_a_precise_single_node_dispatch_envelope(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8").lower()
        required = (
            "one observable state transition or one evidence question",
            "load this skill before spawning or reusing any native custom agent",
            "two or more independent work surfaces",
            "loading the skill does not authorize delegation by itself",
            "repository/worktree plus baseline revision",
            "exact files or symbols",
            "allowed operation and exclusions",
            "complete",
            "partial",
            "one active writer per worktree/file set",
            "twice in succession for the same task",
            "one higher supported model or reasoning level",
            "re-bounded task still fails for reasoning quality",
        )
        for literal in required:
            self.assertIn(literal, skill)

        readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
        readme_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        self.assertIn("## dispatch discipline", readme)
        self.assertIn("one observable state transition or one evidence question", readme)
        self.assertIn("## 派发纪律", readme_zh)
        self.assertIn("一个可观察状态转换或一个证据问题", readme_zh)

    def test_fresh_install_payload_config_and_read_only_check(self):
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp)
            codex.mkdir()
            config = codex / "config.toml"
            config.write_bytes(b'top = "keep"\n\n[agents]\nfuture_key = "keep"\nmax_threads = 9\n\n[mcp]\nenabled = true\n')
            before_check = destination_state(home, codex)
            checked = json.loads(run("check", env=env).stdout)
            self.assertEqual(destination_state(home, codex), before_check)
            self.assertFalse(checked["managed"])
            self.assertEqual(checked["config_migration"], "none")

            installed = json.loads(run("install", env=env).stdout)
            skill = home / ".agents/skills/govern-agent-system"
            self.assertEqual([path.relative_to(skill).as_posix() for path in skill.rglob("*")], ["SKILL.md"])
            adapter_paths = sorted((codex / "agents").glob("*.toml"))
            self.assertEqual({path.stem for path in adapter_paths}, ROLE_NAMES)
            for path in adapter_paths:
                tomllib.loads(path.read_text(encoding="utf-8"))
            parsed = tomllib.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(parsed["agents"], {"future_key": "keep", "max_depth": 1, "max_threads": 4})
            self.assertEqual(parsed["mcp"], {"enabled": True})
            rendered = config.read_bytes()
            self.assertTrue(rendered.startswith(b'top = "keep"\n\n[agents]\nfuture_key = "keep"\n'))
            self.assertIn(b'\n[mcp]\nenabled = true\n', rendered)
            self.assertTrue(installed["ok"])

            after_install = destination_state(home, codex)
            self.assertTrue(json.loads(run("check", env=env).stdout)["ok"])
            self.assertEqual(destination_state(home, codex), after_install)

        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp)
            rejected = run("install", "--link", env=env, ok=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(destination_state(home, codex), {})

    @unittest.skipUnless(shutil.which("codex"), "Codex CLI is not installed")
    def test_generated_config_parses_with_local_codex_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp)
            self.assertTrue(json.loads(run("install", env=env).stdout)["ok"])
            result = subprocess.run(
                [shutil.which("codex"), "features", "list"],
                text=True,
                capture_output=True,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_config_merge_fails_closed_on_table_like_multiline_string(self):
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp)
            codex.mkdir()
            config = codex / "config.toml"
            original = (
                'title = "preserved"\n'
                'payload = """\n'
                'before\n'
                '[agents]\n'
                'max_threads = 99\n'
                'after\n'
                '"""\n\n'
                '[other]\n'
                'flag = true\n'
            ).encode("utf-8")
            config.write_bytes(original)
            parsed_before = tomllib.loads(original.decode("utf-8"))
            state_before = destination_state(home, codex)

            checked = run("check", env=env, ok=False)
            self.assertNotEqual(checked.returncode, 0)
            self.assertIn("multiline", (checked.stdout + checked.stderr).lower())
            self.assertEqual(destination_state(home, codex), state_before)
            self.assertEqual(tomllib.loads(config.read_text(encoding="utf-8")), parsed_before)

            installed = run("install", env=env, ok=False)
            self.assertNotEqual(installed.returncode, 0)
            self.assertIn("multiline", (installed.stdout + installed.stderr).lower())
            self.assertEqual(destination_state(home, codex), state_before)
            self.assertEqual(tomllib.loads(config.read_text(encoding="utf-8")), parsed_before)

    def test_managed_v012_upgrade_preserves_ledger_and_exact_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp)
            legacy_config, legacy_ledger = seed_managed_legacy(home, codex)
            legacy_state = destination_state(home, codex)
            legacy_snapshot = codex / "agent-system/snapshots/snapshot-0123456789abcdef0123456789abcdef/manifest.json"
            legacy_snapshot_bytes = legacy_snapshot.read_bytes()
            legacy_snapshot_mode = stat.S_IMODE(legacy_snapshot.stat().st_mode)

            before_check = destination_state(home, codex)
            checked = json.loads(run("check", env=env).stdout)
            self.assertTrue(checked["ok"])
            self.assertEqual(checked["config_migration"], "remove_legacy_agents_enabled")
            self.assertEqual(destination_state(home, codex), before_check)

            upgraded = json.loads(run("install", env=env).stdout)
            skill = home / ".agents/skills/govern-agent-system"
            self.assertEqual([path.relative_to(skill).as_posix() for path in skill.rglob("*")], ["SKILL.md"])
            self.assertFalse((skill / "scripts/agent_system.py").exists())
            self.assertEqual((codex / "agent-system/ledger.jsonl").read_bytes(), legacy_ledger)
            self.assertEqual(legacy_snapshot.read_bytes(), legacy_snapshot_bytes)
            self.assertEqual(stat.S_IMODE(legacy_snapshot.stat().st_mode), legacy_snapshot_mode)
            self.assertEqual((codex / "agents/user-owned-agent.toml").read_text(), 'name = "user-owned"\n')
            parsed = tomllib.loads((codex / "config.toml").read_text(encoding="utf-8"))
            self.assertEqual(parsed["top"], "preserved")
            self.assertEqual(parsed["other"], {"flag": False})
            self.assertEqual(parsed["agents"]["future_key"], "preserved")
            self.assertNotIn("enabled", parsed["agents"])

            rolled_back = json.loads(run("rollback", "--snapshot", upgraded["snapshot"], env=env).stdout)
            self.assertTrue(rolled_back["ok"])
            self.assertEqual(destination_state(home, codex), legacy_state)
            self.assertEqual((codex / "config.toml").read_bytes(), legacy_config)
            self.assertTrue(tomllib.loads((codex / "config.toml").read_text())["agents"]["enabled"])
            self.assertEqual((codex / "agent-system/ledger.jsonl").read_bytes(), legacy_ledger)
            self.assertEqual(legacy_snapshot.read_bytes(), legacy_snapshot_bytes)

    def test_unmanaged_agents_enabled_fails_closed_without_deleting_other_enabled_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp)
            codex.mkdir()
            config = codex / "config.toml"
            original = b'[agents]\nenabled = true\nfuture_key = "keep"\n\n[mcp]\nenabled = true\n'
            config.write_bytes(original)
            before = destination_state(home, codex)
            for command in ("check", "install"):
                result = run(command, env=env, ok=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("enabled", (result.stdout + result.stderr).lower())
                self.assertEqual(destination_state(home, codex), before)
                self.assertEqual(config.read_bytes(), original)

    def test_legacy_agents_enabled_value_requires_exact_managed_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            home, codex, env = isolated(temp)
            seed_managed_legacy(home, codex)
            config = codex / "config.toml"
            config.write_text(config.read_text().replace("enabled = true", "enabled = false", 1))
            manifest_path = codex / "agent-system/managed-install.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["config"]["managed"]["enabled"] = False
            manifest["config"]["managed_sha256"] = sha256(
                json.dumps(manifest["config"]["managed"], sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
            config.chmod(0o600)
            manifest_path.chmod(0o600)
            before = destination_state(home, codex)
            for command in ("check", "install"):
                result = run(command, env=env, ok=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("provenance", (result.stdout + result.stderr).lower())
                self.assertEqual(destination_state(home, codex), before)

    def test_legacy_enabled_migration_accepts_only_released_managed_versions(self):
        for version in ("0.1.0", "0.1.1", "0.1.2"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp:
                home, codex, env = isolated(temp)
                seed_managed_legacy(home, codex, version)
                before = destination_state(home, codex)
                upgraded = json.loads(run("install", env=env).stdout)
                self.assertNotIn("enabled", tomllib.loads((codex / "config.toml").read_text())["agents"])
                self.assertTrue(json.loads(run("rollback", "--snapshot", upgraded["snapshot"], env=env).stdout)["ok"])
                self.assertEqual(destination_state(home, codex), before)

        for version in ("0.0.9", "0.1.99"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp:
                home, codex, env = isolated(temp)
                seed_managed_legacy(home, codex, version)
                before = destination_state(home, codex)
                for command in ("check", "install"):
                    result = run(command, env=env, ok=False)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("version", (result.stdout + result.stderr).lower())
                    self.assertEqual(destination_state(home, codex), before)

    def test_public_documentation_keeps_v02_contracts_aligned(self):
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        for text in (english, chinese):
            for literal in (
                "0.2.1",
                "python3 scripts/install.py check",
                "python3 scripts/install.py install",
                "python3 scripts/install.py rollback --snapshot <snapshot-path>",
                "CodeGraph",
                "mechanical_luna",
                "max_depth = 1",
                "max_threads = 4",
            ):
                self.assertIn(literal, text)
            for name, runtime in ROLE_MATRIX.items():
                self.assertIn(f"| `{name}` | `{runtime[0]}` | {runtime[1]} | {runtime[2]} |", text)
            config_example = text.split("```toml", 1)[1].split("```", 1)[0]
            self.assertNotIn("enabled", config_example)
        self.assertIn("Upgrade an existing managed installation", english)
        self.assertIn("升级已安装的受管版本", chinese)


if __name__ == "__main__":
    unittest.main()
