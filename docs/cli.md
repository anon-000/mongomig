# CLI reference

```text
mongomig [--config PATH] [--env NAME] [--json] [--verbose] [--version] COMMAND ...
```

| Global option | |
|---|---|
| `-c, --config PATH` | config file (default: `mongomig.yaml` found upwards from the cwd; env `MONGOMIG_CONFIG`) |
| `-e, --env NAME` | merge `mongomig.<NAME>.yaml` over the base config (env `MONGOMIG_ENV`) |
| `--json` | one JSON document on stdout (also accepted after the command); warnings on stderr |
| `-v, --verbose` | more detail (paths, error details) |

## Project and revisions (offline)

| Command | |
|---|---|
| `init [DIR] [--migrations-dir NAME]` | create `mongomig.yaml` and `migrations/`; refuses to overwrite |
| `revision -m MSG [--head REV] [--rev-id ID]` | new empty revision on the current head |
| `revision -m MSG --autogenerate [--rename C.OLD:NEW ...]` | generate from model changes; updates the snapshot |
| `diff [--check] [--rename C.OLD:NEW ...]` | model changes since the last migration; `--check` exits 1 if any |
| `baseline [-m MSG] [--force]` | adopt an existing database: snapshot the models, empty revision |
| `models` | declared schema of registered models + storage warnings |
| `heads` | head revision(s) |
| `history` | revisions, newest first |
| `merge [REV ...] [-m MSG]` | join heads (default: all) into one merge revision |
| `validate [--database] [--no-import] [--strict]` | CI checks; `--database` adds checksums / failed runs |

## Database

| Command | |
|---|---|
| `current [--check]` | applied / pending / failed / modified; `--check` exits 1 unless fully up to date |
| `plan [TARGET] [--steps N]` | pending migrations with estimated impact and risk; writes nothing |
| `upgrade [TARGET] [--steps N] [--dry-run] [--yes] [--lock-timeout S]` | apply; `TARGET` = `head` (default), `heads`, or a revision |
| `downgrade [TARGET] [--steps N] [--dry-run] [--yes] [--force] [--lock-timeout S]` | revert one step (default), back to `TARGET` (kept applied), or `base` |
| `stamp REV ... \| heads \| base` | mark as applied without running (baselines, checksum repair) |
| `inspect [COLL ...] [--sample-size N \| --sample-percent P \| --full-scan]` | observed schema: fields, types, presence, indexes, validator |
| `backups [--drop REV\|NAME ...] [--yes]` | list / drop `backup=True` copies |
| `doctor` | environment diagnostics: versions, config, connection, server, permissions, lock, backups |

Revisions can be given as full ids, unique prefixes (4+ characters) or branch labels.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | validation failure: `diff --check`, `validate`, `current --check`, unknown revision, confirmation required |
| 2 | execution failure: a migration raised, connection failed, irreversible downgrade refused |
| 3 | configuration error |
| 4 | revision conflict: multiple heads, cycle, missing parent, duplicate id |
| 5 | lock held by another runner |
| 6 | checksum mismatch: an applied revision file was modified |

## Configuration (`mongomig.yaml`)

```yaml
database:
  uri: ${MONGODB_URI}                # ${VAR} and ${VAR:-default}
  name: ${MONGODB_DATABASE:-app}     # optional if the URI has a database
  server_selection_timeout_ms: 5000
migrations:
  directory: migrations
  tracking_collection: __mongomig_migrations
  lock_collection: __mongomig_lock
execution:
  confirm: destructive               # destructive | always | never
  batch_size: 1000
  sleep_ms_between_batches: 0
  max_retries: 3
  lock_ttl_seconds: 300
sampling:
  size: 10000                        # default for `inspect`
```

Unknown keys are rejected. Environment overlays (`mongomig.<env>.yaml`) are deep-merged.
