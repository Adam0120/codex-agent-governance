# Changelog

## 0.2.3 — 2026-07-21

- Added a schema-aware compatibility path for task surfaces that expose `spawn_agent` but omit `agent_type`: instead of accidentally inheriting the parent model, ordinary dispatches explicitly pass the fixed role model/effort profile. Native role binding remains preferred whenever `agent_type` is available.
- Made the boundary explicit: compatibility dispatch does not load the TOML sandbox or developer profile, never masquerades as a bound role, keeps the parent writer lease and evidence checks, and does not emit user-facing model-binding telemetry. Mature existing children are not restarted solely to rebind; the rule applies to their next newly bounded node.

## 0.2.2 — 2026-07-21

- Raised the managed horizontal concurrency cap from four to six child threads, matching the current Codex default while retaining `max_depth = 1`; current-format manifests remain upgradeable or uninstallable using their exact manifest-proven positive thread and non-negative depth values rather than a release-number mapping.
- Made role binding explicit: native spawns must pass `agent_type`, keep ordinary model/effort selection in the registered adapter, and avoid full-history inheritance; task labels alone no longer masquerade as role selection. Skill loading now supports any capable high-or-greater main model or an explicit user request.
- Restored explicit per-role model and effort defaults based on v0.2.1, while moving the bounded `default`, `worker`, and `explorer` roles to `gpt-5.6-luna` at high effort. `code_locator` remains Spark/high, critical execution roles remain Terra/medium, and `semantic_reviewer` remains Sol/medium.
- Kept the existing eight roles: the main agent freezes the objective and acceptance boundary, then Luna-backed roles escalate to Terra only after repeated reasoning failure. No separate Luna role or user-visible dispatch/binding ledger is required.
- Made current-format compatibility independent of release-number ordering: verified v0.2-format sources can skip versions or carry a higher version than the CLI, while exact schema and provenance remain mandatory.
- Added snapshot-backed atomic `uninstall`, removing only manifest-proven managed files and configuration keys while preserving user agents, unrelated Codex configuration, and MCP settings; removed the v0.1 migration path.
- Made transaction recovery durable across interruption and process death: every replacement plan and cleanup artifact is journaled before mutation, every nonterminal journal fences writers, and exact rollback removes transaction debris. Proven dead-owner locks are safely reclaimable both for explicit journal recovery and for the no-journal edges before journal creation or after journal close; the replacement owner rechecks the journal and cleans only the reserved staging namespace. Manifest and snapshot schema/provenance integers now require exact JSON scalar types rather than Python equality.
- Added a publishable `codex-agent-governance` Python entry point with a single-source wheel payload, enabling `uvx ...@latest install` after publication without duplicating installer logic.

## 0.2.1 — 2026-07-20

- Rebalanced the eight native roles for lower child-agent cost: six roles now use `gpt-5.6-terra` at medium effort, `code_locator` keeps `gpt-5.3-codex-spark` at high, and advisory `semantic_reviewer` keeps `gpt-5.6-sol` at medium.
- Narrowed every child to a frozen node and returned architecture and product decisions, risk acceptance, integration, and final acceptance to a Sol/Terra high-or-greater main agent.
- Made one child the default, retained `max_threads = 4` and `max_depth = 1`, preserved all role names and sandboxes, and removed language-specific runtime wording.
- Added managed v0.2.0-to-v0.2.1 role replacement and byte-exact rollback coverage without changing unrelated Codex or MCP configuration.


## 0.2.0 — 2026-07-20

- Replaced controller-mediated dispatch with one concise Skill and eight canonical, self-contained native custom-agent TOMLs; removed the dispatch-only Luna variant, runtime profiles, overlays, generation, evaluation, verification, and telemetry interfaces.
- Simplified installation to direct copy only, removed `install --link`, and retained fail-closed locks, collision checks, atomic promotion, private snapshots, recovery fencing, no-follow/reparse/hard-link defenses, and exact rollback.
- Added a tested managed v0.1.2 migration that replaces controller-era runtime artifacts while preserving non-agent configuration, legacy ledger bytes as inert data, existing snapshots, permissions, MCP configuration, and byte-exact rollback.
- Corrected native Codex compatibility: standalone agent TOMLs are auto-discovered, fresh config writes only `agents.max_threads` and `agents.max_depth`, and proven released v0.1.0–v0.1.2 upgrades remove the legacy installer-owned `agents.enabled` while exact rollback restores it.

## 0.1.2 — 2026-07-19

- Security: restrict installer-owned state and snapshot directories on POSIX, and restrict snapshot/configuration/manifest/journal/optional-ledger files regardless of source mode or umask.
- Added read-only permission diagnostics plus post-lock, descriptor-rooted, no-follow remediation for validated legacy managed state; sensitive hard links and unknown entries fail closed, while Windows retains reparse defenses without claiming POSIX ACL equivalence.

## 0.1.1 — 2026-07-19

- Fixed the post-v0.1.0 CI YAML, explicit `HOME` and cross-platform alias handling, Windows managed-link update/provenance, Windows-only link normalization, and lexical link identity.
- Added `--version` diagnostics and `release_version` to compatible machine-readable checks.
- Documented MCP/Skill permission boundaries for generated adapters, the dependency-free Spark locator boundary, and release consistency regressions.

## 0.1.0

- Initial portable governance control-plane release.
