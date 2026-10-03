# Two developers changed models at the same time

**Situation:** Alice adds `nickname` on her branch and Bob adds `notes` on his. Both ran
`revision --autogenerate`. Now both branches are being merged. This happens in every team.

## What you'll see

Alice's and Bob's revisions both point at the same parent. After merging both branches, git
reports conflicts in the model and in the snapshot (if the two changes are close together in
the file):

```text
$ git merge bob
Auto-merging app/models.py
CONFLICT (content): Merge conflict in app/models.py
Auto-merging migrations/schema_snapshot.json
CONFLICT (content): Merge conflict in migrations/schema_snapshot.json
Automatic merge failed; fix conflicts and then commit the result.
```

and the revision history has two heads:

```console
$ mongomig heads
80c3e9701dc2 (head)  add notes
d0ec80d92216 (head)  add nickname

2 heads: the history has diverged (e.g. two branches each added a revision). Merge them before upgrading.
```

## 1. Resolve the code conflict as usual

Keep both fields in `app/models.py`:

```python
class User(BaseModel):
    email: str
    nickname: str = ""
    notes: str = ""
```

## 2. Don't hand-edit the snapshot: run `mongomig merge`

`schema_snapshot.json` still contains conflict markers. `diff` points you the right way:

```console
$ mongomig diff
error: schema_snapshot.json is not valid JSON (line 19): Expecting property name enclosed in double quotes
hint: After a git merge of two branches that both changed models, run `mongomig merge`: it rebuilds the snapshot from your models. Otherwise restore the file from git.
```

```console
$ mongomig merge -m "merge alice and bob"
Created merge revision f29416b7e073 → migrations/versions/20261003_1852_f29416b7e073_merge_alice_and_bob.py
  merges: 80c3e9701dc2, d0ec80d92216
  schema_snapshot.json had git conflict markers: rebuilt from the models
  Make sure every model change since the branches split has a migration on one of them. Run `mongomig diff` afterwards: it should report no changes.
```

`merge` creates a revision joining both heads and **rebuilds the snapshot from the models**.
If git merged the snapshot cleanly (or you kept one side), it lists the changes it absorbs,
so you can check each one has a migration on one of the branches.

## 3. Verify and commit

```console
$ mongomig diff
No schema changes: models match the snapshot.
```

```console
$ mongomig validate
✓ configuration         mongomig.yaml
✓ revision files        4 revision(s), graph is consistent
✓ single head           f29416b7e073
✓ migration imports     all revision files import cleanly
✓ env.py / models       1 collection(s) registered
✓ models vs migrations  every model change has a migration

0 failed, 0 warning(s)
```

Commit everything (the models, the merge revision and the snapshot) and finish the git merge.

## 4. Databases catch up on their own

Alice's development database already had her migration. It now runs Bob's and the merge
revision:

```console
$ mongomig upgrade
Running upgrade fdd3ca9d2b4b -> 80c3e9701dc2, add notes
  • backfill users: 0 modified (0 matched, 0 batches)
  ✓ done in 7ms
Running upgrade 80c3e9701dc2, d0ec80d92216 -> f29416b7e073, merge alice and bob
  ✓ done in 0ms
Applied 2 revision(s).
```

!!! tip "Avoiding it"
    Two heads are normal and the fix takes a minute, but you can make them rarer: merge
    `main` into your branch before generating a migration, and keep migration PRs short-lived.
