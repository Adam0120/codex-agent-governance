# Changelog

## 0.1.2 — 2026-07-19

- Security: restrict installer-owned state and snapshot directories on POSIX, and restrict snapshot/configuration/manifest/journal/optional-ledger files regardless of source mode or umask.
- Added read-only permission diagnostics plus post-lock, descriptor-rooted, no-follow remediation for validated legacy managed state; sensitive hard links and unknown entries fail closed, while Windows retains reparse defenses without claiming POSIX ACL equivalence.

## 0.1.1 — 2026-07-19

- Fixed the post-v0.1.0 CI YAML, explicit `HOME` and cross-platform alias handling, Windows managed-link update/provenance, Windows-only link normalization, and lexical link identity.
- Added `--version` diagnostics and `release_version` to compatible machine-readable checks.
- Documented MCP/Skill permission boundaries for generated adapters, the dependency-free Spark locator boundary, and release consistency regressions.

## 0.1.0

- Initial portable governance control-plane release.
