# Changelog

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
