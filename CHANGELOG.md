# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.1] — 2026-10-03

### Added
- Documentation site at https://anon-000.github.io/mongomig/ (MkDocs Material, built from
  `docs/` and published to GitHub Pages on every docs change to `main`). It has search,
  navigation, dark mode and an "edit this page" link.
- `make docs` (live preview) and `make docs-build`, plus a `docs` extra.
- CI builds the docs with `--strict`, so broken links or anchors fail the pull request.

### Changed
- README and PyPI "Documentation" links point to the docs site.
- Docs: completed the contributor layout map; comparison rows for `alembic check`, squash and
  drift.

## [0.4.0] — 2026-10-03

### Added
- `mongomig squash [TO] [-m MSG] [--dry-run]` (PRD §54) replaces the revisions from the base
  up to `TO` with one squashed revision (`replaces = (...)`).
  - The squashed revision builds the schema of the replaced chain (collections, indexes,
    validators) for new databases. It's derived by reading the `ctx.ops` calls and keeping
    their net effect; data operations are skipped.
  - Code that can't be carried over is reported as `TODO(review)`: possible seed-data
    inserts, schema changes outside `ctx.ops`, `ctx.ops` calls in loops or conditions or with
    computed arguments, and `ctx.unsafe_db`.
  - Replaced files move to `versions/_squashed/<rev>/`, and later revisions that still name
    replaced ids are linked to the squash, so no applied file changes.
  - Databases that ran all replaced revisions adopt the squash on the next `upgrade`; partially
    migrated databases run the missing revisions from the archive first; nested squashes work.
  - `upgrade` asks for confirmation before running a squash on a database that already holds
    documents.
- `history` marks squashes and shows parents as the graph resolves them.
- Docs: "Squashing old revisions" in the production guide; CLI, concepts and troubleshooting
  updates.

## [0.3.0] — 2026-10-03

### Added
- **Resumable migrations** (PRD §24). Every batched `ctx.ops` operation checkpoints its
  progress in `__mongomig_checkpoints`. After a failure or a killed process, a re-run skips
  completed operations and continues after the last completed batch, even for
  non-idempotent updates such as `$inc`. Checkpoints are discarded if the migration file
  changed, are ignored if an operation's filter or update changed, and are deleted on success.
- `ctx.batches(collection, filter, projection=..., batch_size=..., transactional=False)`:
  resumable custom loops in `_id` order. They are at-least-once by default; with
  `transactional=True` each batch commits together with its checkpoint (exactly-once).
- `ctx.transaction()` (PRD §34): `with ctx.transaction() as session:` commits at the end of
  the block and aborts if it raises. It needs a replica set and gives a clear error on a
  standalone server.
- `mongomig resume`, and `mongomig current` now shows where interrupted migrations will
  resume.
- Config: `migrations.checkpoint_collection`.

### Fixed
- Resuming a batched scan after a dropped cursor no longer skips documents whose `_id` has a
  different BSON type (e.g. string ids after numeric ones).

## [0.2.0] — 2026-10-03

### Added
- `mongomig drift`: compares the registered models with sampled documents (PRD §30/§66) and
  reports:
  - missing required fields, unexpected types (with storage-profile hints) and fields that
    aren't in the model, including nested objects and arrays;
  - declared indexes that are missing or different (text-index and collation defaults are
    understood), undeclared indexes, managed validators that are missing or changed, and
    registered collections that don't exist.

  Data findings fail above `drift.thresholds` in `mongomig.yaml`. `--check` exits 1 for CI and
  scheduled jobs, and `--strict` gives zero tolerance. Sampling options match `inspect`, and
  a warning appears when migrations are still pending.
- Python API: `mongomig.check_drift()`.
- Docs: "Monitoring drift" in the production guide; CLI, API and troubleshooting updates.

## [0.1.1] — 2026-09-25

### Fixed
- README links to the documentation and examples were relative, so they were broken on the
  PyPI project page; they now point to GitHub.

### Added
- README badges; PyPI sidebar links (Documentation, Changelog, Source).
- `docs/comparison.md`: MongoMig for Alembic users and for teams using hand-written scripts.
- `CONTRIBUTING.md`, `SECURITY.md`, issue and pull request templates.

