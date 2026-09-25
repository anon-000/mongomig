"""`mongomig doctor`: diagnose the environment MongoMig runs in."""

from __future__ import annotations

import platform
import time
from datetime import UTC, datetime
from importlib import metadata as importlib_metadata
from typing import TYPE_CHECKING, Any

from mongomig.cli.commands._checks import CheckList, Status, finish
from mongomig.cli.context import GlobalOptions
from mongomig.errors import MongoMigError

if TYPE_CHECKING:
    from pymongo.database import Database

    from mongomig.config.models import LoadedConfig
    from mongomig.output.console import Output

TESTED_MAJOR_VERSIONS = (6, 7, 8)


def run(opts: GlobalOptions, out: Output) -> None:
    checks = CheckList()
    _versions(checks)
    config = _config(opts, checks)
    if config is not None:
        _models(config, checks)
        _database(config, checks)
    finish(out, checks)


def _versions(checks: CheckList) -> None:
    from mongomig._version import __version__

    parts = [f"mongomig {__version__}", f"Python {platform.python_version()}"]
    for package in ("pymongo", "pydantic", "beanie"):
        try:
            parts.append(f"{package} {importlib_metadata.version(package)}")
        except importlib_metadata.PackageNotFoundError:
            continue
    checks.add("versions", "ok", ", ".join(parts))


def _config(opts: GlobalOptions, checks: CheckList) -> LoadedConfig | None:
    from mongomig.config.loader import load_config
    from mongomig.database.redact import describe_hosts

    try:
        config = load_config(opts.config, environment=opts.env)
    except MongoMigError as err:
        checks.add("configuration", "fail", err.message, err.suggestion or "")
        return None
    env = f", environment {config.environment}" if config.environment else ""
    checks.add("configuration", "ok", f"{config.config_path}{env}")
    for warning in config.warnings:
        checks.add("credentials", "warn", warning)
    uri = config.settings.database.uri
    missing = sorted(v for v in config.missing_env_vars if f"${{{v}" in uri)
    if missing:
        checks.add(
            "connection string",
            "fail",
            f"{missing[0]} is not set",
            f"export {missing[0]}=mongodb://...",
        )
        return config
    checks.add("connection string", "ok", describe_hosts(uri))
    return config


def _models(config: LoadedConfig, checks: CheckList) -> None:
    from mongomig.config.envpy import load_metadata

    try:
        metadata = load_metadata(config)
    except MongoMigError as err:
        checks.add("models", "fail", err.message, err.suggestion or "")
        return
    if metadata is None:
        checks.add("models", "skip", "none registered in env.py")
        return
    try:
        _, warnings = metadata.schemas()
    except MongoMigError as err:
        checks.add("models", "fail", err.message, err.suggestion or "")
        return
    checks.add(
        "models",
        "ok",
        f"{len(metadata.collections)} collection(s), storage profile {metadata.profile.mode!r}",
    )
    for warning in warnings:
        checks.add("type mapping", "warn", warning)


def _database(config: LoadedConfig, checks: CheckList) -> None:
    from mongomig.database.client import open_database

    if any(c.name == "connection string" and c.status == "fail" for c in checks.checks):
        checks.add("connection", "skip", "no connection string")
        return
    started = time.perf_counter()
    try:
        with open_database(config) as db:
            elapsed = (time.perf_counter() - started) * 1000
            checks.add("connection", "ok", f"database {db.name!r}, ping {elapsed:.0f} ms")
            _server(db, checks)
            _permissions(db, checks)
            _migration_state(db, config, checks)
    except MongoMigError as err:
        checks.add("connection", "fail", err.message, err.suggestion or "")


