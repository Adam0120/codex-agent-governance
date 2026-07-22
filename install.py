#!/usr/bin/env python3
"""Install the bundled Skill and native Codex sub-agent configurations.

Usage:
    python3 install.py
    CODEX_HOME=/path/to/.codex python3 install.py
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SKILL_NAME = "govern-agent-system"
SKILL_SOURCE = ROOT / "SKILL.md"
AGENTS_SOURCE = ROOT / ".codex" / "agents"


def codex_home() -> Path:
    """Return Codex's configured home, respecting CODEX_HOME when present."""
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def install() -> None:
    """Copy the Skill and bundled role TOMLs into the native Codex locations."""
    if not SKILL_SOURCE.is_file() or not AGENTS_SOURCE.is_dir():
        raise RuntimeError("run this script from a complete codex-agent-governance checkout")

    home = codex_home()
    skill_target = home / "skills" / SKILL_NAME / "SKILL.md"
    agents_target = home / "agents"
    skill_target.parent.mkdir(parents=True, exist_ok=True)
    agents_target.mkdir(parents=True, exist_ok=True)

    shutil.copy2(SKILL_SOURCE, skill_target)
    agent_sources = sorted(AGENTS_SOURCE.glob("*.toml"))
    if not agent_sources:
        raise RuntimeError("no bundled Codex agent configurations were found")
    for source in agent_sources:
        shutil.copy2(source, agents_target / source.name)

    print(f"Installed {skill_target}")
    print(f"Installed {len(agent_sources)} agent configurations in {agents_target}")
    print("Restart Codex before starting a new task so it reloads the agent registry.")


if __name__ == "__main__":
    install()
