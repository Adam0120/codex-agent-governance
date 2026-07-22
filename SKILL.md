---
name: govern-agent-system
description: Automatically govern native Codex custom-agent delegation with coordinator-first, cost-aware role selection. Load before spawning or reusing agents, and for multi-surface, multi-repository, cross-module, parallel, review, or release work; delegation remains bounded.
---

# Govern Agent System

Use this Skill from any capable main model at high-or-greater reasoning, or whenever the user explicitly requests it. Do not gate loading on a model family or model name. Coordinator-first dispatch is the default: bounded implementation, search-heavy, repetitive, long-running, or independently verifiable work goes to the smallest lower-cost registered child; the main agent works directly only for trivial or tightly coupled work smaller than handoff, or when delegation is unavailable. When two or more ready nodes are genuinely independent, launch the smallest useful parallel batch without waiting for the user to request multiple agents.

## Automatic loading

Load this Skill before spawning or reusing any native custom agent. It should also be selected automatically when a request has two or more independent work surfaces, spans repositories or modules, needs a cross-module contract, asks for parallel agent work, or coordinates an accumulated review or release. Do not wait for the user to name this Skill or remind the main agent.

Loading the Skill does not authorize delegation by itself. Apply the dispatchability gate and work directly when no bounded specialist provides material benefit.

## Coordinator-first ownership

The main agent owns scope, contracts, sequence, integration, review, risk acceptance, user decisions, and final acceptance. It may directly locate code, make in-scope edits, run focused checks, fix ordinary findings, update task documentation, and integrate results only when the work is trivial or tightly coupled, smaller than handoff, or delegation is unavailable. Otherwise dispatch the bounded node to the smallest lower-cost registered child.

Coordinator-first ownership is explicit: the main agent owns scope, contracts, sequence, integration, review, risk acceptance, user decisions, and final acceptance. A child receives only a bounded implementation, search/evidence, repetitive, long-running, or independently verifiable node within that contract.

Treat a coherent delivery stage as one ownership unit. Its owner may perform the necessary location, implementation, focused verification, ordinary corrections, and required documentation without creating one child per phase. Loading another Skill changes how the current owner works; it does not by itself create a design, gate, review, checkpoint, or documentation subtask.

Use direct ownership only when the exception above applies. For one linear coherent stage, keep one child and reuse it for related implementation, focused verification, review fixes, and documentation; do not create one child per phase. When a stage exposes multiple independent ready nodes, create one child per non-overlapping node instead of serializing them behind the first child.

While children run, the main agent continues scheduling, contract decisions, integration preparation, and any safe parent-owned work. It must not enter a wait-only loop while another independent node is ready. Wait only for the child result that the next decision or dependent operation actually needs.

Native delegation is an optimization, not a prerequisite. If the current task does not expose native spawn or reuse tools, continue directly within the task's existing authority and workspace; missing optional delegation capability is never by itself `STOP`, a goal blocker, or a reason to repeat capability audits. The main agent holds the writer lease until it explicitly grants a bounded surface to a child, so the absence of a child never leaves an otherwise authorized worktree ownerless.

If a native spawn returns `unknown agent_type`, treat that role as unavailable in this task's session-local registry, not as evidence that the role's TOML, model, or assignment is invalid. Do not retry the same unavailable role in that task. Continue directly or with an already registered smallest-fit role, and preserve the frozen node. A fresh top-level task after Codex has reloaded the custom-agent registry is required before newly installed roles can be selected; an already-running task may retain its prior role set.

Some task surfaces expose native `spawn_agent` but omit its `agent_type` parameter. Inspect the actual call schema before the first dispatch: a role name in a task name or assignment never loads a TOML adapter. When `agent_type` is absent, enter **profile compatibility mode** rather than silently inheriting the parent model. Pass the selected role's exact profile through the available `model` and `reasoning_effort` parameters: `code_locator` → `gpt-5.3-codex-spark`/high; `default`, `worker`, and `explorer` → `gpt-5.6-luna`/high; `cross_module_architect`, `systems_safety`, and `release_operator` → `gpt-5.6-terra`/medium; `semantic_reviewer` → `gpt-5.6-sol`/medium. This direct override is a compatibility exception, not a capability escalation. Keep `fork_turns="none"` or the smallest useful positive window, and carry the selected role's operation, exclusions, and terminal contract into the self-contained assignment.

Compatibility mode does **not** load the TOML sandbox or developer profile. Do not describe such a child as role-bound or sandboxed, and do not trust a read-only label as an enforcement boundary: the parent retains the writer lease, freezes the precise evidence baseline, and checks the owned files or revision before accepting the result. If the exact profile pair is unsupported by the current spawn schema, continue directly rather than silently inheriting or selecting a higher model. Existing children cannot be retroactively rebound; let a mature, correctly bounded node finish and apply compatibility mode only to its next newly bounded node. Keep this bookkeeping internal—do not emit user-facing dispatch or model-binding logs.

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

Delegate only when the node has **one observable state transition or one evidence question**. This constrains a child assignment; it does not require the main task to be decomposed into one child per lifecycle phase. Background narrative is context, not scope. A task is ready only when the parent can name all of the following:

1. A single objective and owner: repository/worktree plus baseline revision or immutable state.
2. A bounded surface: exact files or symbols, or a deliberately small search area; state explicit exclusions.
3. The permitted operation class: read-only evidence, one implementation change, one safety patch, or one release batch.
4. The frozen behavior/invariant, authority, and data-safety boundary; no unresolved product or architecture decision may be hidden in the task.
5. One acceptance boundary: exact check(s), expected observable result, and the evidence needed by the parent.

