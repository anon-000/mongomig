# Coming from Alembic or hand-written scripts

## From Alembic

MongoMig deliberately feels like Alembic. Most concepts map one to one:

| Alembic | MongoMig | Notes |
|---|---|---|
| `alembic init` | `mongomig init` | |
| `alembic.ini` | `mongomig.yaml` | plus `mongomig.<env>.yaml` overlays (`--env`) |
| `env.py` + `target_metadata = Base.metadata` | `env.py` + `target_metadata = MongoMetadata.default()` | models register via `@collection`, `register()` or `register_beanie()` |
| `revision -m` / `--autogenerate` | `revision -m` / `--autogenerate` | |
| `op.add_column`, `op.create_index`, ... | `ctx.ops.backfill`, `ctx.ops.create_index`, ... | `ctx.collection(name)` for plain PyMongo |
| `upgrade head`, `downgrade -1` | `upgrade`, `downgrade` (one step) or `downgrade --steps N` | |
| `heads`, `history`, `current`, `merge`, `stamp` | same | |
| `alembic_version` table | `__mongomig_migrations` collection | also stores status, checksum, who/where/commit |
| `--sql` (offline mode) | `plan` / `--dry-run` | recorded against live data, with document estimates and risk |

What's different, because MongoDB is different:

- **Autogenerate compares models with a committed snapshot**, not with the database. SQL has a
  catalog to reflect; MongoDB only has documents, and a sample of them isn't a reliable
  schema. The snapshot makes diffs deterministic and reviewable.
- **Data changes are first-class.** Adding a column with a default is DDL in SQL; in MongoDB
  existing documents must be backfilled. Autogenerate writes those backfills, batched and
  re-runnable.
- **No DDL transactions.** A failed migration isn't rolled back automatically, so `ctx.ops`
  operations are idempotent and a re-run continues where it stopped.
- **Storage profiles.** The stored BSON type depends on how the app serialises models
  (`model_dump()` vs `jsonable_encoder`), so you declare it.
- **Safety extras:** a distributed lock, confirmation before destructive migrations,
  restorable backups, checksums of applied files, `inspect` for the real data.

## From hand-written scripts

Many MongoDB projects evolve their schema with ad-hoc scripts (`scripts/fix_users.py`,
`update_many` in a shell). That works until it doesn't:

| Hand-written scripts | MongoMig |
|---|---|
| "Was this script run on production?" | `mongomig current`: applied / pending / failed per environment |
| two people run it at once | distributed lock; the second runner waits or fails |
| script dies halfway through 4M documents | failure recorded; batched `ctx.ops` resume on re-run |
| someone edits a script after it ran | checksum mismatch stops the next upgrade |
| model changed, nobody wrote the backfill | `mongomig validate` fails CI |
| "how many documents will this touch?" | `mongomig plan`: estimates, collection scans, index-build failures |
| rollback is another ad-hoc script | `downgrade`, generated alongside `upgrade`; backups for deletions |
| indexes created by the app at startup | indexes versioned in migrations, created once, reviewed |

Adopting MongoMig on an existing database takes three commands: `mongomig init`, register
your models in `env.py`, then `mongomig baseline`. Nothing in the database changes. Future
changes go through `mongomig diff` and `revision --autogenerate`.

## When MongoMig is *not* the right tool

- **Schemaless by design.** If documents are intentionally free-form, there's little to
  diff; plain versioned scripts may be enough (you still get tracking and locking by writing
  migrations by hand).
- **Non-Python services.** MongoMig reads Pydantic/Beanie models; other languages can still use
  hand-written migrations, but not autogenerate.
- **Online resharding and cluster operations.** Those belong to MongoDB's own tooling;
  MongoMig flags sharded collections but doesn't manage shard keys.
