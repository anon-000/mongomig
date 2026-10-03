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
| `revision -m/--message MSG [--head REV] [--rev-id ID]` | new empty revision on the current head |
| `revision -m MSG --autogenerate [--rename C.OLD:NEW ...]` | generate from model changes; updates the snapshot |
| `diff [--check] [--rename C.OLD:NEW ...]` | model changes since the last migration; `--check` exits 1 if any |
| `baseline [-m MSG] [--force]` | adopt an existing database: snapshot the models, empty revision |
| `models` | declared schema of registered models + storage warnings |
| `heads` | head revision(s) |
| `history` | revisions, newest first |
| `merge [REV ...] [-m MSG]` | join heads (default: all) into one merge revision |
| `squash [TO] [-m MSG] [--dry-run]` | replace the revisions from the base up to `TO` (default: head) with one; old files go to `versions/_squashed/<rev>/` |
| `validate [--database] [--import/--no-import] [--strict]` | CI checks; `--database` adds checksums / failed runs |

## Database

| Command | |
|---|---|
| `current [--check]` | applied / pending / failed / modified; `--check` exits 1 unless fully up to date |
| `plan [TARGET] [--steps N]` | pending migrations with estimated impact and risk; writes nothing |
| `upgrade [TARGET] [--steps N] [--dry-run] [--yes] [--lock-timeout S]` | apply; `TARGET` = `head` (default), `heads`, or a revision |
| `downgrade [TARGET] [--steps N] [--dry-run] [--yes] [--force] [--lock-timeout S]` | revert one step (default), back to `TARGET` (kept applied), or `base` |
| `resume [--yes] [--lock-timeout S]` | continue failed/interrupted migrations from their checkpoints (then the rest, like `upgrade`) |
| `stamp REV ... \| heads \| base` | mark as applied without running (baselines, checksum repair) |
| `inspect [COLL ...] [--sample-size N \| --sample-percent P \| --full-scan]` | observed schema: fields, types, presence, indexes, validator |
| `drift [COLL ...] [--check] [--strict] [--sample-size N \| --sample-percent P \| --full-scan]` | models vs stored data: missing fields, unexpected types/fields, index and validator drift; `--check` exits 1 over thresholds |
| `backups [--drop REV\|NAME ...] [--yes]` | list / drop `backup=True` copies |
| `doctor` | environment diagnostics: versions, config, connection, server, permissions, lock, backups |

Revisions can be given as full ids, unique prefixes (4+ characters) or branch labels.
`-m` is short for `--message` everywhere it's accepted (`revision`, `merge`, `baseline`,
`squash`).

## Example output

What the main commands print, captured from a real project (more in the
[recipes](recipes/index.md)).

### `current`

```console
$ mongomig current
Database: orders_db
Current:  <base> (no revisions applied)
Pending:  1
  - ab4e4d589e2a  totals as numbers
Failed:   ab4e4d589e2a: upgrade: WriteError: Failed to parse number '12,50' in $convert with no onError value: Did not consume whole string.
          resumes from checkpoint: orders (120,000 documents done)
Fix the cause, then `mongomig resume` (or `upgrade`).
```

### `plan`

```console
$ mongomig plan
Migration plan for shop

  4e3c7332253a  initial schema                           applied
  e4f1c38b9d65  add status and plan                      applied
  19f6d1e042c8  rename name                              applied
  f91f7962b9d4  age is an int                            applied
  83eb731318d1  drop legacy_flags                        applied
  f8389ca45387  unique email                             pending

f8389ca45387  unique email   Risk: HIGH
  users  drop_index    email_1                      
  users  create_index  users_email_unique (unique)  50,000 docs
         ⚠ will fail: duplicate values exist, e.g. {'email': 'dup@example.com'}
  reversible: yes · deletes data: no · resumable: yes (checkpointed)
  why HIGH: expected to fail (users.create_index)

1 migration(s) · ~0 document writes · highest risk HIGH
Estimates only; nothing was changed.
```

### `inspect`

```console
$ mongomig inspect customers
Collection: customers
Documents: 10,000 random sample of ~20,000  (0.06s)

Fields (nested presence is relative to the parent object):
  field       presence   types
  _id          100.00%   objectId
  name         100.00%   string
  email        100.00%   string
  created_at   100.00%   date 86.0% · string 14.1%
  phone         24.78%   string
  vip            1.86%   bool

Indexes:
  _id_ (_id ↑)
  customers_email_unique (email ↑, unique)

Validator: none

Based on 10,000 sampled documents; documents outside the sample may differ. Use --full-scan for a complete analysis.
```

### `drift`

```console
$ mongomig drift

CUSTOMERS  10,000 sampled of ~20,000 documents
  ✗ created_at  expected date · observed string 14.0%  > 0.5%
                hint: convert the values in a migration, or widen the model's type
  ✗ vip         not in the model · present in 2.02% of documents  > 1%
                hint: add it to the model, or remove it: ctx.ops.unset_field(..., backup=True)

2 failed, 0 below threshold
Based on samples: documents outside the sample may differ. Use --full-scan for an exact answer.
```

### `validate`

```console
$ mongomig validate
✓ configuration         mongomig.yaml
✓ revision files        6 revision(s), graph is consistent
✓ single head           f8389ca45387
✓ migration imports     all revision files import cleanly
✓ env.py / models       1 collection(s) registered
✓ models vs migrations  every model change has a migration

0 failed, 0 warning(s)
```

### `doctor`

```console
$ mongomig doctor
✓ versions           mongomig 0.4.2, Python 3.12.14, pymongo 4.18.2, pydantic 2.13.5, beanie 2.2.0
✓ configuration      /srv/myapp/mongomig.yaml
✓ connection string  localhost:27017
✓ models             1 collection(s), storage profile 'python'
✓ connection         database 'shop', ping 40 ms
✓ server version     MongoDB 7.0.43
✓ topology           replica set 'rs0'
- permissions        not authenticated (authentication disabled?)
✓ tracking           6 revision(s) applied
✓ lock               free

0 failed, 0 warning(s)
```

### `stamp`

```console
$ mongomig stamp heads
Stamped 1 revision(s) as applied (no migration code was run). Current: 1c0a08941591
```

### `backups`

```console
$ mongomig backups
  __mongomig_backup_83eb731318d1                                   50,000 docs       3.7 MB

Drop with: mongomig backups --drop <revision-or-name> --yes
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | validation failure: `diff --check`, `validate`, `current --check`, `drift --check`, unknown revision, confirmation required |
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
  checkpoint_collection: __mongomig_checkpoints
execution:
  confirm: destructive               # destructive | always | never
  batch_size: 1000
  sleep_ms_between_batches: 0
  max_retries: 3
  lock_ttl_seconds: 300
sampling:
  size: 10000                        # default sample for `inspect` and `drift`
drift:
  thresholds:                        # % of sampled documents/values before a finding fails
    missing_field_percent: 0.5
    unexpected_type_percent: 0.5
    unexpected_field_percent: 1
```

Unknown keys are rejected. Environment overlays (`mongomig.<env>.yaml`) are deep-merged.
