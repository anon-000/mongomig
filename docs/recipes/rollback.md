# Roll back a bad release

**Situation:** release 1.4 went out with two migrations, and something's wrong. You want the
database back where 1.3 expects it.

## 1. Preview the rollback

```console
$ mongomig downgrade --dry-run

downgrade b6d8914ee98d  backfill user status   Risk: HIGH
  users  unset_field  $unset status  ~1,000 docs · collection scan · DELETES DATA
  reversible: yes · deletes data: YES · resumable: yes (checkpointed)
  why HIGH: deletes data without a backup (users.unset_field)

1 migration(s) · ~1,000 document writes · highest risk HIGH
Estimates only; nothing was changed.
```

Read the warning: downgrading the "backfill user status" migration removes the `status` field
it added, including values written since. That's usually right (1.3 doesn't know the field),
but it's data, so make the call consciously.

## 2. Downgrade to the revision release 1.3 shipped with

```console
$ mongomig downgrade 8937564747f6 --yes
Running downgrade b6d8914ee98d -> 166c65307e0b, backfill user status
  • unset_field users: 1,000 modified (1,000 matched, 1 batches)
  ✓ done in 20ms
Running downgrade 166c65307e0b -> 8937564747f6, seed plans
  ✓ done in 0ms
Reverted 2 revision(s).
```

```console
$ mongomig current
Database: app_prod
Current:  8937564747f6  add orders
Pending:  2
  - 166c65307e0b  seed plans
  - b6d8914ee98d  backfill user status
```

A target revision stays applied; everything after it is reverted, newest first. Without a
target, `downgrade` goes back one step; `downgrade base` reverts everything.

## 3. Roll back the application, then fix forward

Deploy 1.3. When 1.4 is fixed, `mongomig upgrade` applies the migrations again:

```console
$ mongomig upgrade --yes
Running upgrade 8937564747f6 -> 166c65307e0b, seed plans
  ✓ done in 2ms
Running upgrade 166c65307e0b -> b6d8914ee98d, backfill user status
  • backfill users: 1,000 modified (1,000 matched, 1 batches)
  ✓ done in 34ms
Applied 2 revision(s).
```

## Things to know

- **Irreversible migrations** (`reversible = False`) stop a downgrade. Restore from a backup,
  or `--force` to run what downgrade code exists and un-track the rest.
- **Deleted data comes back only if it was backed up.** A migration that used
  `unset_field(..., backup=True)` restores it on downgrade. See
  [Remove a field](remove-a-field.md).
- **Often, rolling forward is better:** a new migration that fixes the problem, instead of
  reverting data changes.
- Take a regular database backup before big releases, whatever the migrations say.
