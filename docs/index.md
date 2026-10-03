# MongoMig

**Alembic-style schema evolution and migrations for MongoDB.**

Change your models, see exactly what changed, generate a migration, review its impact, and run
it safely. Built for Python services (FastAPI, Flask, workers) on PyMongo or Beanie.

```bash
pip install mongomig            # add [beanie] for Beanie support
```

Python 3.11+ · MongoDB 6.0+ (tested on 6.0, 7.0 and 8.0)

## The workflow

```console
$ mongomig diff                                     # what changed in your models (offline)
$ mongomig revision --autogenerate -m "evolve users" # generate the migration
$ mongomig plan                                     # impact and risk against real data
$ mongomig upgrade                                  # batched, locked, resumable
$ mongomig drift --check                            # does stored data still match? (scheduled)
```

## What you get

- **Autogenerate from your models.** Plain Pydantic (`@collection`) or Beanie documents, with
  no extra declarations. MongoMig generates backfills with your defaults, renames, indexes and
  `$jsonSchema` validators. Risky changes become commented `TODO(review)` blocks, and data is
  never deleted automatically.
- **Deterministic diffs.** Models are compared with a committed schema snapshot, so the
  result is the same on every machine and in CI.
- **See the impact first.** `plan` and `--dry-run` estimate the documents touched and spot
  collection scans, unique indexes that would fail on duplicates, and validator rejections.
- **Production-grade execution.** A distributed lock, batched operations with progress, and
  checkpoints so a failed migration resumes where it stopped. Also transactions, checksums,
  confirmation before destructive migrations, and restorable backups.
- **Catch drift.** `mongomig drift` compares your models with the data actually stored.
- **Keep history manageable.** `mongomig squash` replaces old revisions with one, and existing
  databases adopt it automatically.

## Where to go next

| | |
|---|---|
| New here | [Getting started](getting-started.md) |
| Know Alembic, or using hand-written scripts? | [Coming from Alembic or scripts](comparison.md) |
| How it thinks | [Concepts](concepts.md) |
| Writing migrations by hand | [Writing migrations](writing-migrations.md) |
| From model changes to migrations | [Autogenerate](autogenerate.md) |
| Running it in production | [Production guide](production.md) |
| Look something up | [CLI](cli.md) · [Python API](python-api.md) · [Troubleshooting](troubleshooting.md) |

Example projects:
[FastAPI + Pydantic](https://github.com/anon-000/mongomig/tree/main/examples/fastapi_pydantic) ·
[FastAPI + Beanie](https://github.com/anon-000/mongomig/tree/main/examples/fastapi_beanie)
