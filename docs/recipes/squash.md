# Squash old history

**Situation:** after a year, every new environment replays dozens of revisions. Replace them
with one.

## 1. Preview

```console
$ mongomig squash --dry-run
Would squash 4 revisions (06f0062b7e5c … b6d8914ee98d)
  schema: 2 collection(s), 2 index(es), 0 validator(s); 1 data operation(s) skipped
  review: 166c65307e0b line 18: may insert documents (insert_many): seed data to carry over?
```

## 2. Squash

```console
$ mongomig squash -m "squash 2026 history"
Squashed 4 revisions (06f0062b7e5c … b6d8914ee98d)
  new revision: 1c0a08941591 → migrations/versions/20261003_1852_1c0a08941591_squash_2026_history.py
  replaced files moved to: migrations/versions/_squashed/1c0a08941591/
  schema: 2 collection(s), 2 index(es), 0 validator(s); 1 data operation(s) skipped
  review: 166c65307e0b line 18: may insert documents (insert_many): seed data to carry over?

Next: review the new file, run `mongomig validate`, commit. Databases that ran the old revisions adopt the squash on their next `upgrade`. Delete migrations/versions/_squashed/1c0a08941591/ once every environment has upgraded past b6d8914ee98d.
```

The old files move to an archive, and the new revision stands in for them:

```text
migrations/versions/20261003_1852_1c0a08941591_squash_2026_history.py
migrations/versions/_squashed/1c0a08941591/20261003_1852_06f0062b7e5c_create_users.py
migrations/versions/_squashed/1c0a08941591/20261003_1852_166c65307e0b_seed_plans.py
migrations/versions/_squashed/1c0a08941591/20261003_1852_8937564747f6_add_orders.py
migrations/versions/_squashed/1c0a08941591/20261003_1852_b6d8914ee98d_backfill_user_status.py
```

```console
$ mongomig history
<base> -> 1c0a08941591 (head, squash of 4), squash 2026 history
```

The squashed revision contains the **schema** the old ones built up (collections, indexes,
validators). It's for building *new* databases, which have no documents to change.

```python
"""squash 2026 history

Revision: 1c0a08941591
Revises: <base>
Created: 2026-10-03 18:52:28 UTC

Squashed from 4 revisions (06f0062b7e5c … b6d8914ee98d):
  06f0062b7e5c  create users
  8937564747f6  add orders
  166c65307e0b  seed plans
  b6d8914ee98d  backfill user status

A new, empty database runs this revision instead of the ones above: it creates the
collections, indexes and validators they build up. A database that already ran them
adopts this revision on its next upgrade without running anything.
Data operations skipped (nothing to change in an empty database): 1.

Needs review (see TODO(review) in upgrade):
  - 166c65307e0b line 18: may insert documents (insert_many): seed data to carry over?
"""

revision = "1c0a08941591"
down_revision = None
branch_labels = None
depends_on = None
replaces = ("06f0062b7e5c", "8937564747f6", "166c65307e0b", "b6d8914ee98d")
reversible = True
snapshot_hash = "sha256:68c4fc7693eaf1005a648b6e287239d1e23cea1b3d9d735e8b84c04f8258996a"
mongomig_format = 1


def upgrade(ctx):
    ctx.ops.create_collection("users")
    ctx.ops.create_index("users", "email", name="users_email_unique", unique=True)
    ctx.ops.create_collection("orders")
    ctx.ops.create_index("orders", [("user_id", 1), ("created_at", -1)], name="orders_by_user")

    # TODO(review): the replaced revisions also contain code squash can't carry over:
    #   - 166c65307e0b line 18: may insert documents (insert_many): seed data to carry over?


def downgrade(ctx):
    ctx.ops.drop_index("orders", "orders_by_user")
    # ctx.ops.drop_collection("orders")  # would delete its data
    ctx.ops.drop_index("users", "users_email_unique")
    # ctx.ops.drop_collection("users")  # would delete its data
```

## 3. Act on the review item

Squash flagged `seed plans`: that migration inserted reference data, which a new database
still needs. Copy it into the squashed `upgrade()`:

```python
    ctx.collection("plans").insert_many([{"_id": "free"}, {"_id": "pro"}])
```

## 4. Every database keeps working

**Production** already ran all four old revisions: it adopts the squash and runs nothing.

```console
$ mongomig upgrade
  • adopted squash 1c0a08941591: the 4 revisions it replaces are applied
Adopted squash 1c0a08941591; nothing to run.
```

**Staging** had run only two of them: it runs the other two from the archive, then adopts.

```console
$ mongomig upgrade
Running upgrade 8937564747f6 -> 166c65307e0b, seed plans
  ✓ done in 10ms
Running upgrade 166c65307e0b -> b6d8914ee98d, backfill user status
  • backfill users: 0 modified (0 matched, 0 batches)
  ✓ done in 13ms
  • adopted squash 1c0a08941591: the 4 revisions it replaces are applied
Applied 2 revision(s).
```

**A new database** runs the single squashed revision:

```console
$ mongomig upgrade
Running upgrade <base> -> 1c0a08941591, squash 2026 history
  • created collection users
  • index users.users_email_unique ready
  • created collection orders
  • index orders.orders_by_user ready
  ✓ done in 19ms
Applied 1 revision(s).
```

## 5. Later: delete the archive

Keep `migrations/versions/_squashed/<revision>/` until **every** environment has upgraded past
the squash; a half-migrated database needs it. Then delete it.

!!! note "Rules"
    A squash always starts at the first revision, and the range must be a straight line (no
    branches or merges). If a squash would *run* on a database that already has documents
    (data but no migration history), `upgrade` asks first: use `mongomig stamp <squash>` if
    that database is already up to date.
