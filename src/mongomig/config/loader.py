"""Locate, read, interpolate and validate ``mongomig.yaml`` (+ environment overlays)."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mongomig.config.models import DEFAULT_CONFIG_FILENAME, LoadedConfig, MongoMigConfig
from mongomig.errors import ConfigError

CONFIG_ENV_VAR = "MONGOMIG_CONFIG"
ENVIRONMENT_ENV_VAR = "MONGOMIG_ENV"

# ${VAR} or ${VAR:-default}
_VAR_RE = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")
_ENV_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def find_config(start: Path | None = None) -> Path | None:
    """Walk up from ``start`` (default: cwd) looking for ``mongomig.yaml``."""
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / DEFAULT_CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_config(
    config_path: Path | None = None,
    *,
    environment: str | None = None,
    env: Mapping[str, str] | None = None,
    start_dir: Path | None = None,
) -> LoadedConfig:
    """Load configuration.

    Resolution order for the file: explicit ``config_path`` → ``$MONGOMIG_CONFIG`` → search
    upward from ``start_dir``. The environment overlay (``mongomig.<environment>.yaml``) comes
    from ``environment`` or ``$MONGOMIG_ENV`` and is deep-merged over the base file.
    """
    env = os.environ if env is None else env

    path = _resolve_config_path(config_path, env, start_dir)
    environment = environment or env.get(ENVIRONMENT_ENV_VAR) or None

    raw = _read_yaml(path)
    warnings = _credential_warnings(raw, path)

    if environment:
        if not _ENV_NAME_RE.match(environment):
            raise ConfigError(
                f"Invalid environment name {environment!r}.",
                suggestion="Use letters, digits, '-' or '_' (e.g. --env production).",
            )
        overlay_path = path.with_name(f"{path.stem}.{environment}{path.suffix}")
        if not overlay_path.is_file():
            raise ConfigError(
                f"No configuration found for environment {environment!r}.",
                suggestion=f"Create {overlay_path.name} next to {path.name}.",
                details={"expected_path": str(overlay_path)},
            )
        overlay = _read_yaml(overlay_path)
        warnings += _credential_warnings(overlay, overlay_path)
        raw = deep_merge(raw, overlay)

    missing: set[str] = set()
    interpolated = interpolate(raw, env, missing)

    from pydantic import ValidationError

    try:
        settings = MongoMigConfig.model_validate(interpolated)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(
            f"Invalid configuration in {path.name}: {problems}",
            suggestion="Check the file against the documented mongomig.yaml format.",
            details={"path": str(path)},
        ) from None

    return LoadedConfig(
        settings=settings,
        config_path=path,
        environment=environment,
        missing_env_vars=frozenset(missing),
        warnings=tuple(warnings),
    )


def interpolate(value: Any, env: Mapping[str, str], missing: set[str]) -> Any:
    """Recursively expand ``${VAR}`` / ``${VAR:-default}`` in string values.

    Unset variables without a default are left as-is and recorded in ``missing`` so commands
    that don't need them (e.g. ``history``) keep working offline.
    """
    if isinstance(value, str):

        def _sub(match: re.Match[str]) -> str:
            name, default = match.group("name"), match.group("default")
            if name in env:
                return env[name]
            if default is not None:
                return default
            missing.add(name)
            return match.group(0)

        return _VAR_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: interpolate(v, env, missing) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v, env, missing) for v in value]
    return value


def deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Merge mappings recursively; overlay wins. Lists and scalars are replaced, not merged."""
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _resolve_config_path(
    config_path: Path | None, env: Mapping[str, str], start_dir: Path | None
) -> Path:
    if config_path is None and env.get(CONFIG_ENV_VAR):
        config_path = Path(env[CONFIG_ENV_VAR])
    if config_path is not None:
        path = config_path.expanduser().resolve()
        if not path.is_file():
            raise ConfigError(f"Config file not found: {path}")
        return path
    found = find_config(start_dir)
    if found is None:
        raise ConfigError(
            f"No {DEFAULT_CONFIG_FILENAME} found in this directory or any parent.",
            suggestion="Run `mongomig init` to create one, or pass --config PATH.",
        )
    return found


def _read_yaml(path: Path) -> dict[str, Any]:
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} is not valid YAML: {exc}") from None
    except OSError as exc:
        raise ConfigError(f"Cannot read {path}: {exc.strerror}") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must contain a YAML mapping at the top level.")
    return data


def _credential_warnings(raw: Mapping[str, Any], path: Path) -> list[str]:
    from mongomig.database.redact import uri_has_inline_password

    database = raw.get("database")
    uri = database.get("uri") if isinstance(database, Mapping) else None
    if isinstance(uri, str) and uri_has_inline_password(uri):
        return [
            f"{path.name} contains a password in database.uri. "
            "Use an environment variable instead, e.g. uri: ${MONGODB_URI}"
        ]
    return []
