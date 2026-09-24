# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Migration engine (Milestone 2): `upgrade` (to `head`, `heads`, a revision, or `--steps N`),
  `downgrade` (one step, `--steps N`, to a revision, or `base`; asks for confirmation),
  `merge`, `stamp`, and `current --check`.
- `ctx.ops`: idempotent, batched operations: `create_index`, `drop_index`,
  `create_collection`, `drop_collection`, `rename_collection`, `set_validator`,
  `remove_validator`, `backfill`, `unset_field`, `rename_field`. Includes progress with
  rate/ETA and retries on transient errors.
- Distributed migration lock with heartbeat, using the server clock (`--lock-timeout`).
- Checksum verification of applied revisions (exit code 6); failed/interrupted run tracking;
  run metadata (host, user, environment, git commit).
- Irreversible migrations (`reversible = False`) block downgrades unless `--force`.
- Python API: `mongomig.upgrade()`, `downgrade()`, `upgrade_to_head()`,
  `aupgrade_to_head()` (FastAPI lifespan), `MigrationContext`, `Reporter`.

### Changed
- GitHub Actions updated to Node 24 based versions.

## [0.1.0.dev0] — 2026-09-25

### Added
- Project foundation (Milestone 1): packaging, CLI, configuration with `${VAR}` interpolation
  and environment overlays, credential redaction, MongoDB connection handling.
- Revision files with random 12-hex ids, AST-based loading (no code execution for offline
  commands), checksums, and a revision graph with heads, branches, merges, prefix resolution.
- Migration tracking collection (`__mongomig_migrations`).
- Commands: `init`, `revision`, `heads`, `history`, `current`; `--json` output everywhere.
- Docker Compose MongoDB (single-node replica set) and CI for Python 3.11–3.13 × MongoDB 6/7/8.
