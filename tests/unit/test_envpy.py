from __future__ import annotations

from pathlib import Path

import pytest

from mongomig.config.envpy import load_env_module, load_target_metadata
from mongomig.config.loader import load_config
from mongomig.errors import ConfigError
from tests.helpers import write_file


def _project(tmp_path: Path, env_py: str) -> Path:
    write_file(tmp_path / "mongomig.yaml", "database:\n  uri: mongodb://h/db\n")
    write_file(tmp_path / "migrations" / "env.py", env_py)
    return tmp_path


def test_env_py_can_import_project_code(tmp_path: Path) -> None:
    write_file(tmp_path / "myproj_app" / "__init__.py", "")
    write_file(tmp_path / "myproj_app" / "models.py", "METADATA = {'users': 1}\n")
    root = _project(
        tmp_path, "from myproj_app.models import METADATA\ntarget_metadata = METADATA\n"
    )
    config = load_config(root / "mongomig.yaml", env={})
    assert load_target_metadata(config) == {"users": 1}


def test_none_metadata_allowed(tmp_path: Path) -> None:
    root = _project(tmp_path, "target_metadata = None\n")
    assert load_target_metadata(load_config(root / "mongomig.yaml", env={})) is None


def test_missing_target_metadata(tmp_path: Path) -> None:
    root = _project(tmp_path, "x = 1\n")
    with pytest.raises(ConfigError, match="target_metadata"):
        load_target_metadata(load_config(root / "mongomig.yaml", env={}))


def test_errors_in_env_py_are_reported(tmp_path: Path) -> None:
    root = _project(tmp_path, "import definitely_not_installed_pkg\n")
    with pytest.raises(ConfigError, match="ModuleNotFoundError") as exc:
        load_env_module(root / "migrations" / "env.py", root)
    assert "env.py" in exc.value.details["line"]


def test_missing_env_py(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_env_module(tmp_path / "migrations" / "env.py", tmp_path)
