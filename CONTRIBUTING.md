# Contributing to MongoMig

Thanks for helping! Bug reports, docs fixes and pull requests are all welcome.

## Reporting bugs

Open an issue with the bug report template. The most useful reports include:

- `mongomig --version`, Python version, MongoDB version (`mongomig doctor` prints all of it;
  it never prints credentials),
- the command you ran and its full output (add `--verbose`),
- a minimal model / migration file that reproduces it.

Found a security issue? Please don't open a public issue; see [SECURITY.md](SECURITY.md).

## Development setup

```bash
git clone https://github.com/anon-000/mongomig && cd mongomig
make install        # creates .venv with Python 3.12 via uv, installs mongomig + dev tools
make mongo-up       # MongoDB 7 single-node replica set in Docker (for integration tests)
make check          # ruff + mypy --strict + pytest
```

Integration tests (`tests/integration`) need MongoDB and are skipped when it isn't running.
Test other server versions with `MONGO_VERSION=8.0 make mongo-up`. CI runs unit tests on
Python 3.11–3.13 and integration tests on MongoDB 6.0, 7.0 and 8.0.

## Pull requests

1. Open an issue first for anything bigger than a small fix, so we can agree on the approach.
2. Keep the change focused; add tests (unit tests for logic, integration tests for anything
   that touches MongoDB).
3. `make check` must pass. Code is formatted with `ruff format`, typed with `mypy --strict`.
4. Update the docs in `docs/` and `CHANGELOG.md` (under *Unreleased*) for user-visible changes.

## Design principles

These guide reviews. Please keep them intact:

- **Never delete data automatically.** Autogenerate may only emit live code it can do
  correctly; anything else is a commented `TODO(review)` block.
- **Deterministic diffs.** `diff`/autogenerate compare models with the committed snapshot,
  never with sampled data.
- **Never print credentials.** Anything that may contain a URI goes through
  `mongomig.database.redact`.
- **Fast CLI.** Heavy imports (pymongo, pydantic, rich) happen inside command functions;
  `mongomig --help` should stay well under 500 ms.
- **Everything scriptable.** Every command supports `--json` and uses the documented exit
  codes.

## Project layout

```text
src/mongomig/
├── cli/            Typer app (app.py) and one module per command (commands/)
├── config/         mongomig.yaml loading, env.py loading
├── database/       PyMongo client creation, credential redaction
├── metadata/       MongoMetadata registry, @collection, Beanie adapter, Pydantic → BSON mapping
├── schema/         CollectionSchema model, inference, snapshot, diff engine
├── generators/     diff → migration code
├── migrations/     revision files, graph, tracker, lock, executor, ctx / ctx.ops, dry run
└── safety/         impact analysis, risk, permissions
```
