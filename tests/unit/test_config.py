from __future__ import annotations

from pathlib import Path

import pytest

from mongomig.config.loader import deep_merge, find_config, interpolate, load_config
from mongomig.errors import ConfigError, ExitCode
from tests.helpers import write_file

BASE = """
database:
  uri: ${MONGODB_URI}
  name: ${MONGODB_DATABASE:-app}
execution:
  batch_size: 500
"""


def test_interpolation_with_defaults_and_missing() -> None:
    missing: set[str] = set()
    result = interpolate(
        {"a": "${X}", "b": ["${Y:-dflt}", "pre-${X}-post"], "c": "${NOPE}", "d": 5},
        {"X": "1"},
        missing,
    )
    assert result == {"a": "1", "b": ["dflt", "pre-1-post"], "c": "${NOPE}", "d": 5}
    assert missing == {"NOPE"}


def test_deep_merge_replaces_lists_and_merges_dicts() -> None:
    base = {"a": {"x": 1, "y": 2}, "l": [1, 2]}
    assert deep_merge(base, {"a": {"y": 3}, "l": [9]}) == {"a": {"x": 1, "y": 3}, "l": [9]}


def test_load_defaults_and_env(tmp_path: Path) -> None:
    cfg_path = write_file(tmp_path / "mongomig.yaml", BASE)
    cfg = load_config(cfg_path, env={"MONGODB_URI": "mongodb://h/db"})
    assert cfg.settings.database.uri == "mongodb://h/db"
    assert cfg.settings.database.name == "app"
    assert cfg.settings.execution.batch_size == 500
    assert cfg.settings.migrations.tracking_collection == "__mongomig_migrations"
    assert cfg.missing_env_vars == frozenset()
    assert cfg.versions_dir == (tmp_path / "migrations" / "versions").resolve()


def test_missing_env_var_is_recorded_not_fatal(tmp_path: Path) -> None:
    cfg = load_config(write_file(tmp_path / "mongomig.yaml", BASE), env={})
    assert cfg.missing_env_vars == frozenset({"MONGODB_URI"})


def test_environment_overlay(tmp_path: Path) -> None:
    base = write_file(tmp_path / "mongomig.yaml", BASE)
    write_file(tmp_path / "mongomig.production.yaml", "execution:\n  batch_size: 5000\n")
    cfg = load_config(base, environment="production", env={"MONGODB_URI": "mongodb://h"})
    assert cfg.environment == "production"
    assert cfg.settings.execution.batch_size == 5000
    assert cfg.settings.database.name == "app"  # untouched keys survive the merge


def test_environment_from_env_var(tmp_path: Path) -> None:
    base = write_file(tmp_path / "mongomig.yaml", BASE)
    write_file(tmp_path / "mongomig.test.yaml", "database:\n  name: testdb\n")
    cfg = load_config(base, env={"MONGOMIG_ENV": "test"})
    assert cfg.settings.database.name == "testdb"


def test_missing_overlay_is_an_error(tmp_path: Path) -> None:
    base = write_file(tmp_path / "mongomig.yaml", BASE)
    with pytest.raises(ConfigError, match="staging") as exc:
        load_config(base, environment="staging", env={})
    assert exc.value.exit_code == ExitCode.CONFIG_ERROR
    assert "mongomig.staging.yaml" in (exc.value.suggestion or "")


def test_invalid_environment_name(tmp_path: Path) -> None:
    base = write_file(tmp_path / "mongomig.yaml", BASE)
    with pytest.raises(ConfigError, match="Invalid environment"):
        load_config(base, environment="../etc", env={})


def test_inline_credentials_warn(tmp_path: Path) -> None:
    path = write_file(tmp_path / "mongomig.yaml", "database:\n  uri: mongodb://u:pw@h/db\n")
    cfg = load_config(path, env={})
    assert len(cfg.warnings) == 1
    assert "pw" not in cfg.warnings[0]


def test_unknown_keys_rejected(tmp_path: Path) -> None:
    path = write_file(tmp_path / "mongomig.yaml", BASE + "execution:\n  batchsize: 1\n")
    with pytest.raises(ConfigError, match="batchsize"):
        load_config(path, env={})


def test_invalid_values_rejected(tmp_path: Path) -> None:
    path = write_file(
        tmp_path / "mongomig.yaml", "database:\n  uri: x\nexecution:\n  batch_size: 0\n"
    )
    with pytest.raises(ConfigError, match=r"execution\.batch_size"):
        load_config(path, env={})


def test_invalid_yaml(tmp_path: Path) -> None:
    path = write_file(tmp_path / "mongomig.yaml", "database: [unclosed\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(path, env={})


def test_find_config_walks_up(tmp_path: Path) -> None:
    write_file(tmp_path / "mongomig.yaml", BASE)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_config(nested) == (tmp_path / "mongomig.yaml").resolve()


def test_no_config_found(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"No mongomig\.yaml") as exc:
        load_config(env={}, start_dir=tmp_path)
    assert "mongomig init" in (exc.value.suggestion or "")


def test_config_path_from_env_var(tmp_path: Path) -> None:
    path = write_file(tmp_path / "custom.yaml", BASE)
    cfg = load_config(env={"MONGOMIG_CONFIG": str(path)}, start_dir=Path("/"))
    assert cfg.config_path == path.resolve()
