"""Typed model of ``mongomig.yaml``."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator

DEFAULT_CONFIG_FILENAME = "mongomig.yaml"
DEFAULT_MIGRATIONS_DIR = "migrations"
VERSIONS_DIRNAME = "versions"
ENV_FILENAME = "env.py"
SNAPSHOT_FILENAME = "schema_snapshot.json"


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DatabaseConfig(_Section):
    uri: str
    # Optional: falls back to the database named in the URI path.
    name: str | None = None
    server_selection_timeout_ms: PositiveInt = 5000

    @field_validator("name")
    @classmethod
    def _empty_name_is_none(cls, value: str | None) -> str | None:
        return value or None


class MigrationsConfig(_Section):
    directory: str = DEFAULT_MIGRATIONS_DIR
    tracking_collection: str = "__mongomig_migrations"
    lock_collection: str = "__mongomig_lock"


class ExecutionConfig(_Section):
    batch_size: PositiveInt = 1000
    # When `upgrade` asks for confirmation (or needs --yes): "destructive" = migrations that
    # can delete data or are irreversible; "always" = every run (e.g. in production); "never".
    confirm: Literal["destructive", "always", "never"] = "destructive"
    sleep_ms_between_batches: int = Field(default=0, ge=0)
    max_retries: int = Field(default=3, ge=0)
    lock_ttl_seconds: PositiveInt = 300


class SamplingConfig(_Section):
    size: PositiveInt = 10000


class MongoMigConfig(_Section):
    database: DatabaseConfig
    migrations: MigrationsConfig = MigrationsConfig()
    execution: ExecutionConfig = ExecutionConfig()
    sampling: SamplingConfig = SamplingConfig()


class LoadedConfig(BaseModel):
    """A parsed config plus where it came from. Paths are absolute."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    settings: MongoMigConfig
    config_path: Path
    environment: str | None = None
    # Environment variables referenced by the config but not set. Commands that need them
    # (anything touching the database) fail with a clear error; offline commands still work.
    missing_env_vars: frozenset[str] = frozenset()
    warnings: tuple[str, ...] = ()

    @property
    def root_dir(self) -> Path:
        return self.config_path.parent

    @property
    def migrations_dir(self) -> Path:
        return (self.root_dir / self.settings.migrations.directory).resolve()

    @property
    def versions_dir(self) -> Path:
        return self.migrations_dir / VERSIONS_DIRNAME

    @property
    def env_py_path(self) -> Path:
        return self.migrations_dir / ENV_FILENAME

    @property
    def snapshot_path(self) -> Path:
        return self.migrations_dir / SNAPSHOT_FILENAME
