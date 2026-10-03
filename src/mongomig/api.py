"""Programmatic API: run migrations from Python (tests, scripts, FastAPI startup).

FastAPI example::

    from contextlib import asynccontextmanager
    from mongomig import aupgrade_to_head

    @asynccontextmanager
    async def lifespan(app):
        await aupgrade_to_head()   # all workers call this; one migrates, the rest wait
        yield

In production prefer a separate migration job (``mongomig upgrade``) before rolling out the
app; the lifespan hook is best for development and small deployments.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from mongomig.migrations.executor import RunResult
from mongomig.migrations.reporting import LoggingReporter, Reporter

if TYPE_CHECKING:
    from mongomig.schema.drift import CollectionDrift

DEFAULT_LOCK_TIMEOUT_S = 120.0


def upgrade(
    target: str = "head",
    *,
    config: str | Path | None = None,
    env: str | None = None,
    steps: int | None = None,
    lock_timeout: float = 0,
    reporter: Reporter | None = None,
    yes: bool = False,
) -> RunResult:
    """Apply pending migrations up to ``target`` (``head``, ``heads`` or a revision).

    Migrations that can delete data (or all of them with ``execution.confirm: always``) raise
    ``ConfirmationRequiredError`` unless ``yes=True``.
    """
    from mongomig.config.loader import load_config
    from mongomig.database.client import open_database
    from mongomig.migrations.executor import Executor
    from mongomig.migrations.graph import build_graph

    loaded = load_config(Path(config) if config else None, environment=env)
    graph = build_graph(loaded.versions_dir)
    with open_database(loaded) as db:
        executor = Executor(loaded, graph, db, reporter=reporter or LoggingReporter())
        return executor.upgrade(
            target,
            steps=steps,
            lock_timeout=lock_timeout,
            confirm=(lambda _plan, _reasons: True) if yes else None,
        )


def downgrade(
    target: str | None = None,
    *,
    config: str | Path | None = None,
    env: str | None = None,
    steps: int | None = None,
    allow_irreversible: bool = False,
    lock_timeout: float = 0,
    reporter: Reporter | None = None,
) -> RunResult:
    """Undo migrations: one step by default, ``steps=N``, down to a revision, or ``"base"``."""
    from mongomig.config.loader import load_config
    from mongomig.database.client import open_database
    from mongomig.migrations.executor import Executor
    from mongomig.migrations.graph import build_graph

    loaded = load_config(Path(config) if config else None, environment=env)
    graph = build_graph(loaded.versions_dir)
    with open_database(loaded) as db:
        executor = Executor(loaded, graph, db, reporter=reporter or LoggingReporter())
        return executor.downgrade(
            target,
            steps=steps,
            allow_irreversible=allow_irreversible,
            lock_timeout=lock_timeout,
        )


def upgrade_to_head(
    *,
    config: str | Path | None = None,
    env: str | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT_S,
    reporter: Reporter | None = None,
    yes: bool = False,
) -> RunResult:
    """Upgrade to the single head, waiting up to ``lock_timeout`` for another runner.

    Safe to call from every app worker at startup: one acquires the lock and migrates, the
    others wait for it and then find nothing pending. Migrations that can delete data are
    refused unless ``yes=True``; run those from a deploy job instead.
    """
    return upgrade(
        "head", config=config, env=env, lock_timeout=lock_timeout, reporter=reporter, yes=yes
    )


async def aupgrade_to_head(
    *,
    config: str | Path | None = None,
    env: str | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT_S,
    reporter: Reporter | None = None,
    yes: bool = False,
) -> RunResult:
    """``upgrade_to_head`` for async code (runs in a worker thread; doesn't block the loop)."""
    return await asyncio.to_thread(
        upgrade_to_head,
        config=config,
        env=env,
        lock_timeout=lock_timeout,
        reporter=reporter,
        yes=yes,
    )


def check_drift(
    collections: list[str] | None = None,
    *,
    config: str | Path | None = None,
    env: str | None = None,
    sample_size: int | None = None,
    full_scan: bool = False,
) -> list[CollectionDrift]:
    """Compare the registered models with sampled data (like ``mongomig drift``).

    Returns one ``CollectionDrift`` per collection; ``.failed`` lists findings over the
    thresholds configured under ``drift.thresholds``.
    """
    from mongomig.cli.commands._schema import require_metadata
    from mongomig.config.loader import load_config
    from mongomig.database.client import open_database
    from mongomig.schema.drift import Thresholds, check_collections

    loaded = load_config(Path(config) if config else None, environment=env)
    declared, _ = require_metadata(loaded).schemas()
    t = loaded.settings.drift.thresholds
    with open_database(loaded) as db:
        return check_collections(
            db,
            declared,
            Thresholds(
                t.missing_field_percent, t.unexpected_type_percent, t.unexpected_field_percent
            ),
            only=collections,
            sample_size=sample_size or loaded.settings.sampling.size,
            full_scan=full_scan,
        )
