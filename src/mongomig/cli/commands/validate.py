"""`mongomig validate`: everything CI should check before a deploy (offline by default)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mongomig.cli.commands._checks import CheckList, finish
from mongomig.cli.context import GlobalOptions
from mongomig.errors import MongoMigError

if TYPE_CHECKING:
    from mongomig.config.models import LoadedConfig
    from mongomig.migrations.graph import RevisionGraph
    from mongomig.output.console import Output


def run(opts: GlobalOptions, out: Output, *, database: bool, imports: bool, strict: bool) -> None:
    checks = CheckList()
    config = _config(opts, checks)
    graph = _revisions(config, checks) if config else None
    if config and graph is not None:
        if imports:
            _imports(config, graph, checks)
        else:
            checks.add("migration imports", "skip", "--no-import")
        _models(config, graph, checks, strict=strict)
        if database:
            _database(config, graph, checks)
    finish(out, checks)


def _config(opts: GlobalOptions, checks: CheckList) -> LoadedConfig | None:
    from mongomig.config.loader import load_config

    try:
        config = load_config(opts.config, environment=opts.env)
    except MongoMigError as err:
        checks.add("configuration", "fail", err.message, err.suggestion or "")
        return None
    env = f" (environment {config.environment})" if config.environment else ""
    checks.add("configuration", "ok", f"{config.config_path.name}{env}")
    for warning in config.warnings:
        checks.add("configuration", "warn", warning)
    return config


def _revisions(config: LoadedConfig, checks: CheckList) -> RevisionGraph | None:
    from mongomig.migrations.graph import RevisionGraph
    from mongomig.migrations.script import load_scripts

    try:
        graph = RevisionGraph(load_scripts(config.versions_dir))
    except MongoMigError as err:
        checks.add("revision files", "fail", err.message, err.suggestion or "")
        return None
    checks.add("revision files", "ok", f"{len(graph)} revision(s), graph is consistent")
    heads = graph.heads()
    if len(heads) > 1:
        checks.add(
            "single head",
            "fail",
            f"{len(heads)} heads: {', '.join(heads)}",
            "Join them with `mongomig merge`.",
        )
    else:
        checks.add("single head", "ok", heads[0] if heads else "no revisions yet")
    return graph


def _imports(config: LoadedConfig, graph: RevisionGraph, checks: CheckList) -> None:
    from mongomig.migrations.executor import load_migration_module

    broken: list[str] = []
    for rev in graph.topological_order():
        try:
            load_migration_module(graph.scripts[rev], config.root_dir)
        except MongoMigError as err:
            broken.append(err.message)
    if broken:
        checks.add(
            "migration imports",
            "fail",
            broken[0] + (f" (+{len(broken) - 1} more)" if len(broken) > 1 else ""),
        )
    else:
        checks.add("migration imports", "ok", "all revision files import cleanly")


def _models(config: LoadedConfig, graph: RevisionGraph, checks: CheckList, *, strict: bool) -> None:
    from mongomig.cli.commands._schema import compute
    from mongomig.config.envpy import load_metadata

    try:
        metadata = load_metadata(config)
    except MongoMigError as err:
        checks.add("env.py / models", "fail", err.message, err.suggestion or "")
        return
    if metadata is None:
        checks.add("env.py / models", "skip", "no models registered (autogenerate not in use)")
        return
    checks.add("env.py / models", "ok", f"{len(metadata.collections)} collection(s) registered")

    try:
        state = compute(config, graph, [])
    except MongoMigError as err:
        checks.add("models vs migrations", "fail", err.message, err.suggestion or "")
        return
    diff = state.diff
    if diff.has_changes:
        checks.add(
            "models vs migrations",
            "fail",
            f"{len(diff.changes)} model change(s) have no migration: "
            + ", ".join(c.summary for c in diff.changes[:3])
            + (" ..." if len(diff.changes) > 3 else ""),
            'Run `mongomig revision --autogenerate -m "..."` and commit the result.',
        )
    else:
        checks.add("models vs migrations", "ok", "every model change has a migration")
    for warning in diff.warnings:
        checks.add("models", "fail" if strict else "warn", warning)


def _database(config: LoadedConfig, graph: RevisionGraph, checks: CheckList) -> None:
    from mongomig.database.client import open_database
    from mongomig.migrations.tracker import MigrationTracker, compute_state

    try:
        with open_database(config) as db:
            records = MigrationTracker(db, config.settings.migrations.tracking_collection).records()
    except MongoMigError as err:
        checks.add("database", "fail", err.message, err.suggestion or "")
        return
    state = compute_state(graph, records)
    checks.add("database", "ok", f"{len(state.applied)} applied, {len(state.pending)} pending")
    if state.modified:
        checks.add(
            "applied checksums",
            "fail",
            f"modified after running: {', '.join(state.modified)}",
            "Revert the edits, or `mongomig stamp <head>` if they're harmless.",
        )
    else:
        checks.add("applied checksums", "ok", "applied revision files are unchanged")
    if state.failed:
        checks.add(
            "failed runs",
            "fail",
            f"failed or interrupted: {', '.join(state.failed)}",
            "Fix the cause and run `mongomig upgrade` again.",
        )
    if state.unknown:
        checks.add(
            "unknown revisions",
            "warn",
            f"applied in the database but missing here: {', '.join(state.unknown)}",
            "Is this checkout older than the database?",
        )
