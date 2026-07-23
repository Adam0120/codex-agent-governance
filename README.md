# Govern Agent System

[简体中文](README.zh-CN.md)

A minimal native Codex multi-agent setup: one installer, one Skill, and eight custom-agent configurations.

## Install

From this checkout, run:

```bash
python3 install.py
```

The script copies `SKILL.md` to `~/.codex/skills/govern-agent-system/` and the eight TOMLs from `.codex/agents/` to `~/.codex/agents/`. Set `CODEX_HOME` to install into another Codex home:

```bash
CODEX_HOME=/path/to/.codex python3 install.py
```

Restart Codex before starting a new task so its custom-agent registry reloads. The installer replaces only the bundled Skill and the eight role files with matching names. It never reads, writes, or creates `config.toml`.

## Included roles

| Role | Model | Purpose |
|---|---|---|
| `default` | Luna | Bounded read-only advice |
| `worker` | Luna | Settled implementation slice |
| `explorer` | Luna | Focused discovery or triage |
| `code_locator` | Spark | Revision-aware factual locations |
| `cross_module_architect` | Terra | Cross-module contract evidence |
| `systems_safety` | Terra | Parent-approved safety patch |
| `semantic_reviewer` | Sol | Frozen-diff review |
| `release_operator` | Terra | Approved revision-bound release batch |

## Dispatch policy

This Skill is enabled only when you explicitly invoke `$govern-agent-system` or ask to use it; task complexity, cross-module work, parallelism, review, and release work do not trigger it automatically. Once enabled, the main agent owns scope, contracts, integration, risk decisions, and final acceptance. Delegate one bounded, independently verifiable node to the smallest suitable role.

For a linear stage, reuse one child. When two or more genuinely independent nodes are ready, start the smallest useful parallel batch without waiting for a user request. Writers must have non-overlapping files or worktrees; dependent or conflicting work stays serial. While children run, the main agent continues safe coordination and integration work, waiting only for an actual dependency.

The full operating guidance is in [SKILL.md](SKILL.md); each role's execution boundary is in `.codex/agents/*.toml`.
