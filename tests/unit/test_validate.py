from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from mongomig.metadata import registry
from mongomig.safety.permissions import database_actions, missing_actions
from tests.helpers import write_migration
from tests.project import Project

MODELS = """
@collection("users", indexes=[Index("email", unique=True)])
class User(BaseModel):
    email: str
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Project]:
    p = Project(tmp_path, monkeypatch)
    p.models(MODELS)
    assert p.run("revision", "--autogenerate", "-m", "initial").exit_code == 0
    yield p
    registry._default = None


def statuses(p: Project, *args: str) -> tuple[int, dict[str, list[str]]]:
    result = p.run("--json", "validate", *args)
    import json

    data = json.loads(result.stdout)
    by_name: dict[str, list[str]] = {}
    for check in data["checks"]:
        by_name.setdefault(check["name"], []).append(check["status"])
    return result.exit_code, by_name


def test_clean_project_passes(project: Project) -> None:
    code, checks = statuses(project)
    assert code == 0
    assert checks["configuration"] == ["ok"]
    assert checks["revision files"] == ["ok"]
    assert checks["single head"] == ["ok"]
    assert checks["migration imports"] == ["ok"]
    assert checks["models vs migrations"] == ["ok"]
    result = project.run("validate")
    assert "0 failed" in result.output


def test_unmigrated_model_change_fails(project: Project) -> None:
    project.models(MODELS + "    name: str = 'x'\n")
    code, checks = statuses(project)
    assert code == 1
    assert checks["models vs migrations"] == ["fail"]


def test_multiple_heads_fail(project: Project) -> None:
    first = next(project.versions.glob("*.py")).name.split("_")[2]
    write_migration(project.versions, "branch1", first, minute=30)
    write_migration(project.versions, "branch2", first, minute=31)
    code, checks = statuses(project)
    assert code == 1
    assert checks["single head"] == ["fail"]


def test_broken_import_fails_unless_skipped(project: Project) -> None:
    first = next(project.versions.glob("*.py")).name.split("_")[2]
    path = write_migration(project.versions, "bad1", first, minute=30)
    path.write_text("import not_installed_anywhere\n" + path.read_text())
    code, checks = statuses(project)
    assert code == 1
    assert checks["migration imports"] == ["fail"]
    code, checks = statuses(project, "--no-import")
    assert code == 0
    assert checks["migration imports"] == ["skip"]


def test_type_warnings_and_strict(project: Project) -> None:
    project.models(MODELS.replace("email: str", "email: str\n    day: datetime.date"))
    project.run("revision", "--autogenerate", "-m", "day")
    code, checks = statuses(project)
    assert code == 0
    assert checks["models"] == ["warn"]
    code, checks = statuses(project, "--strict")
    assert code == 1
    assert checks["models"] == ["fail"]


def test_bad_config_fails(project: Project) -> None:
    (project.root / "mongomig.yaml").write_text("database: [oops\n")
    code, checks = statuses(project)
    assert code == 1
    assert list(checks) == ["configuration"]


def test_privilege_evaluation() -> None:
    privileges = [
        {"resource": {"db": "app", "collection": ""}, "actions": ["find", "insert"]},
        {"resource": {"db": "app", "collection": "only_this"}, "actions": ["update"]},
        {"resource": {"db": "", "collection": ""}, "actions": ["listCollections"]},
        {"resource": {"db": "other", "collection": ""}, "actions": ["remove"]},
    ]
    assert database_actions(privileges, "app") == {"find", "insert", "listCollections"}
    assert database_actions(
        [{"resource": {"anyResource": True}, "actions": ["collMod"]}], "app"
    ) == {"collMod"}
    required, optional = missing_actions(
        {
            "find",
            "insert",
            "update",
            "remove",
            "createCollection",
            "createIndex",
            "dropIndex",
            "listCollections",
            "listIndexes",
        }
    )
    assert required == []
    assert optional == ["collMod", "dropCollection", "renameCollectionSameDB"]
