"""The example projects must keep working: validate offline, upgrade and downgrade for real."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pymongo.database import Database
from typer.testing import CliRunner

from mongomig.cli.app import app
from mongomig.metadata import registry

runner = CliRunner()
EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
PACKAGES = {"fastapi_pydantic": "userservice", "fastapi_beanie": "catalog", "fastapi_store": "app"}


@pytest.fixture(params=sorted(PACKAGES))
def example(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    name: str = request.param
    root = tmp_path / name
    shutil.copytree(EXAMPLES / name, root, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.chdir(root)
    registry._default = None
    yield root
    registry._default = None
    for module in [m for m in sys.modules if m.split(".")[0] == PACKAGES[name]]:
        del sys.modules[module]


def test_example_validates_offline(example: Path) -> None:
    result = runner.invoke(app, ["validate"])
    assert result.exit_code == 0, result.output
    assert "every model change has a migration" in result.output


@pytest.mark.integration
def test_example_migrates_up_and_down(
    example: Path,
    monkeypatch: pytest.MonkeyPatch,
    mongo_uri: str,
    mongo_db: Database[dict[str, Any]],
) -> None:
    monkeypatch.setenv("MONGODB_URI", mongo_uri)
    monkeypatch.setenv("MONGODB_DATABASE", mongo_db.name)
    up = runner.invoke(app, ["upgrade"])
    assert up.exit_code == 0, up.output
    assert runner.invoke(app, ["current", "--check"]).exit_code == 0
    assert set(mongo_db.list_collection_names()) - {"__mongomig_migrations", "__mongomig_lock"}
    down = runner.invoke(app, ["downgrade", "base", "--yes"])
    assert down.exit_code == 0, down.output


@pytest.mark.integration
def test_fastapi_store_example_test_suite(mongo_uri: str, mongo_client: Any) -> None:
    """The FastAPI and testing recipes embed this project's files: its tests must pass."""
    import subprocess
    import sys

    root = EXAMPLES / "fastapi_store"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=root,
        env={**os.environ, "TEST_MONGODB_URI": mongo_uri, "MONGODB_URI": mongo_uri},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 passed" in result.stdout
