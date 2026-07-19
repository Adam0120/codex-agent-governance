#!/usr/bin/env python3
"""Portable, deterministic governance for a fixed Codex role catalog."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import managed_lock

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "references"
RELEASE_VERSION = "0.1.2"
SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
PARENT_MODELS = {"gpt-5.6-sol", "gpt-5.6-terra"}
PARENT_EFFORTS = {"high", "xhigh", "max", "ultra"}
UNCERTAINTY = {"location", "ownership", "blast_radius"}
EVENT_FIELDS = {"task_id", "task_hash", "role", "task_type", "result_status", "failure_class", "user_correction_category", "user_correction", "fallback", "duration_ms", "tool_counts", "output_bytes", "config_hash"}
CORRECTIONS = {"none", "routing", "scope", "contract", "safety", "quality"}
TOOLS = {"shell", "search", "web", "mcp", "other"}
CONFIG_KEY_ORDER = ("enabled", "max_depth", "max_threads")


def fail(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}, sort_keys=True), file=sys.stderr)
    raise SystemExit(2)


def is_link_or_reparse(info: os.stat_result) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & reparse)


def trusted_root(raw: Path) -> Path:
    absolute = Path(os.path.abspath(os.path.expanduser(str(raw))))
    root = absolute.parent.resolve(strict=False) / absolute.name
    try: info = root.lstat()
    except FileNotFoundError: return root
    if is_link_or_reparse(info): raise ValueError(f"trusted managed root may not be a symlink or reparse point: {root}")
    return root


def configured_home(environ: Mapping[str, str] | None = None, fallback: Path | None = None) -> Path:
    source = os.environ if environ is None else environ
    raw = source.get("HOME")
    return Path(raw) if raw else (Path.home() if fallback is None else Path(fallback))


def user_paths() -> dict[str, Path]:
    home = trusted_root(configured_home())
    codex = trusted_root(Path(os.environ.get("CODEX_HOME", str(home / ".codex"))))
    return {"codex": codex, "agents": codex / "agents", "config": codex / "config.toml", "skills": home / ".agents" / "skills", "ledger": codex / "agent-system" / "ledger.jsonl", "journal": codex / "agent-system" / "rollback-journal.json", "lock": codex / "agent-system" / "install.lock"}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"invalid JSON {path.name}: {exc}")
    if not isinstance(value, dict): fail(f"JSON root must be an object: {path.name}")
    return value


def digest(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def catalog() -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, Any], str]:
    roles_doc, runtime_doc, governance = (read_json(REF / "roles.json"), read_json(REF / "runtime-profiles.json"), read_json(REF / "governance.json"))
    raw_roles = roles_doc.get("roles")
    if roles_doc.get("schema_version") != 1 or not isinstance(raw_roles, list): fail("invalid role catalog schema")
    profile_name, profiles = runtime_doc.get("default_profile"), runtime_doc.get("profiles")
    if runtime_doc.get("schema_version") != 1 or profile_name != "openai-gpt-5.6-balanced" or not isinstance(profiles, dict): fail("invalid runtime profile schema")
    profile = profiles.get(profile_name, {}); mapping = profile.get("roles")
    if not isinstance(mapping, dict): fail("default runtime mapping missing")
    required = {"name", "description", "matching", "execution_contract", "tool_boundary", "escalation_conditions"}
    roles: dict[str, dict[str, Any]] = {}
    types: set[str] = set()
    for raw in raw_roles:
        if not isinstance(raw, dict) or set(raw) != required or not isinstance(raw.get("name"), str): fail("incomplete role catalog entry")
        name = raw["name"]
        if name in roles or name not in mapping: fail("duplicate or unmapped role")
        runtime = mapping[name]
        if not isinstance(runtime, dict) or set(runtime) != {"model", "reasoning_effort", "sandbox_mode"}: fail("invalid runtime mapping")
        if runtime["sandbox_mode"] not in {"read-only", "workspace-write"}: fail("invalid sandbox")
        task_types = raw["matching"].get("task_types") if isinstance(raw["matching"], dict) else None
        if not isinstance(task_types, list) or not task_types or any(not isinstance(x, str) or x in types for x in task_types): fail("overlapping task type")
        types.update(task_types); roles[name] = {**raw, "runtime": runtime}
    luna = profile.get("mechanical_luna")
    if len(roles) != 8 or set(mapping) != set(roles) or luna != {"model":"gpt-5.6-luna","reasoning_effort":"high","sandbox_mode":"workspace-write"}: fail("catalog must retain eight mapped roles and mechanical Luna runtime")
    variant = governance.get("runtime_variant")
    if not isinstance(variant, dict) or variant.get("applies_to_role") != "worker" or not isinstance(variant.get("required_true_fields"), list): fail("invalid mechanical variant")
    if governance.get("runtime") != {"enabled": True, "max_depth": 1, "max_threads": 4}: fail("invalid thread bounds")
    return roles, governance, runtime_doc, digest(roles_doc, runtime_doc, governance)


def overlay(cwd: Path, base_hash: str) -> tuple[dict[str, Any] | None, Path | None]:
    for parent in (cwd.resolve(), *cwd.resolve().parents):
        path = parent / ".agents" / "agent-system" / "overlay.json"
        if not path.is_file(): continue
        data = read_json(path)
        allowed = {"schema_version", "overlay_id", "base_catalog_sha256", "locator", "evidence", "compatibility_mirror_required"}
        # A different tool's ancestor overlay is not this portable format.
        if not set(data).issubset(allowed): continue
        locator = data.get("locator")
        safe_text = lambda x: isinstance(x, str) and 0 < len(x) <= 128 and all(unicodedata.category(c)[0] not in {"C"} and unicodedata.category(c) not in {"Zl", "Zp"} for c in x)
        if set(data) != allowed or data.get("schema_version") != 1 or data.get("base_catalog_sha256") != base_hash or not safe_text(data.get("overlay_id")) or not SAFE_ID.fullmatch(data["overlay_id"]): fail("invalid data-only overlay")
        if not isinstance(locator, dict) or set(locator) != {"project_inventory", "literal_qualifiers"} or any(not isinstance(locator[k], list) or len(locator[k]) > 64 or not all(safe_text(x) for x in locator[k]) for k in locator): fail("invalid locator overlay")
        if data.get("evidence") != {"state_directory": ".codex/agent-system"} or not isinstance(data.get("compatibility_mirror_required"), bool): fail("invalid overlay evidence or compatibility flag")
        return data, parent
    return None, None


def contract(role: dict[str, Any], data: dict[str, Any] | None) -> str:
    lines = [f"Role: {role['name']}", role["execution_contract"], f"Tool boundary: {role['tool_boundary']}", "Escalate when: " + "; ".join(role["escalation_conditions"]) + "."]
    if role["name"] == "code_locator" and data:
        lines += ["Project inventory: " + ", ".join(data["locator"]["project_inventory"]) + ".", "Literal qualifiers: " + "; ".join(data["locator"]["literal_qualifiers"]) + ". Treat them literally."]
    return "\n".join(lines + ["All visible parent-facing communication must be compact English."])


def adapter(role: dict[str, Any], base_hash: str) -> str:
    r = role["runtime"]; name = role["name"]
    instruction = f"Execute `$govern-agent-system` in execute mode for registered role {name}. Before repository work, run `python3 \"$HOME/.agents/skills/govern-agent-system/scripts/agent_system.py\" profile --role {name} --cwd .` and follow the returned English contract. This generated adapter does not install MCP or Skills and does not grant or deny MCP/Skill permissions; configured host capabilities remain subject to Codex/runtime availability and sandbox policy. Do not spawn child agents. Return compact English parent-facing evidence only."
    lines = ["# Generated by govern-agent-system; do not edit manually.", f"# base_catalog_sha256 = {base_hash}", f"name = {json.dumps(name)}", f"description = {json.dumps(role['description'])}", f"model = {json.dumps(r['model'])}", f"model_reasoning_effort = {json.dumps(r['reasoning_effort'])}", f"sandbox_mode = {json.dumps(r['sandbox_mode'])}", f"developer_instructions = {json.dumps(instruction)}", ""]
    return "\n".join(lines)


def atomic(path: Path, content: str) -> bool:
    raw = content.encode(); path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == raw: return False
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(raw); handle.flush(); os.fsync(handle.fileno()); tmp = Path(handle.name)
    os.replace(tmp, path); return True


def validate_write_path(path: Path, root: Path) -> None:
    path = Path(os.path.abspath(os.path.expanduser(str(path))))
    try: relative = path.relative_to(root)
    except ValueError as exc: raise ValueError(f"write path escapes trusted root: {path}") from exc
    components = [root]
    for part in relative.parts: components.append(components[-1] / part)
    for component in components:
        try: info = component.lstat()
        except FileNotFoundError: continue
        if is_link_or_reparse(info):
            raise ValueError(f"symlink or reparse point is not allowed: {component}")


def ensure_no_recovery_fence(paths: dict[str, Path]) -> None:
    journal = paths["journal"]
    validate_write_path(journal, paths["codex"])
    if not journal.exists():
        return
    try: document = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc: raise ValueError(f"RECOVERY_REQUIRED: invalid recovery journal: {exc}") from exc
    if not isinstance(document, dict) or document.get("identity") != "govern-agent-system" or not isinstance(document.get("status"), str):
        raise ValueError("RECOVERY_REQUIRED: invalid recovery journal schema")
    if document["status"] != "recovered":
        raise ValueError(f"RECOVERY_REQUIRED: managed writes are fenced by journal status {document['status']}")


def render_agents_config(original: str, runtime: dict[str, Any]) -> str:
    try: parsed = tomllib.loads(original) if original else {}
    except tomllib.TOMLDecodeError as exc: raise ValueError(f"invalid TOML before mutation: {exc}")
    if not isinstance(parsed, dict) or ("agents" in parsed and not isinstance(parsed["agents"], dict)): raise ValueError("agents configuration must be a table")
    if re.search(r"""(?m)^\s*(?:agents|"agents"|'agents')\s*\.""", original): raise ValueError("unsupported dotted agents keys; refuse ambiguous merge")
    if set(runtime) != set(CONFIG_KEY_ORDER): raise ValueError("invalid managed agents key set")
    values = {
        key: str(runtime[key]).lower() if isinstance(runtime[key], bool) else str(runtime[key])
        for key in CONFIG_KEY_ORDER
    }
    lines = original.splitlines(keepends=True); out: list[str] = []; in_agents = False; found_agents = False

    def append_managed() -> None:
        if out and not out[-1].endswith(("\n", "\r")):
            out.append("\n")
        for key in CONFIG_KEY_ORDER:
            out.append(f"{key} = {values[key]}\n")

    for line in lines:
        table = re.match(r"^\s*\[([^]]+)\]\s*(?:#.*)?$", line)
        if table:
            if in_agents: append_managed()
            in_agents = table.group(1).strip() == "agents"
            found_agents |= in_agents
            out.append(line)
            continue
        managed = re.match(r"^\s*(enabled|max_depth|max_threads)\s*=", line) if in_agents else None
        if not managed: out.append(line)
    if in_agents:
        append_managed()
    elif not found_agents:
        if not original:
            out = ["[agents]\n"]
        else:
            if not original.endswith("\n"): out.append("\n")
            out += ["\n[agents]\n"]
        append_managed()
    rendered = "".join(out)
    try: tomllib.loads(rendered)
    except tomllib.TOMLDecodeError as exc: raise ValueError(f"invalid staged TOML: {exc}")
    return rendered


def generation_plan(cwd: Path) -> tuple[dict[str, Any], list[tuple[Path, str]]]:
    roles, governance, _, base_hash = catalog()
    data, _ = overlay(cwd, base_hash)
    paths = user_paths()
    try:
        ensure_no_recovery_fence(paths)
        validate_write_path(paths["config"], paths["codex"])
        for name in roles:
            validate_write_path(paths["agents"] / f"{name}.toml", paths["codex"])
        original = paths["config"].read_text(encoding="utf-8") if paths["config"].exists() else ""
        rendered_config = render_agents_config(original, governance["runtime"])
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    writes = [(paths["agents"] / f"{name}.toml", adapter(roles[name], base_hash)) for name in sorted(roles)]
    writes.append((paths["config"], rendered_config))
    return {
        "role_count": len(roles),
        "base_catalog_sha256": base_hash,
        "overlay_id": data.get("overlay_id") if data else None,
    }, writes


def generate(cwd: Path) -> dict[str, Any]:
    paths = user_paths()
    try:
        lock = managed_lock.acquire(paths["lock"], paths["codex"])
    except managed_lock.LockError as exc:
        fail(str(exc))
    try:
        try:
            ensure_no_recovery_fence(paths)
            metadata, writes = generation_plan(cwd)
        except ValueError as exc:
            fail(str(exc))
        changed: list[str] = []
        for path, content in writes:
            if atomic(path, content): changed.append(str(path))
        return {"ok": True, **metadata, "changed": changed}
    finally:
        managed_lock.release(lock)


def select(request: dict[str, Any], roles: dict[str, dict[str, Any]]) -> tuple[str, list[str]]:
    if not {"task_type", "known_target", "factual_uncertainty"}.issubset(request): fail("routing request requires task_type, known_target, factual_uncertainty")
    task, known, uncertain = request["task_type"], request["known_target"], request["factual_uncertainty"]
    if not isinstance(task, str) or not isinstance(known, bool) or not isinstance(uncertain, list) or any(x not in UNCERTAINTY for x in uncertain): fail("invalid routing facts")
    if known and uncertain: fail("ROUTING_CONFLICT: known target conflicts with factual uncertainty")
    owners = [name for name, role in roles.items() if task in role["matching"]["task_types"]]
    if len(owners) != 1: fail(f"UNMATCHED_TASK_TYPE: {task}")
    if task == "factual_lookup": return "code_locator", ["EXPLICIT_FACTUAL_LOOKUP", "LOCATOR_ONLY"]
    if not known: return "code_locator", ["TARGET_UNRESOLVED", "LOCATOR_FIRST"]
    if uncertain: return "code_locator", ["FACTUAL_UNCERTAINTY", "LOCATOR_FIRST"]
    return owners[0], ["MATCHED_ROLE_" + owners[0].upper()]


def variant(request: dict[str, Any], governance: dict[str, Any], role: str) -> str:
    candidate, rule = request.get("mechanical_worker"), governance["runtime_variant"]
    if role != "worker" or not isinstance(candidate, dict) or request.get("factual_uncertainty") != [] or request.get("known_target") is not True: return "standard"
    required = set(rule["required_true_fields"]) | {"task_category", "deterministic_check"}
    if set(candidate) == required | {"target"} and candidate["task_category"] in rule["eligible_task_categories"] and all(isinstance(candidate[x], str) and SAFE_ID.fullmatch(candidate[x]) for x in ("target", "deterministic_check")) and all(candidate[x] is True for x in rule["required_true_fields"]): return "mechanical_luna"
    return "standard"


def dispatch(cwd: Path, raw: str) -> dict[str, Any]:
    try: request = json.loads(raw)
    except json.JSONDecodeError: fail("request must be JSON")
    if not isinstance(request, dict): fail("request must be an object")
    roles, governance, runtime_doc, base_hash = catalog(); data, _ = overlay(cwd, base_hash)
    if request.get("parent_model") not in PARENT_MODELS or request.get("parent_reasoning_effort") not in PARENT_EFFORTS: fail("PARENT_NOT_ELIGIBLE")
    role, reasons = select(request, roles); active_variant = variant(request, governance, role)
    scope, frozen = request.get("scope", "workspace"), request.get("contract", request.get("task_type", "general"))
    if not isinstance(scope, str) or not SAFE_ID.fullmatch(scope) or not isinstance(frozen, str) or not SAFE_ID.fullmatch(frozen): fail("scope and contract must be compact identifiers")
    assignment = [f"Role: {role}", f"Owning scope: {scope}", f"Frozen contract: {frozen}", f"Skill: $govern-agent-system", f"Load merged profile: python3 \"$HOME/.agents/skills/govern-agent-system/scripts/agent_system.py\" profile --cwd . --role {role}"]
    runtime = runtime_doc["profiles"][runtime_doc["default_profile"]]["mechanical_luna"] if active_variant == "mechanical_luna" else roles[role]["runtime"]
    if active_variant == "mechanical_luna": assignment += [f"Bounded target: {request['mechanical_worker']['target']}", f"Validated deterministic check: {request['mechanical_worker']['deterministic_check']}", "Runtime variant contract: " + governance["runtime_variant"]["execution_contract"]]
    assignment.append("Return compact English evidence: outcome, files or symbols, verification, blocker, and next action.")
    return {"ok": True, "role": role, "runtime_variant": active_variant, "reuse_key": f"{role}:{active_variant}:{scope}:{frozen}:{digest(base_hash, data)[:12]}", **runtime, "reason_codes": ["PARENT_ELIGIBLE", *reasons], "assignment": "\n".join(assignment)}


def validate_event(event: dict[str, Any], roles: dict[str, dict[str, Any]], config_hash: str) -> None:
    if not set(event).issubset(EVENT_FIELDS) or not {"task_id", "task_hash", "role", "task_type", "result_status", "failure_class", "user_correction_category", "user_correction", "fallback", "config_hash"}.issubset(event): raise ValueError("event uses unsupported or sensitive fields")
    if not all(isinstance(event[k], str) and SAFE_ID.fullmatch(event[k]) for k in ("task_id", "task_type")) or not all(isinstance(event[k], str) and SHA256.fullmatch(event[k]) for k in ("task_hash", "config_hash")) or event["config_hash"] != config_hash: raise ValueError("invalid event identifiers")
    if event["role"] not in roles or event["result_status"] not in {"success", "failed", "blocked", "cancelled"} or event["failure_class"] not in {"none", "routing", "contract", "tool", "verification", "authority", "environment", "other"} or event["user_correction_category"] not in CORRECTIONS or event["fallback"] not in {"none", *roles} or not isinstance(event["user_correction"], bool): raise ValueError("invalid event enums")
    for key in ("duration_ms", "output_bytes"):
        if key in event and (not isinstance(event[key], int) or isinstance(event[key], bool) or not 0 <= event[key] <= 2147483647): raise ValueError("invalid numeric proxy")
    if "tool_counts" in event and (not isinstance(event["tool_counts"], dict) or set(event["tool_counts"]) - TOOLS or any(not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 100000 for v in event["tool_counts"].values())): raise ValueError("invalid tool counts")


def record(cwd: Path, raw: str) -> dict[str, Any]:
    try: event = json.loads(raw)
    except json.JSONDecodeError: fail("event must be JSON")
    roles, _, _, base_hash = catalog(); data, root = overlay(cwd, base_hash); config_hash = digest(base_hash, data)
    try: validate_event(event, roles, config_hash)
    except ValueError as exc: fail(str(exc))
    target = (root / data["evidence"]["state_directory"] / "ledger.jsonl") if data and root else user_paths()["ledger"]
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle: handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
    return {"ok": True, "recorded": True, "config_hash": config_hash, "ledger": str(target)}


def audit(cwd: Path) -> dict[str, Any]:
    roles, governance, _, base_hash = catalog(); data, _ = overlay(cwd, base_hash); paths = user_paths(); mismatches = []
    for name in roles:
        p = paths["agents"] / f"{name}.toml"
        if not p.is_file() or p.read_text(encoding="utf-8") != adapter(roles[name], base_hash): mismatches.append(str(p))
    try: config = tomllib.loads(paths["config"].read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError): config = {}
    agents_config = config.get("agents")
    good_config = isinstance(agents_config, dict) and all(
        key in agents_config and type(agents_config[key]) is type(value) and agents_config[key] == value
        for key, value in governance["runtime"].items()
    )
    return {"ok": not mismatches and good_config, "role_count": len(roles), "base_catalog_sha256": base_hash, "overlay_id": data.get("overlay_id") if data else None, "adapter_mismatches": mismatches, "config_ok": good_config}


def evaluate(cwd: Path) -> dict[str, Any]:
    roles, governance, _, base_hash = catalog(); data, _ = overlay(cwd, base_hash); cases: list[tuple[str, bool]] = []
    cases += [("eight_unique_roles", len(roles) == 8), ("parent_gate", governance["parent_gate"]["models"] == sorted(PARENT_MODELS) or set(governance["parent_gate"]["models"]) == PARENT_MODELS), ("strict_thread_bounds", governance["runtime"]["max_depth"] == 1), ("english_no_children", all("spawn child agents" in r["execution_contract"] for r in roles.values())), ("portable_locator", "MCP" in roles["code_locator"]["execution_contract"] and "Git" in roles["code_locator"]["tool_boundary"]), ("data_only_overlay", data is None or set(data) == {"schema_version", "overlay_id", "base_catalog_sha256", "locator", "evidence", "compatibility_mirror_required"})]
    for request, expected in [({"task_type":"implementation","known_target":True,"factual_uncertainty":[]}, "worker"), ({"task_type":"implementation","known_target":False,"factual_uncertainty":[]}, "code_locator"), ({"task_type":"factual_lookup","known_target":False,"factual_uncertainty":[]}, "code_locator")]: cases.append(("routing_" + expected, select(request, roles)[0] == expected))
    try: select({"task_type":"unknown","known_target":False,"factual_uncertainty":[]}, roles); rejected = False
    except SystemExit: rejected = True
    cases.append(("unknown_rejected", rejected))
    failed = [name for name, ok in cases if not ok]
    return {"ok": not failed, "passed": len(cases) - len(failed), "failed": failed, "base_catalog_sha256": base_hash}


def verify(cwd: Path) -> dict[str, Any]:
    first, second = generate(cwd), generate(cwd)
    audit_result, evaluation = audit(cwd), evaluate(cwd)
    return {"ok": bool(audit_result["ok"] and evaluation["ok"] and not second["changed"]), "generated": first["base_catalog_sha256"], "idempotent": not second["changed"], "audit": audit_result["ok"], "evaluate": evaluation["ok"]}


def locator_smoke() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); subprocess.run(["git", "init", "-q", str(root)], check=True); (root / "sample.txt").write_text("alpha\nbeta\n", encoding="utf-8"); subprocess.run(["git", "-C", str(root), "add", "."], check=True); subprocess.run(["git", "-C", str(root), "-c", "user.name=smoke", "-c", "user.email=smoke@example.invalid", "commit", "-qm", "smoke"], check=True)
        revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(); lines = (root / "sample.txt").read_text(encoding="utf-8").splitlines(); found = lines.index("beta") + 1
        return {"ok": bool(re.fullmatch(r"[a-f0-9]{40,64}", revision) and found == 2), "revision": revision, "items": [{"path":"sample.txt","status":"FOUND","line":found}, {"path":"missing.txt","status":"FILE_MISSING"}], "lookup_status":"PARTIAL"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--version", action="version", version=RELEASE_VERSION); sub = parser.add_subparsers(dest="command", required=True)
    for name in ("audit", "evaluate", "verify", "generate"): item = sub.add_parser(name); item.add_argument("--cwd", default=str(Path.cwd()))
    for name, arg in (("dispatch", "request"), ("record", "event")): item = sub.add_parser(name); item.add_argument("--cwd", default=str(Path.cwd())); item.add_argument(f"--{arg}", required=True)
    item = sub.add_parser("profile"); item.add_argument("--cwd", default=str(Path.cwd())); item.add_argument("--role", required=True)
    sub.add_parser("locator-smoke")
    args = parser.parse_args()
    if args.command == "profile":
        roles, _, _, base_hash = catalog(); data, _ = overlay(Path(args.cwd), base_hash)
        if args.role not in roles: fail("unknown role")
        print(contract(roles[args.role], data)); return
    result = {"audit": lambda: audit(Path(args.cwd)), "evaluate": lambda: evaluate(Path(args.cwd)), "verify": lambda: verify(Path(args.cwd)), "generate": lambda: generate(Path(args.cwd)), "dispatch": lambda: dispatch(Path(args.cwd), args.request), "record": lambda: record(Path(args.cwd), args.event), "locator-smoke": locator_smoke}[args.command]()
    print(json.dumps({**result, "release_version": RELEASE_VERSION}, sort_keys=True))
    if not result.get("ok", False): raise SystemExit(1)


if __name__ == "__main__": main()
