from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

from mongomig.metadata import registry
from mongomig.migrations.script import load_script
from mongomig.schema.snapshot import snapshot_hash
from tests.project import Project

V1 = """
@collection("users", indexes=[Index("email", unique=True)])
class User(BaseModel):
    first_name: str
    email: str
"""

V2 = """
@collection("users", indexes=[Index("email", unique=True)])
class User(BaseModel):
    given_name: str
    email: str
    status: str = "active"
    age: int | None = None
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Project]:
    yield Project(tmp_path, monkeypatch)
    registry._default = None


def test_diff_requires_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = Project(tmp_path, monkeypatch)
    (tmp_path / "migrations" / "env.py").write_text("target_metadata = None\n")
    result = p.run("diff")
    assert result.exit_code == 3
    assert "No models registered" in result.output


def test_full_autogenerate_cycle(project: Project) -> None:
    project.models(V1)

    first = project.json("diff")
    assert [c["kind"] for c in first["changes"]] == ["collection_added", "index_added"]
    assert project.run("diff", "--check").exit_code == 1

    gen = project.json("revision", "--autogenerate", "-m", "initial")
    script = load_script(project.root / gen["path"])
    assert script.down_revisions == ()
    assert script.snapshot_hash == snapshot_hash(project.root / "migrations/schema_snapshot.json")
    source = script.path.read_text()
    assert 'ctx.ops.create_collection("users")' in source
    assert 'ctx.ops.create_index("users", "email", name="email_1", unique=True)' in source
    assert "users" in project.snapshot["collections"]

    # models unchanged → nothing to do
    assert project.run("diff", "--check").exit_code == 0
    none = project.run("revision", "--autogenerate", "-m", "nothing")
    assert none.exit_code == 0
    assert "No schema changes detected" in none.output
    assert len(list(project.versions.glob("*.py"))) == 1

    # evolve the model
    project.models(V2)
    diff = project.json("diff")
    kinds = {(c["kind"], c["path"]) for c in diff["changes"]}
    assert ("field_added", "status") in kinds
    assert diff["rename_hints"][0]["flag"] == "--rename users.first_name:given_name"

    second = project.json(
        "revision", "--autogenerate", "-m", "evolve", "--rename", "users.first_name:given_name"
    )
    assert second["down_revision"] == gen["revision"]
    assert second["review"] == []
    source = (project.root / second["path"]).read_text()
    ast.parse(source)
    assert 'ctx.ops.rename_field("users", "first_name", "given_name")' in source
    assert '{"$set": {"status": "active"}}' in source
    assert "users: + age: int | null = None  [SAFE]" in source  # docstring summary
    assert project.run("diff", "--check").exit_code == 0


def test_review_items_are_reported(project: Project) -> None:
    project.models(V1)
    project.run("revision", "--autogenerate", "-m", "initial")
    project.models(V1.replace("email: str", "email: str\n    phone: str"))
    result = project.run("revision", "--autogenerate", "-m", "phone")
    assert result.exit_code == 0
    assert "1 item(s) need review" in result.output
    source = next(project.versions.glob("*phone.py")).read_text()
    assert "TODO(review): phone" in source
    assert "Needs review" in source


def test_baseline(project: Project) -> None:
    project.models(V1)
    result = project.json("baseline")
    assert result["collections"] == ["users"]
    source = (project.root / result["path"]).read_text()
    assert "ctx.ops" not in source.split('"""', 2)[2]  # no operations
    assert project.run("diff", "--check").exit_code == 0

    again = project.run("baseline")
    assert again.exit_code == 1
    assert "already describes collections" in again.output
    assert project.run("baseline", "--force").exit_code == 0


def test_consistency_warnings(project: Project) -> None:
    project.models(V1)
    project.run("revision", "--autogenerate", "-m", "initial")

    snap = project.root / "migrations" / "schema_snapshot.json"
    snap.write_text(snap.read_text().replace('"email_1"', '"email_idx"'))
    assert any("changed since head revision" in w for w in project.json("diff")["warnings"])

    env = project.root / "migrations" / "env.py"
    env.write_text(env.read_text().replace('storage="python"', 'storage="json"'))
    assert any("storage profile changed" in w for w in project.json("diff")["warnings"])


def test_autogenerate_with_multiple_heads(project: Project) -> None:
    project.models(V1)
    first = project.json("revision", "-m", "a")["revision"]
    project.json("revision", "-m", "b")
    project.json("revision", "-m", "c", "--head", first)
    result = project.run("revision", "--autogenerate", "-m", "x")
    assert result.exit_code == 4


def test_bad_rename_flag(project: Project) -> None:
    project.models(V1)
    result = project.run("diff", "--rename", "first_name:given_name")
    assert result.exit_code == 1
    assert "COLLECTION.OLD_FIELD:NEW_FIELD" in result.output


BASE = """
@collection("users")
class User(BaseModel):
    email: str
"""


def two_branches(project: Project) -> tuple[str, str, str]:
    """Two developers each add a field on their own branch, starting from the same revision.

    Returns (snapshot after branch A, snapshot after branch B, base revision).
    """
    project.models(BASE)
    base = project.json("revision", "--autogenerate", "-m", "initial")["revision"]
    snap = project.root / "migrations" / "schema_snapshot.json"
    base_snapshot = snap.read_text()

    project.models(BASE + '    nickname: str = ""\n')  # developer A
    project.json("revision", "--autogenerate", "-m", "add nickname")
    snapshot_a = snap.read_text()

    snap.write_text(base_snapshot)  # developer B, branched before A's change
    project.models(BASE + "    age: int = 0\n")
    project.json("revision", "--autogenerate", "-m", "add age", "--head", base)
    snapshot_b = snap.read_text()

    project.models(BASE + '    nickname: str = ""\n    age: int = 0\n')  # git merged the models
    return snapshot_a, snapshot_b, base


def test_merge_rebuilds_a_conflicted_snapshot(project: Project) -> None:
    snapshot_a, snapshot_b, _ = two_branches(project)
    snap = project.root / "migrations" / "schema_snapshot.json"
    snap.write_text(f"<<<<<<< HEAD\n{snapshot_a}=======\n{snapshot_b}>>>>>>> branch-b\n")

    assert len(project.json("heads")["heads"]) == 2
    result = project.json("merge", "-m", "merge branches")
    assert result["snapshot_had_conflicts"] is True
    assert len(project.json("heads")["heads"]) == 1
    assert project.run("diff", "--check").exit_code == 0  # nothing generated twice
    assert project.run("validate").exit_code == 0


def test_merge_lists_what_it_absorbs(project: Project) -> None:
    snapshot_a, _, _ = two_branches(project)
    (project.root / "migrations" / "schema_snapshot.json").write_text(snapshot_a)  # "ours"
    result = project.run("merge")
    assert result.exit_code == 0, result.output
    assert "now includes" in result.output
    assert "users: + age: int = 0" in result.output  # branch B's change, already migrated
    assert project.run("diff", "--check").exit_code == 0


def test_merge_without_models_leaves_snapshot_alone(project: Project) -> None:
    env = project.root / "migrations" / "env.py"
    env.write_text("target_metadata = None\n")
    first = project.json("revision", "-m", "a")["revision"]
    project.json("revision", "-m", "b")
    project.json("revision", "-m", "c", "--head", first)
    assert project.json("merge")["snapshot_rebuilt"] is False
