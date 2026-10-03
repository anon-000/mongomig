from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mongomig.cli.app import app
from mongomig.errors import RevisionConflictError, RevisionNotFoundError, ValidationError
from mongomig.migrations.graph import RevisionGraph, build_graph
from mongomig.migrations.script import load_script, load_scripts
from mongomig.migrations.squash import analyze, linear_chain
from mongomig.migrations.tracker import AppliedRecord, compute_state
from tests.helpers import write_file, write_migration

runner = CliRunner()


def chain(versions: Path, *bodies: str) -> list[str]:
    revs = []
    for i, body in enumerate(bodies):
        rev = f"r{i + 1}"
        write_migration(versions, rev, revs[-1] if revs else None, upgrade=body, minute=i)
        revs.append(rev)
    return revs


def test_analyze_folds_schema_operations(versions_dir: Path) -> None:
    chain(
        versions_dir,
        'ctx.ops.create_collection("users", capped=False)\n'
        'ctx.ops.create_index("users", "email", unique=True, name="email_u")\n'
        'ctx.ops.create_index("users", "legacy")',
        'ctx.ops.backfill("users", {}, {"$set": {"s": 1}})\n'
        'ctx.ops.drop_index("users", "legacy_1")\n'
        'ctx.ops.set_validator("users", {"$jsonSchema": {}}, level="strict")',
        'ctx.ops.create_collection("logs")\n'
        'ctx.ops.create_index("logs", "at")\n'
        'ctx.ops.rename_collection("logs", "events")\n'
        'ctx.ops.create_index("tmp", "x")\n'
        'ctx.ops.drop_collection("tmp")',
    )
    result = analyze(load_scripts(versions_dir))
    assert list(result.collections) == ["users", "events"]
    users = result.collections["users"]
    assert users.options == {"capped": False}
    assert list(users.indexes) == ["email_u"]
    assert users.indexes["email_u"].unique
    assert users.validation_level == "strict"
    assert list(result.collections["events"].indexes) == ["at_1"]
    assert result.data_ops_skipped == 1
    assert result.review == []


def test_analyze_reports_what_it_cannot_carry_over(versions_dir: Path) -> None:
    chain(
        versions_dir,
        'ctx.collection("plans").insert_many([{"n": 1}])',
        'ctx.collection("x").update_one({"k": 1}, {"$set": {"v": 1}}, upsert=True)\n'
        'ctx.collection("x").update_many({}, {"$set": {"v": 2}})',
        'ctx.collection("x").create_index("v")\nname = "users"\nctx.ops.create_index(name, "a")',
        'for c in ["a", "b"]:\n'
        '    ctx.ops.create_index(c, "k")\n'
        'for c in ["a", "b"]:\n'
        '    ctx.ops.backfill(c, {}, {"$set": {"k": 1}})\n'
        'ctx.unsafe_db.command("ping")',
    )
    review = analyze(load_scripts(versions_dir)).review
    text = "\n".join(review)
    assert "r1 line" in review[0]
    assert "may insert documents (insert_many)" in text
    assert "may insert documents (update_one)" in text
    assert "update_many" not in text  # plain updates do nothing in an empty database
    assert "changes the schema outside ctx.ops (create_index)" in text
    assert "non-literal arguments" in text
    assert "ctx.ops.create_index inside a condition or loop" in text
    assert "backfill inside" not in text
    assert "uses ctx.unsafe_db" in text


def test_linear_chain_rules(versions_dir: Path) -> None:
    chain(versions_dir, "pass", "pass", "pass")
    graph = RevisionGraph(load_scripts(versions_dir))
    assert linear_chain(graph, "r3") == ["r1", "r2", "r3"]
    assert linear_chain(graph, "r2") == ["r1", "r2"]
    with pytest.raises(ValidationError, match="first revision"):
        linear_chain(graph, "r1")

    write_migration(versions_dir, "b1", "r1", minute=10)  # branch at r1
    with pytest.raises(ValidationError, match="branches at r1"):
        linear_chain(RevisionGraph(load_scripts(versions_dir)), "r3")
    write_migration(versions_dir, "m1", ("r3", "b1"), minute=11)
    with pytest.raises(ValidationError, match="merge"):
        linear_chain(RevisionGraph(load_scripts(versions_dir)), "m1")


