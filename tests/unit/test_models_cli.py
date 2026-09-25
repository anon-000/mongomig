from __future__ import annotations

import json
import secrets
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mongomig.cli.app import app
from mongomig.metadata import registry

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fresh_default() -> Iterator[None]:
    registry._default = None
    yield
    registry._default = None


def make_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, models_src: str, env_tail: str
) -> str:
    """A project whose env.py imports a uniquely-named models package."""
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    pkg = f"proj_{secrets.token_hex(4)}"
    (tmp_path / pkg).mkdir()
    (tmp_path / pkg / "__init__.py").write_text("")
    (tmp_path / pkg / "models.py").write_text(models_src)
    env = tmp_path / "migrations" / "env.py"
    env.write_text(
        env.read_text().replace(
            "# import app.models  # noqa: F401  (importing registers @collection models)",
            f"import {pkg}.models  # noqa: F401",
        )
        + env_tail
    )
    return pkg


MODELS = """
import datetime, decimal
from pydantic import BaseModel
from mongomig import collection, Index

class Profile(BaseModel):
    verified: bool = False

@collection("users", indexes=[Index("email", unique=True)], validator="auto")
class User(BaseModel):
    email: str
    joined: datetime.datetime
    balance: decimal.Decimal = decimal.Decimal(0)
    profile: Profile | None = None
"""


def test_models_shows_declared_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, monkeypatch, MODELS, "")
    result = runner.invoke(app, ["models"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "users" in out
    assert "joined" in out
    assert "email_1 (email ↑, unique)" in out
    assert "validator: managed" in out
    assert "Decimal needs bson.Decimal128" in out  # python-mode warning surfaces


def test_models_json_and_storage_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, monkeypatch, MODELS, "\nMongoMetadata.default(storage='json')\n")
    data = json.loads(runner.invoke(app, ["--json", "models"]).stdout)
    assert data["storage"]["mode"] == "json"
    users = data["collections"]["users"]
    assert users["fields"]["joined"]["bson_types"] == ["string"]
    assert users["fields"]["profile"]["fields"]["verified"]["bson_types"] == ["bool"]
    assert data["warnings"] == []


def test_models_without_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    env = tmp_path / "migrations" / "env.py"
    env.write_text("target_metadata = None\n")
    result = runner.invoke(app, ["models"])
    assert result.exit_code == 0
    assert "No models registered" in result.output

    env.write_text("target_metadata = {'users': 1}\n")
    result = runner.invoke(app, ["models"])
    assert result.exit_code == 3
    assert "must be a MongoMetadata" in result.output


def test_init_env_py_is_valid_and_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["--json", "models"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["collections"] == {}
