"""The only module that constructs PyMongo clients."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mongomig.config.models import LoadedConfig
from mongomig.database.redact import describe_hosts, redact_text
from mongomig.errors import ConfigError, DatabaseError

if TYPE_CHECKING:
    from pymongo import MongoClient
    from pymongo.database import Database


def create_client(config: LoadedConfig) -> MongoClient[dict[str, Any]]:
    db_cfg = config.settings.database
    unresolved = sorted(v for v in config.missing_env_vars if f"${{{v}" in db_cfg.uri)
    if unresolved:
        raise ConfigError(
            f"Environment variable {unresolved[0]} is not set (needed by database.uri).",
            suggestion=f"export {unresolved[0]}=mongodb://... or add it to your deployment env.",
        )

    from pymongo import MongoClient
    from pymongo.errors import ConfigurationError, InvalidURI

    try:
        return MongoClient(
            db_cfg.uri,
            appname="mongomig",
            serverSelectionTimeoutMS=db_cfg.server_selection_timeout_ms,
            tz_aware=True,
            connect=False,
        )
    except (InvalidURI, ConfigurationError) as exc:
        raise ConfigError(
            f"Invalid MongoDB URI: {redact_text(str(exc))}",
            suggestion="Check database.uri (format: mongodb://host:27017/dbname).",
        ) from None


def get_database(
    client: MongoClient[dict[str, Any]], config: LoadedConfig
) -> Database[dict[str, Any]]:
    name = config.settings.database.name
    if name and "${" in name:
        raise ConfigError(
            f"database.name references an unset environment variable: {name}",
        )
    if name:
        return client[name]

    from pymongo.errors import ConfigurationError

    try:
        return client.get_default_database()
    except ConfigurationError:
        raise ConfigError(
            "No database name configured.",
            suggestion="Set database.name in mongomig.yaml or include it in the URI path.",
        ) from None


def ping(client: MongoClient[dict[str, Any]], config: LoadedConfig) -> None:
    """Fail fast with a readable, credential-free error when MongoDB is unreachable."""
    from pymongo.errors import OperationFailure, PyMongoError

    hosts = describe_hosts(config.settings.database.uri)
    try:
        client.admin.command("ping")
    except OperationFailure as exc:
        raise DatabaseError(
            f"MongoDB rejected the connection to {hosts}: {redact_text(str(exc.details or exc))}",
            suggestion="Check credentials and that the user has access to this database.",
        ) from None
    except PyMongoError as exc:
        raise DatabaseError(
            f"Cannot connect to MongoDB at {hosts}: {type(exc).__name__}",
            suggestion="Is MongoDB running and reachable? For local dev: `docker compose up -d`.",
            details={"driver_error": redact_text(str(exc))[:500]},
        ) from None