def _server(db: Database[dict[str, Any]], checks: CheckList) -> None:
    client = db.client
    version = str(client.admin.command("buildInfo").get("version", "?"))
    major = int(version.split(".", maxsplit=1)[0]) if version[:1].isdigit() else 0
    status: Status = "ok" if major in TESTED_MAJOR_VERSIONS else "warn"
    checks.add(
        "server version",
        status,
        f"MongoDB {version}",
        "MongoMig is tested on MongoDB 6.0, 7.0 and 8.0.",
    )

    hello = client.admin.command("hello")
    if hello.get("msg") == "isdbgrid":
        checks.add("topology", "ok", "sharded cluster (mongos)")
    elif hello.get("setName"):
        checks.add("topology", "ok", f"replica set {hello['setName']!r}")
    else:
        checks.add(
            "topology",
            "warn",
            "standalone server",
            "Fine for development; production should use a replica set (durability, transactions).",
        )

    local_time = hello.get("localTime")
    if isinstance(local_time, datetime):
        server = local_time if local_time.tzinfo else local_time.replace(tzinfo=UTC)
        skew = abs((datetime.now(UTC) - server).total_seconds())
        if skew > 30:
            checks.add(
                "clock",
                "warn",
                f"this machine and the server differ by {skew:.0f}s",
                "Timestamps in `current`/`history` may look off; the lock uses server "
                "time so it is unaffected.",
            )


def _permissions(db: Database[dict[str, Any]], checks: CheckList) -> None:
    from mongomig.safety.permissions import (
        OPTIONAL_ACTIONS,
        REQUIRED_ACTIONS,
        database_actions,
        missing_actions,
    )

    status = db.client.admin.command("connectionStatus", showPrivileges=True)
    info = status.get("authInfo", {})
    if not info.get("authenticatedUsers"):
        checks.add("permissions", "skip", "not authenticated (authentication disabled?)")
        return
    user = info["authenticatedUsers"][0]
    who = f"{user.get('user')}@{user.get('db')}"
    actions = database_actions(info.get("authenticatedUserPrivileges", []), db.name)
    required, optional = missing_actions(actions)
    if required:
        checks.add(
            "permissions",
            "fail",
            f"{who} lacks {', '.join(required)} on {db.name}",
            f"Grant the readWrite role on {db.name} (needed for: "
            + "; ".join(REQUIRED_ACTIONS[a] for a in required)
            + ").",
        )
    else:
        checks.add("permissions", "ok", f"{who} can run migrations on {db.name}")
    for action in optional:
        checks.add(
            "permissions",
            "warn",
            f"{who} lacks {action}",
            f"Needed only for {OPTIONAL_ACTIONS[action]}.",
        )


def _migration_state(db: Database[dict[str, Any]], config: LoadedConfig, checks: CheckList) -> None:
    from mongomig.migrations.lock import LOCK_DOC_ID
    from mongomig.migrations.ops import BACKUP_PREFIX
    from mongomig.migrations.tracker import MigrationTracker

    settings = config.settings.migrations
    tracker = MigrationTracker(db, settings.tracking_collection)
    records = tracker.records()
    applied = sum(1 for r in records if r.status == "applied")
    broken = [r.revision for r in records if r.status in ("failed", "running")]
    if not tracker.exists():
        checks.add("tracking", "ok", "no migrations applied yet")
    elif broken:
        checks.add(
            "tracking",
            "fail",
            f"failed or interrupted: {', '.join(broken)}",
            "Fix the cause and run `mongomig upgrade` again.",
        )
    else:
        checks.add("tracking", "ok", f"{applied} revision(s) applied")

    lock = db[settings.lock_collection].find_one({"_id": LOCK_DOC_ID})
    if lock is None:
        checks.add("lock", "ok", "free")
    else:
        expires = lock.get("expires_at")
        now = datetime.now(UTC)
        if isinstance(expires, datetime) and expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if isinstance(expires, datetime) and expires > now:
            checks.add(
                "lock",
                "warn",
                f"held by {lock.get('owner')} until {expires:%H:%M:%S} UTC",
                "A migration is running now, or a runner died and the lock will expire.",
            )
        else:
            checks.add("lock", "ok", "free (stale lock will be taken over)")

    backups = [n for n in db.list_collection_names() if n.startswith(BACKUP_PREFIX)]
    if backups:
        checks.add(
            "backups",
            "warn",
            f"{len(backups)} backup collection(s) present",
            "Review with `mongomig backups`; drop them once you no longer need them.",
        )
