from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


ROOT = Path(__file__).resolve().parent
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


class BuildPyWithRuntimePayload(build_py):
    """Copy the canonical repository runtime into the installed CLI package."""

    def run(self):
        super().run()
        payload = Path(self.build_lib) / "codex_agent_governance" / "_payload"
        if payload.exists():
            shutil.rmtree(payload)
        scripts = payload / "scripts"
        agents = payload / ".codex" / "agents"
        scripts.mkdir(parents=True)
        agents.mkdir(parents=True)
        (payload / "__init__.py").write_text("", encoding="utf-8")
        (scripts / "__init__.py").write_text("", encoding="utf-8")
        shutil.copy2(ROOT / "SKILL.md", payload / "SKILL.md")
        shutil.copy2(ROOT / "scripts" / "install.py", scripts / "install.py")
        shutil.copy2(ROOT / "scripts" / "managed_lock.py", scripts / "managed_lock.py")
        source_agents = ROOT / ".codex" / "agents"
        actual_roles = {path.stem for path in source_agents.glob("*.toml")}
        if actual_roles != ROLE_NAMES:
            raise RuntimeError("the wheel payload must contain exactly the eight managed roles")
        for name in sorted(ROLE_NAMES):
            shutil.copy2(source_agents / f"{name}.toml", agents / f"{name}.toml")


setup(cmdclass={"build_py": BuildPyWithRuntimePayload})
