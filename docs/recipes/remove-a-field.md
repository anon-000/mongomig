# Remove a field (and delete its data safely)

**Situation:** `legacy_flags` is no longer used. Removing it from the model is safe on its own:
MongoMig never deletes data automatically. If you also want the data gone, do it with a
**backup**, so you can undo it.

## 1. Remove it from the model and generate

```console
$ mongomig revision --autogenerate -m "drop legacy_flags"
Detected:

USERS
  - legacy_flags   WARNING  removed from the model; existing data is kept (not deleted)

1 WARNING
Generated 83eb731318d1 → migrations/versions/20261003_1851_83eb731318d1_drop_legacy_flags.py
  updated: migrations/schema_snapshot.json
Review the file, then run `mongomig upgrade`.
```

```python
def upgrade(ctx):
    # legacy_flags was removed from the model; existing data is kept.
    # To delete it from all documents, keeping a restorable backup:
    # ctx.ops.unset_field("users", "legacy_flags", backup=True)
    pass


def downgrade(ctx):
    # ctx.ops.restore_field("users", "legacy_flags")
    pass
```

## 2. Opt in to deleting, with a backup

Uncomment both lines (and remove the `pass` lines):

```python
def upgrade(ctx):
    # legacy_flags was removed from the model; existing data is kept.
    # To delete it from all documents, keeping a restorable backup:
    ctx.ops.unset_field("users", "legacy_flags", backup=True)


def downgrade(ctx):
    ctx.ops.restore_field("users", "legacy_flags")
```

`backup=True` copies each value to `__mongomig_backup_<revision>` before removing it, so
the migration stays **reversible**.

## 3. Check and apply

```console
$ mongomig plan
Migration plan for shop

  4e3c7332253a  initial schema                           applied
  e4f1c38b9d65  add status and plan                      applied
  19f6d1e042c8  rename name                              applied
  f91f7962b9d4  age is an int                            applied
  83eb731318d1  drop legacy_flags                        pending

83eb731318d1  drop legacy_flags   Risk: LOW
  users  unset_field  $unset legacy_flags (backup → __mongomig_backup_83eb731318d1)  ~50,000 docs · collection scan
  reversible: yes · deletes data: no · resumable: yes (checkpointed)

1 migration(s) · ~50,000 document writes · highest risk LOW
Estimates only; nothing was changed.
```

`deletes data: no`: with a backup, nothing is lost, so `upgrade` doesn't ask for
confirmation. (Without `backup=True`, `plan` shows `DELETES DATA` and `upgrade` asks first, or
needs `--yes` in scripts.)

```console
$ mongomig upgrade
Running upgrade f91f7962b9d4 -> 83eb731318d1, drop legacy_flags
  unset_field users: 35,000 / ~50,000 (70.0%) · 17,085 docs/s · elapsed 2.0s · ETA 878ms
  unset_field users: 50,000 / ~50,000 (100.0%) · 17,165 docs/s · elapsed 2.9s
  • unset_field users: 50,000 modified (50,000 matched, 50 batches)
  ✓ done in 2.9s
Applied 1 revision(s).
```

## 4. Changed your mind? Roll back

```console
$ mongomig downgrade --dry-run

downgrade 83eb731318d1  drop legacy_flags   Risk: LOW
  users  restore_field  legacy_flags ← __mongomig_backup_83eb731318d1  ~50,000 docs
  reversible: yes · deletes data: no · resumable: yes (checkpointed)

1 migration(s) · ~50,000 document writes · highest risk LOW
Estimates only; nothing was changed.
```

```console
$ mongomig downgrade --yes
Running downgrade 83eb731318d1 -> f91f7962b9d4, drop legacy_flags
  restore_field users: 41,000 / ~50,000 (82.0%) · 20,270 docs/s · elapsed 2.0s · ETA 444ms
  restore_field users: 50,000 / ~50,000 (100.0%) · 20,055 docs/s · elapsed 2.5s
  • restore_field users: 50,000 values restored
  ✓ done in 2.6s
Reverted 1 revision(s).
```

Every value is back: `documents with legacy_flags after downgrade: 50000`.

## 5. Clean up the backup when you're sure

```console
$ mongomig backups
  __mongomig_backup_83eb731318d1                                   50,000 docs       3.7 MB

Drop with: mongomig backups --drop <revision-or-name> --yes
```

```console
$ mongomig backups --drop 83eb731318d1 --yes
Dropped 1 backup(s).
```

!!! warning
    Drop a backup only once every environment has run the migration and you're sure you
    won't downgrade. After that, the downgrade has nothing left to restore.