## [0.1.0] — 2026-09-25

First stable-track release: the MVP defined in the PRD is complete.

### Added
- `mongomig validate`: one command for CI. It checks configuration, revision files and
  imports, a single head, that every model change has a migration, and snapshot consistency.
  `--database` adds applied-checksum, failed-run and unknown-revision checks, and `--strict`
  turns type-mapping warnings into failures.
- `mongomig doctor`: environment diagnostics covering versions, configuration, connection,
  server version and topology, clock skew, **the connected user's privileges** (required and
  optional actions), failed runs, lock holder, and leftover backups.
- `plan` flags operations on sharded collections.
- Documentation in `docs/`: getting started, concepts, writing migrations, autogenerate,
  production guide (incl. minimum permissions), CLI reference, Python API, troubleshooting.
- Example projects `examples/fastapi_pydantic` and `examples/fastapi_beanie`, tested in CI.

### Changed
- Autogenerate: under a **strict** managed validator, new fields defaulting to `None` are
  backfilled (strict validation rejects updates to documents missing a required field).
- Generated code wraps long literals more accurately.

## [0.1.0a2] — 2026-09-25

### Added
- Production safety (Milestone 5):
  - `mongomig plan`, `upgrade --dry-run` and `downgrade --dry-run` run migrations in recording
    mode. Reads hit MongoDB; writes are recorded with document estimates, collection-scan
    detection, duplicate checks for new unique indexes, and counts of documents a new
    validator rejects. Each migration gets a risk label (LOW / MEDIUM / HIGH) with reasons.
  - `upgrade` asks for confirmation (or `--yes`) before migrations that can delete data or
    are irreversible, detected by reading the code rather than running it. Configurable with
    `execution.confirm: destructive | always | never`.
  - Backups: `unset_field(..., backup=True)`, `drop_collection(..., backup=True)`,
    `restore_field`, `restore_collection`, and `mongomig backups [--drop]`. Autogenerated
    suggestions for removed fields/collections now use backups.
  - `ctx.dry_run` for migration code that needs to know.
- Python API: `upgrade(..., yes=True)`, `upgrade_to_head(yes=True)`.

### Fixed
- `unset_field` and `rename_field` ignored a user `filter` on the same field
  (`unset_field("users", "legacy", filter={"legacy": 0})` removed `legacy` from **every**
  document). Filters are now combined with `$and`.

## [0.1.0a1] — 2026-09-25

First alpha: the full models → diff → autogenerate → upgrade workflow.

### Added
- Diff engine (Milestone 4): models vs `schema_snapshot.json`, covering collections, fields
  (nested objects and arrays), types (int/long as one family), nullability,
  required/optional, enums, indexes and validators. Every change is classified as SAFE,
  WARNING, REQUIRES_DATA_MIGRATION, MANUAL_REVIEW or DESTRUCTIVE.
- Rename hints (similarity-based) and explicit `--rename COLLECTION.OLD:NEW`.
- `mongomig revision --autogenerate`: generates `ctx.ops` code (backfills with model
  defaults, renames, indexes, validators) and rewrites the snapshot. Risky or ambiguous changes
  are emitted as commented `TODO(review)` blocks; data is never deleted automatically.
- `mongomig diff [--check]` for local review and CI.
- `mongomig baseline` to adopt MongoMig on an existing database.
- Warnings when the snapshot was edited after the head revision, or the storage profile changed.
- Schema engine (Milestone 3): `MongoMetadata` registry, `@collection` decorator,
  `Index`, explicit `register()`, and `register_beanie()` (reads Beanie `Settings`,
  `Indexed(...)`, `keep_nulls`, `use_revision`).
- Pydantic → BSON type mapping with storage profiles (`python`, `json`, `beanie`, plus
  `by_alias`/`exclude_none`/`exclude_unset`/`type_overrides`). Warns about types PyMongo
  cannot store.
- Generated `$jsonSchema` validators (`validator="auto"`).
- Schema inference from documents (sample / percentage / full scan) with per-field presence
  and type distribution, map detection, indexes and validator.
- Commands: `mongomig models`, `mongomig inspect`.
- Snapshot model (`schema_snapshot.json`) with a deterministic format and content hash.
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