Split before delegation only where ownership, authority, safety boundaries, or genuinely independent state machines differ. Do not split a coherent stage solely because it includes discovery, implementation, tests, ordinary fixes, and documentation. A writer may touch several files when they are necessary for the same atomic behavior and may carry that behavior through focused verification.

For large or ambiguous work, the main agent may first resolve the contract, then delegate one coherent implementation node. This is a decision boundary, not a mandatory `design → locate → implement → gate → review → fix → regate → document` pipeline.

## Assignment description

Use a compact assignment with these labelled facts: **objective; repository/worktree plus baseline revision; exact files or symbols (or narrow search area); allowed operation and exclusions; frozen contract or invariant; acceptance boundary and required evidence**. Add authority or safety details only when material. Do not substitute a project name, milestone, or long transcript for these facts.

Every non-locator child returns a compact terminal envelope: **COMPLETE**, **PARTIAL**, or **STOP**; inspected/changed scope; resulting revision or state when relevant; verification performed or not performed; blocker or failed gate; and the next parent action. `code_locator` instead ends with its exact `Lookup` status from its richer factual status vocabulary.

## Capability progression

Do not treat a review finding as proof that a role or model is inadequate until the dispatchability gate was met. First shrink the node, reproduce the exact boundary, and add the missing acceptance signal. A refusal, failed safety gate, missing authority, or external blocker remains `STOP` and is not a capability-escalation signal. Otherwise, if the same child returns `PARTIAL` or a scope/ambiguity `STOP` twice in succession for the same task, do not keep re-prompting it unchanged: reduce the owned scope when the slice is too broad, clarify the objective/invariant/expected result when the target is ambiguous, or do both. Only when the re-bounded task still fails for reasoning quality rather than a tool, environment, authority, or scope problem may the parent create a newly bounded node at one higher supported model or reasoning level. Escalate a Luna-backed role to Terra without creating a duplicate role. Do not increase both task breadth and capability in the same retry. Reserve the highest-cost review capability for a frozen high-risk diff; never use it to compensate for an oversized implementation assignment.

## Verification and review economy

Let the current owner run focused verification for its coherent stage. A separate gate agent is justified only when the check is long-running and can proceed independently, or when independent evidence is required. Do not create a gate agent for short commands the main agent or writer can run directly.

Use `semantic_reviewer` once for an accumulated frozen high-risk diff, an explicit review request, or a meaningful release boundary. Ordinary changes rely on owner verification and a final diff sanity check. When review finds in-scope issues, reuse the same writer to fix them and rerun only affected checks. Do not require a second full review unless the correction changes a security, persistence, concurrency, migration, deployment, irreversible, or public-contract boundary.

Have the main agent or existing writer update required documentation after verification. Do not create a documentation-only child unless the documentation is itself a substantial independent deliverable.

## Native delegation

1. Pass the dispatchability gate: freeze the single objective, owning scope, contract, safety boundary, acceptance boundary, and required evidence before delegating.
2. Select the smallest role that can execute or evidence the frozen node. Prefer `code_locator`, `worker`, or `explorer` for bounded lower-cost work; reserve `systems_safety`, `cross_module_architect`, `semantic_reviewer`, and `release_operator` for their named risk boundaries. The main agent retains architecture and product decisions, risk acceptance, integration, user decisions, and final acceptance. Prefer no delegation when the slice is trivial, tightly coupled to the main thread, or lacks a stable contract.
3. Inspect the actual native spawn schema before calling it. When it exposes `agent_type`, pass the selected role through that field; a task name or label does not bind a role. For an ordinary role-bound spawn, omit direct `model` and `reasoning_effort` overrides so the registered adapter supplies both. When `agent_type` is absent, use profile compatibility mode: explicitly pass the selected role's configured model and effort, state its bounded role contract in the assignment, and do not claim its TOML sandbox/profile was loaded. In either mode use `fork_turns="none"` or the smallest useful positive history count; never use an omitted or `all` full-history fork because it inherits the parent model and reasoning effort. Direct model or effort overrides are otherwise reserved for the explicit one-level capability escalation above. Keep the assignment self-contained; do not ask a child to infer its ownership, allowed operation, exclusions, or verification boundary from surrounding history.
4. Schedule by dependency, not by an artificial one-child limit. For a linear coherent stage, use one bounded child and reuse it for follow-up work. When two or more independent ready nodes have separate owned surfaces, launch the smallest useful parallel batch immediately; read-only nodes may share an immutable revision, and writers must have non-overlapping file sets or worktrees. Reuse each child by id while its repository/worktree, frozen objective/invariant, allowed operation, and owned surface remain stable; that child's own accepted edits do not invalidate reuse. Reuse the same writer for review fixes, focused re-verification, and related documentation. Create a new node only for a genuinely different objective, owner, operation class, or independent review boundary. The parent continues its own non-conflicting coordination and integration work while children run, and waits only at an actual dependency boundary. Allow one active writer per worktree/file set; serialize dependent work and conflicting writes. Parallelize only genuinely independent surfaces whose benefit justifies another agent.
5. Treat a refusal, failed safety gate, missing authority, or unresolved ownership as `STOP`. Do not retry by widening scope, changing roles to bypass the refusal, or elevating authority.

The packaged role adapters are self-contained. Relevant host-provided Skills or MCP tools may be used when available, but this system neither installs nor requires them. Children never spawn children and return compact evidence; the main agent owns sequencing, architecture and product decisions, risk acceptance, integration, user decisions, final acceptance, and final synthesis.
