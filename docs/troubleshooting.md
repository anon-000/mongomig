# Troubleshooting

Start with `mongomig doctor`. It checks config, connection, permissions, lock and failed runs.

| Message | Cause | Fix |
|---|---|---|
| `No mongomig.yaml found` | not in a project directory | `cd` into it, `--config PATH`, or `mongomig init` |
| `Environment variable MONGODB_URI is not set` | config uses `${MONGODB_URI}` | `export MONGODB_URI=...` |
| `Cannot connect to MongoDB at host:port` | server unreachable | check the URI, network, `directConnection=true` for a local single-node replica set |
| `Multiple heads` (exit 4) | two branches each added a revision | `mongomig merge -m "merge"` (or `revision --head REV`) |
| `No models registered` | `env.py` has `target_metadata = None` | register models in `env.py`; check with `mongomig models` |
| `N model change(s) have no migration` (`validate`, `diff --check`) | models edited without autogenerate | `mongomig revision --autogenerate -m "..."`, commit revision + snapshot |
| `schema_snapshot.json changed since head revision` | snapshot hand-edited or mis-merged | restore it from git; as a last resort `baseline --force` |
| `storage profile changed` | `storage=` in env.py differs from the snapshot | intentional? generate a migration (types change); otherwise revert |
| `These migrations need confirmation` (exit 1) | a pending migration can delete data / is irreversible | `mongomig plan`, then `upgrade --yes` |
| `Another MongoMig run holds the migration lock` (exit 5) | concurrent runner or crashed one | wait, `--lock-timeout 300`, or let it expire (`doctor` shows the owner) |
| `Applied migration file(s) were modified` (exit 6) | an applied revision was edited | revert; if harmless, `mongomig stamp <head>` |
| `Migration ... failed during upgrade in create_index on users: DuplicateKeyError` | unique index over duplicate data | fix duplicates (plan warns beforehand), `mongomig upgrade` again |
| `Cannot downgrade: ... reversible = False` | irreversible revision in the path | restore from backup, or `--force` to un-track |
| `Decimal needs bson.Decimal128 or a codec` (warning) | `storage="python"` with a type PyMongo can't encode | convert before inserting, use Beanie / `storage="json"`, or `type_overrides` |
| `database has revisions with no file here` | database migrated by newer code | deploy the newer code; don't downgrade blindly |
| `not fully simulated: the migration uses ctx.unsafe_db` (plan) | raw database access | expected; the impact of that part isn't estimated |
