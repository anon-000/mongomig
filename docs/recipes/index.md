# Recipes

Step-by-step guides for real situations: the commands to run, what you'll see, and what to
watch out for.

Every command output on these pages is real: it was produced by running MongoMig 0.4.2 against
MongoDB, not written by hand. The FastAPI and testing recipes come from a real project,
[`examples/fastapi_store`](https://github.com/anon-000/mongomig/tree/main/examples/fastapi_store),
whose tests run in MongoMig's CI.

## Everyday changes

| I want to... | Recipe |
|---|---|
| add a field (and fill it in old documents) | [Add a field with a default](add-a-field.md) |
| rename a field | [Rename a field](rename-a-field.md) |
| change a field's type (e.g. string → int) | [Change a field's type](change-a-type.md) |
| remove a field, and maybe delete its data | [Remove a field](remove-a-field.md) |
| add a unique index | [Add a unique index when duplicates exist](unique-index.md) |
| run custom logic over many documents | [A long custom data migration](long-data-migration.md) |

## Teams and history

| I want to... | Recipe |
|---|---|
| start using MongoMig on a database that already exists | [Adopt an existing database](adopt-existing-database.md) |
| fix two branches that both changed models | [Two developers changed models](parallel-branches.md) |
| clean up a long migration history | [Squash old history](squash.md) |

## Production

| I want to... | Recipe |
|---|---|
| run migrations in CI/CD, Docker, Kubernetes | [Deploy](deploy.md) |
| use MongoMig in a FastAPI app | [FastAPI](fastapi.md) |
| test my migrations | [Test your migrations](testing.md) |
| recover from a migration that failed halfway | [A migration failed halfway](resume-after-failure.md) |
| undo a bad release | [Roll back a bad release](rollback.md) |
| know when data stops matching my models | [Monitor drift nightly](drift-monitoring.md) |
