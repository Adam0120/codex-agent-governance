---
name: govern-agent-system
description: Use native Codex custom agents for bounded delegation through eight fixed roles. Choose the smallest useful role, freeze the assignment, reuse the same child on the same surface, and stop on refusal or missing authority.
---

# Govern Agent System

Use this Skill from a capable Sol or Terra main agent at high-or-greater reasoning. Delegation is optional: use it only when an independent bounded specialist would materially improve speed, evidence, or safety.

## Roles

| Role | Use for |
|---|---|
| `default` | Explicit general read-only advice that fits no specialist |
| `worker` | A settled implementation slice |
| `explorer` | Bounded discovery or failure triage |
| `code_locator` | Exact revision-aware factual source locations |
| `cross_module_architect` | Ambiguous cross-module contracts or migration design |
| `systems_safety` | Concurrency, lifecycle, unsafe code, crypto, authorization, or durable state |
| `semantic_reviewer` | One final semantic and security review of an accumulated diff |
| `release_operator` | A parent-approved, revision-bound release or live activation |

## Native delegation

1. Freeze the objective, owning scope, contract, safety boundary, and required evidence before delegating.
2. Select the smallest role that owns the decision or work. Prefer no delegation when the slice is trivial, tightly coupled to the main thread, or lacks a stable contract.
3. Spawn the registered custom agent natively with a minimal-history English assignment containing only: objective; owning repository or scope; frozen contract; safety boundary; expected evidence.
4. Reuse the same child agent by agent id for follow-up on the same repository and contract surface. Do not create overlapping writers. Parallelize only genuinely independent, time-consuming surfaces.
5. Treat a refusal, failed safety gate, missing authority, or unresolved ownership as `STOP`. Do not retry by widening scope, changing roles to bypass the refusal, or elevating authority.

The packaged role adapters are self-contained. Relevant host-provided Skills or MCP tools may be used when available, but this system neither installs nor requires them. Children never spawn children and return compact English evidence; the main agent owns sequencing, user decisions, and final synthesis.
