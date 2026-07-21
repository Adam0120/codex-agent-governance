from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _installer_path() -> Path:
    package_root = Path(__file__).resolve().parent
    packaged = package_root / "_payload" / "scripts" / "install.py"
    if packaged.is_file():
        return packaged
    repository = package_root.parents[1] / "scripts" / "install.py"
    if repository.is_file():
        return repository
    raise RuntimeError("codex-agent-governance runtime payload is missing")


def _load_installer() -> ModuleType:
    path = _installer_path()
    spec = importlib.util.spec_from_file_location("_codex_agent_governance_installer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the codex-agent-governance installer")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def main() -> None:
    _load_installer().main()
