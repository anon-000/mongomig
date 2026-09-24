# MongoMig

**Alembic-style schema evolution and migrations for MongoDB.**

Built for Python services (FastAPI, Flask, workers) on PyMongo, Motor or Beanie.

> **Status: pre-alpha.** Milestone 1 (foundation) is done. Running migrations (`upgrade` /
> `downgrade`), schema diff and autogenerate are coming next. See the roadmap below.

```console
$ mongomig init
$ mongomig revision -m "add user profile"
Created revision 7be204a1c9e0 → migrations/versions/20260924_1432_7be204a1c9e0_add_user_profile.py

$ mongomig history
a1f3c9d20b44 -> 7be204a1c9e0 (head), add user profile
<base> -> a1f3c9d20b44, initial

$ mongomig current
Database: app
Current:  a1f3c9d20b44  initial
Pending:  1
  - 7be204a1c9e0  add user profile
```

## Install

```bash
pip install mongomig          # not yet published — install from source for now
```

Requires Python 3.11+ and MongoDB 6.0+.

## Quick start

```bash
mongomig init                                   # creates mongomig.yaml + migrations/
export MONGODB_URI="mongodb://localhost:27017"
mongomig revision -m "initial"                  # new revision file
mongomig current                                # what's applied vs pending
```

`mongomig init` creates:

```text
mongomig.yaml                   # connection + execution settings (no secrets!)
migrations/
├── env.py                      # Python: points MongoMig at your models
├── schema_snapshot.json        # last known expected schema (used by autogenerate)
└── versions/                   # one file per revision
```

### Configuration

```yaml
database:
  uri: ${MONGODB_URI}                 # ${VAR} and ${VAR:-default} are expanded
  name: ${MONGODB_DATABASE:-app}
```

Per-environment overrides live in `mongomig.<env>.yaml` and are merged on top:

```bash
mongomig --env production current     # or MONGOMIG_ENV=production
```

MongoMig never prints passwords or full connection strings. It warns you if a password is
written directly into the config file.

### Revision files

```python
"""add user profile"""

revision = "7be204a1c9e0"
down_revision = "a1f3c9d20b44"
reversible = True


def upgrade(ctx):
    ...


def downgrade(ctx):
    ...
```

Revision order comes from `down_revision`, not from the file name. Revision ids are random,
so two developers working on separate branches never get the same id. If both branches add a
revision, `mongomig heads` shows two heads, and `mongomig revision` refuses to continue until
you choose a parent with `--head`.

Revision ids can be shortened to a unique prefix of 4 or more characters, the same way git
handles commit hashes.

### Commands

| Command | Needs MongoDB | Description |
|---|---|---|
| `init` | no | Create config and migrations directory |
| `revision -m MSG [--head REV]` | no | Create a new revision |
| `heads` | no | Show head revision(s) |
| `history` | no | List revisions, newest first |
| `current` | yes | Applied vs pending revisions (read-only) |

Global options: `--config PATH`, `--env NAME`, `--json`, `--verbose`, `--version`.

Exit codes: `0` success · `1` validation · `2` execution/connection · `3` configuration ·
`4` revision conflict · `5` lock · `6` checksum mismatch.

## Roadmap

- [x] **M1 Foundation**: config, CLI, revision files, revision graph, tracking
- [ ] **M2 Migration engine**: `upgrade`, `downgrade`, locking, `merge`, checksums, FastAPI lifespan helper
- [ ] **M3 Schema engine**: `@collection` models, Beanie support, `inspect`, snapshots
- [ ] **M4 Autogenerate**: `diff`, `revision --autogenerate`, index/validator diff
- [ ] **M5 Production safety**: `--dry-run`, `plan`, batching, progress
- [ ] **M6 Release**: `validate`, `doctor`, docs, PyPI

## Development

```bash
make install        # uv venv + editable install with dev extras
make mongo-up       # MongoDB 7 single-node replica set in Docker
make check          # ruff + mypy --strict + pytest
```

Integration tests are skipped automatically when MongoDB isn't running. To test against another
MongoDB version, run `MONGO_VERSION=8.0 make mongo-up`.

### Releasing

1. Bump `src/mongomig/_version.py` and update `CHANGELOG.md`, then merge to `main`.
2. On GitHub, create a Release with tag `v<version>` (e.g. `v0.1.0`) and publish it.
3. `.github/workflows/release.yml` checks that the tag matches the version, builds and
   smoke-tests the wheel, then publishes to PyPI through Trusted Publishing.

## License

MIT
