# Production guide

## How to run migrations

Recommended: a **separate deploy step** before the new application version rolls out.

```text
CI:      mongomig validate
Deploy:  mongomig --env production plan     (optional: review / attach to the release)
         mongomig --env production upgrade --yes
         roll out the application
```

- **Kubernetes**: a Job (or Helm pre-upgrade hook / Argo CD PreSync hook) running
  `mongomig upgrade --yes`, with the lock timeout if several may start
  (`--lock-timeout 300`).
- **Docker**: `pip install mongomig` in the image; run the same command.
- **FastAPI lifespan** (`await aupgrade_to_head()`): convenient for development and small
  deployments. All workers can call it; one migrates while the others wait. It refuses
  migrations that can delete data unless `yes=True`.

Readiness guard: `mongomig current --check` exits 1 if anything is pending, failed or modified.

## Environments and secrets

```yaml
# mongomig.yaml
database:
  uri: ${MONGODB_URI}
  name: ${MONGODB_DATABASE:-app}
```

`mongomig.production.yaml` is merged over it with `--env production` (or `MONGOMIG_ENV`).
Keep credentials in the environment; MongoMig warns about passwords in config files and never
prints full connection strings. TLS and replica-set options go in the URI as usual.

## Permissions

| Commands | Minimum role on the database |
|---|---|
| `current`, `inspect`, `plan`, `--dry-run`, `validate --database` | `read` |
| `upgrade`, `downgrade`, `stamp` | `readWrite` |
| migrations that manage validators (`set_validator`, `validator="auto"`) | `readWrite` + `dbAdmin` (or a role with `collMod`) |
| `drop_collection`, `rename_collection`, backups of collections | `readWrite` covers them |

`mongomig doctor` checks the connected user's privileges and names what's missing.

## Before running: plan

```bash
mongomig --env production plan
```

Runs pending migrations in recording mode against production data (reads only) and reports
per migration: operations, estimated documents, collection scans, unique indexes that would fail
on duplicates, documents a new validator rejects, whether data is deleted, reversibility, and
a risk label with reasons:

- **HIGH**: deletes data without backup, is expected to fail, crashed in the dry run, or
  rewrites ≥ 1M documents with a collection scan
- **MEDIUM**: ≥ 100k documents, index build on ≥ 1M documents, irreversible, sharded
  collection, custom code (estimates), or not fully simulated
- **LOW**: the rest

Estimates are taken against current data; later migrations in a chain are approximate.

## Confirmation

`upgrade` asks before running migrations that can delete data (`unset_field`/`drop_collection`
without `backup=True`, `delete_*`, `drop()`) or are irreversible. It reads the migration code
to decide, it doesn't run it. Non-interactive runs need `--yes`. Configure per environment:

```yaml
# mongomig.production.yaml
execution:
  confirm: always    # destructive (default) | always | never
```

## Locking

`upgrade`, `downgrade` and `stamp` take a lock in `__mongomig_lock`. Expiry uses the server
clock; a heartbeat renews it while migrations run (`execution.lock_ttl_seconds`, default 300).
A second runner fails with exit code 5, or waits with `--lock-timeout SECONDS`. A crashed
runner's lock expires by itself; `mongomig doctor` shows who holds it.

## Large collections

- `ctx.ops` data operations run in `_id`-ordered batches (`execution.batch_size`, default
  1000) with retries on transient errors (`execution.max_retries`).
- Throttle with `execution.sleep_ms_between_batches` to protect production load.
- Progress shows processed / estimated total, rate and ETA.
- Write filters that stop matching migrated documents, and index them when possible (plan
  flags collection scans).
- Build big indexes in their own revision so a failure doesn't hold up data changes.

## When a migration fails

The error names the revision, operation, collection and documents processed so far; the
tracking record is marked `failed` (`mongomig current` shows it).

1. Fix the cause (data or migration code; the migration hasn't been applied, so editing it is
   fine).
2. Run `mongomig upgrade` again. `ctx.ops` operations are idempotent: completed batches aren't
   redone.

A process killed mid-run leaves status `running`; it's treated the same way. Transactions are
not used automatically: a failure mid-migration leaves earlier batches applied, which is why
migrations should be re-runnable.

## Rolling back

```bash
mongomig downgrade --dry-run      # preview
mongomig downgrade --yes          # one step; or a target revision / base
```

Irreversible revisions stop the downgrade (use backups, or `--force` to un-track). Data removed
with `backup=True` comes back via `restore_field` / `restore_collection` in `downgrade`. Take
a regular database backup before large migrations regardless.

## Checksums and stamp

Editing an applied revision makes `upgrade` stop (exit 6). Revert the edit. If it was harmless
(a comment), `mongomig stamp <current-head>` re-records checksums. `stamp` marks exactly the
given revisions (and their ancestors) as applied without running anything; `stamp base`
clears tracking.

## Sharded clusters

`plan` flags operations on sharded collections: broad updates fan out to every shard. Changes
to shard keys are never autogenerated; write them by hand following MongoDB's resharding
guidance.
