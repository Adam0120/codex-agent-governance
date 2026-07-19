---
name: govern-agent-system
description: Govern portable Codex agent delegation, role-profile inspection, deterministic adapter generation, compact privacy-safe outcome records, and data-only project overlays. Use before authorized subagent dispatch, when generating or verifying `.codex/agents` adapters, or when auditing a governed project. Do not use for model mutation, automatic upgrades, transcript collection, MCP setup, or unrestricted agent registration.
---

# Govern Agent System

Use this Skill as a small, fail-closed control plane for a fixed role catalog.

## Workflow

1. Run `scripts/agent_system.py dispatch --cwd <cwd> --request '<json>'` before an authorized delegation. Supply the parent model and effort, task type, target certainty, and factual uncertainty. Reuse the returned `reuse_key` for the same frozen surface.
2. Run `profile --cwd <cwd> --role <role>` and include the returned English contract in the assignment. Children execute one registered role and never spawn children.
3. Run `audit` and `evaluate` before catalog or overlay changes. Unknown task types and contradictory facts fail closed.
4. Run `generate --cwd <cwd>` after checks. It writes deterministic user adapters and a bounded `[agents]` configuration. Run it twice to establish idempotence.
5. Record only allowlisted compact metadata with `record`; use `verify` for a fresh-process smoke check.

## Boundaries

Treat `references/roles.json` as role semantics and `references/runtime-profiles.json` as runtime mapping. The default `openai-gpt-5.6-balanced` profile may be replaced only by a catalog edit and review, never by a project overlay.

Project overlays are inventory data only: identity/base digest, locator projects and literal qualifiers, evidence directory, and whether a compatibility mirror is required. They cannot alter roles, runtime, tools, policy, depth, sandbox, or model settings.

The locator is portable without MCP or CodeGraph. It uses Git, `rg` when present (otherwise a bounded standard-library scan), readable paths, and line verification. Optional integrations must remain opt-in and non-blocking.
