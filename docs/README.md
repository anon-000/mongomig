# MongoMig documentation

| Guide | Read it when you want to... |
|---|---|
| [Getting started](getting-started.md) | install MongoMig and run a first migration (new or existing project) |
| [Concepts](concepts.md) | understand snapshots, storage profiles, classifications and the revision graph |
| [Writing migrations](writing-migrations.md) | write `upgrade`/`downgrade` by hand; `ctx` and `ctx.ops` reference |
| [Autogenerate](autogenerate.md) | go from model changes to reviewed migrations |
| [Production guide](production.md) | deploy safely: permissions, locking, plan, confirmations, backups, recovery |
| [CLI reference](cli.md) | look up a command, option or exit code |
| [Python API](python-api.md) | run migrations from code (tests, FastAPI startup, scripts) |
| [Troubleshooting](troubleshooting.md) | fix a specific error |

Example projects: [`examples/fastapi_pydantic`](../examples/fastapi_pydantic) and
[`examples/fastapi_beanie`](../examples/fastapi_beanie).
