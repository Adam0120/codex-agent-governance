---
name: govern-agent-system
description: Use native Codex custom agents for bounded node delegation through eight fixed roles while the capable main agent retains decisions and final acceptance.
---

# Govern Agent System

Use this Skill from a capable Sol or Terra main agent at high-or-greater reasoning. Delegation is optional: use it only when an independent bounded specialist would materially improve speed, evidence, or safety.

## Roles

| Role | Use for |
|---|---|
| `default` | A bounded read-only advisory node that fits no specialist |
| `worker` | An already-settled implementation node |
| `explorer` | Bounded discovery or failure-triage evidence |
| `code_locator` | Exact revision-aware factual source locations |
| `cross_module_architect` | Cross-module contract evidence and candidate options |
| `systems_safety` | An exact parent-approved systems invariant or patch |
| `semantic_reviewer` | One advisory semantic and security review of an accumulated diff |
| `release_operator` | A parent-approved, revision-bound release runbook |

## Native delegation

1. Freeze the objective, owning scope, contract, safety boundary, and required evidence before delegating.
2. Select the smallest role that can execute or evidence the frozen node. The main agent retains architecture and product decisions, risk acceptance, integration, and final acceptance. Prefer no delegation when the slice is trivial, tightly coupled to the main thread, or lacks a stable contract.
3. Spawn the registered custom agent natively with a minimal-history assignment containing only: objective; owning repository or scope; frozen contract; safety boundary; acceptance checks; expected evidence.
4. Use one child by default. Reuse the same child agent by agent id for follow-up on the same repository and contract surface. Do not create overlapping writers. Parallelize only genuinely independent, time-consuming surfaces whose benefit justifies another agent.
5. Treat a refusal, failed safety gate, missing authority, or unresolved ownership as `STOP`. Do not retry by widening scope, changing roles to bypass the refusal, or elevating authority.

The packaged role adapters are self-contained. Relevant host-provided Skills or MCP tools may be used when available, but this system neither installs nor requires them. Children never spawn children and return compact evidence; the main agent owns sequencing, architecture and product decisions, risk acceptance, integration, user decisions, final acceptance, and final synthesis.
