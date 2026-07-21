---
name: govern-agent-system
description: Use native Codex custom agents for bounded node delegation through eight fixed roles while the capable main agent retains decisions and final acceptance.
---

# Govern Agent System

Use this Skill from a capable Sol or Terra main agent at high-or-greater reasoning. Delegation is optional: use it only when an independent bounded specialist would materially improve speed, evidence, or safety.

## Roles

| Role | Best-fit single node | Do not use for |
|---|---|---|
| `default` | Small bounded read-only advice that fits no specialist | Architecture design, implementation, or a broad project audit |
| `worker` | One already-settled behavior change in one owned surface, with one focused verification boundary | A feature train, cross-repository contract design, or a migration-plus-runtime-plus-client bundle |
| `explorer` | One bounded discovery question or failure-reproduction loop | Selecting a solution, editing, or a repository-wide “find everything” sweep |
| `code_locator` | Exact revision-aware locations for a fixed list of factual questions | Semantic judgment, recommendations, or broad source dumps |
| `cross_module_architect` | One frozen cross-module contract question, with evidence and candidate options | Choosing product behavior, implementing it, or reviewing an unfrozen moving diff |
| `systems_safety` | One exact parent-approved safety invariant or narrow patch | Discovering the invariant, accepting risk, or widening an authorization/migration boundary |
| `semantic_reviewer` | One frozen accumulated revision or narrow risk slice reviewed once | Implementing, approving/releasing, or reviewing an actively changing tree |
| `release_operator` | One parent-approved revision-bound runbook and mutation batch | Release planning, broad production investigation, or unbounded live repair |

## Dispatchability gate

Delegate only when the node has **one observable state transition or one evidence question**. Background narrative is context, not scope. A task is ready only when the parent can name all of the following:

1. A single objective and owner: repository/worktree plus baseline revision or immutable state.
2. A bounded surface: exact files or symbols, or a deliberately small search area; state explicit exclusions.
3. The permitted operation class: read-only evidence, one implementation change, one safety patch, or one release batch.
4. The frozen behavior/invariant, authority, and data-safety boundary; no unresolved product or architecture decision may be hidden in the task.
5. One acceptance boundary: exact check(s), expected observable result, and the evidence needed by the parent.

Split a request before dispatch when it combines discovery, design, implementation, review, or release; spans independent state machines; or couples a migration, protocol version, runtime change, and client integration. A writer may touch several files only when they are necessary for the same atomic behavior. Sequence a large feature as **map → freeze → implement one node → verify/review**, rather than assigning a whole vertical slice to one worker.

## Assignment description

Use a compact assignment with these labelled facts: **objective; owned repository/worktree and baseline; allowed files/symbols or search area; allowed operation and exclusions; frozen contract/invariant; authority and safety boundary; acceptance/verification; required evidence; terminal-state format**. Do not substitute a project name, milestone, or long transcript for these facts.

Every non-locator child returns a compact terminal envelope: **COMPLETE**, **PARTIAL**, or **STOP**; inspected/changed scope; resulting revision or state when relevant; verification performed or not performed; blocker or failed gate; and the next parent action. `code_locator` instead ends with its exact `Lookup` status from its richer factual status vocabulary.

## Capability progression

Do not treat a review finding as proof that a role or model is inadequate until the dispatchability gate was met. First shrink the node, reproduce the exact boundary, and add the missing acceptance signal. Increase capability by one supported level only when the same precise node repeatedly fails for reasoning quality rather than a tool, environment, authority, or scope problem. Reserve the highest-cost review capability for a frozen high-risk diff; never use it to compensate for an oversized implementation assignment.

## Native delegation

1. Pass the dispatchability gate: freeze the single objective, owning scope, contract, safety boundary, acceptance boundary, and required evidence before delegating.
2. Select the smallest role that can execute or evidence the frozen node. The main agent retains architecture and product decisions, risk acceptance, integration, and final acceptance. Prefer no delegation when the slice is trivial, tightly coupled to the main thread, or lacks a stable contract.
3. Spawn the registered custom agent natively with the compact assignment description above. Do not ask a child to infer its ownership, allowed operation, exclusions, or verification boundary from surrounding history.
4. Use one child by default. Reuse the same child agent by id only when the repository/worktree, baseline, frozen objective/invariant, allowed operation, and owned surface are unchanged; otherwise create a newly bounded node. Allow one active writer per worktree/file set. Parallel readers must use an immutable revision or finish before the writer changes their evidence base; the parent serializes dependent nodes. Parallelize only genuinely independent, time-consuming surfaces whose benefit justifies another agent.
5. Treat a refusal, failed safety gate, missing authority, or unresolved ownership as `STOP`. Do not retry by widening scope, changing roles to bypass the refusal, or elevating authority.

The packaged role adapters are self-contained. Relevant host-provided Skills or MCP tools may be used when available, but this system neither installs nor requires them. Children never spawn children and return compact evidence; the main agent owns sequencing, architecture and product decisions, risk acceptance, integration, user decisions, final acceptance, and final synthesis.