def squashed_project(versions: Path) -> None:
    """r1..r3 squashed into s1 (archived), r4 still points at r3."""
    archive = versions / "_squashed" / "s1"
    archive.mkdir(parents=True)
    for i, rev in enumerate(["r1", "r2", "r3"]):
        write_migration(archive, rev, f"r{i}" if i else None, minute=i)
    write_file(
        versions / "20260101_0000_s1_squash.py",
        'revision = "s1"\ndown_revision = None\nreplaces = ("r1", "r2", "r3")\n'
        "def upgrade(ctx): pass\ndef downgrade(ctx): pass\n",
    )
    write_migration(versions, "r4", "r3", minute=30)


def rec(rev: str, status: str = "applied") -> AppliedRecord:
    return AppliedRecord.from_doc({"_id": rev, "status": status})


def test_graph_maps_replaced_ids_to_the_squash(versions_dir: Path) -> None:
    squashed_project(versions_dir)
    graph = build_graph(versions_dir)
    assert graph.topological_order() == ["s1", "r4"]
    assert graph.parents["r4"] == ("s1",)
    assert graph.replaced_by("s1") == {"r1", "r2", "r3"}
    with pytest.raises(RevisionNotFoundError, match="squashed into s1"):
        graph.resolve("r2")

    write_migration(versions_dir, "r2", "r1", minute=40)  # replaced file back in versions/
    with pytest.raises(RevisionConflictError, match="still in versions"):
        build_graph(versions_dir)


def test_state_with_squashes(versions_dir: Path) -> None:
    squashed_project(versions_dir)
    graph = build_graph(versions_dir)

    old = compute_state(graph, [rec("r1"), rec("r2"), rec("r3")])
    assert old.adoptable == ["s1"]
    assert old.pending == ["r4"]
    assert old.unknown == []  # replaced ids aren't "unknown"

    partial = compute_state(graph, [rec("r1")])
    assert partial.partial == {"s1": ["r2", "r3"]}
    assert partial.pending == ["s1", "r4"]

    fresh = compute_state(graph, [])
    assert (fresh.adoptable, fresh.partial, fresh.pending) == ([], {}, ["s1", "r4"])

    adopted = compute_state(graph, [rec("s1"), rec("r1"), rec("r2"), rec("r3")])
    assert adopted.adoptable == []


def test_nested_squash(versions_dir: Path) -> None:
    squashed_project(versions_dir)
    # squash again: s1 + r4 into s2
    archive = versions_dir / "_squashed" / "s2"
    archive.mkdir()
    for name in ("20260101_0000_s1_squash.py", next(p.name for p in versions_dir.glob("*_r4.py"))):
        (versions_dir / name).rename(archive / name)
    write_file(
        versions_dir / "s2.py",
        'revision = "s2"\nreplaces = ("s1", "r4")\n'
        "def upgrade(ctx): pass\ndef downgrade(ctx): pass\n",
    )
    write_migration(versions_dir, "r5", "r4", minute=50)
    graph = build_graph(versions_dir)
    assert graph.parents["r5"] == ("s2",)
    assert graph.replaced_by("s2") == {"s1", "r4", "r1", "r2", "r3"}
    # a database that only ever ran r1..r4 (never adopted s1) adopts s2 directly
    state = compute_state(graph, [rec("r1"), rec("r2"), rec("r3"), rec("r4")])
    assert state.adoptable == ["s2"]
    assert state.pending == ["r5"]


def test_squash_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    versions = tmp_path / "migrations" / "versions"
    chain(
        versions,
        'ctx.ops.create_index("users", "email")',
        'ctx.ops.create_index("users", "x")',
        "pass",
    )

    preview = json.loads(runner.invoke(app, ["--json", "squash", "r2", "--dry-run"]).stdout)
    assert preview["replaces"] == ["r1", "r2"]
    assert preview["revision"] is None
    assert len(list(versions.glob("*.py"))) == 3  # dry run: nothing written

    result = json.loads(runner.invoke(app, ["--json", "squash", "r2", "-m", "squash"]).stdout)
    new = result["revision"]
    assert result["indexes"] == 2
    archived = sorted(
        p.stem.rsplit("_", 1)[-1] for p in (versions / "_squashed" / new).glob("*.py")
    )
    assert archived == ["r1", "r2"]
    script = load_script(tmp_path / result["path"])
    assert script.replaces == ("r1", "r2")
    assert script.down_revisions == ()
    assert 'ctx.ops.create_index("users", "email", name="email_1")' in script.path.read_text()

    history = json.loads(runner.invoke(app, ["--json", "history"]).stdout)["revisions"]
    assert [(h["revision"], h["down_revisions"]) for h in history] == [("r3", [new]), (new, [])]
    assert runner.invoke(app, ["validate"]).exit_code == 0
    assert runner.invoke(app, ["squash", "r1"]).exit_code == 1  # r1 was squashed away
