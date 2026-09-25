from __future__ import annotations

from pathlib import Path

import pytest

from mongomig.errors import MultipleHeadsError, RevisionNotFoundError, ValidationError
from mongomig.migrations.executor import plan_downgrade, plan_upgrade, stamp_set
from mongomig.migrations.graph import RevisionGraph
from mongomig.migrations.script import load_scripts
from mongomig.schema.indexes import normalize_index_keys
from tests.helpers import make_revision


def revs(scripts: list) -> list[str]:  # type: ignore[type-arg]
    return [s.revision for s in scripts]


@pytest.fixture
def linear(versions_dir: Path) -> RevisionGraph:
    make_revision(versions_dir, "r1", minute=0)
    make_revision(versions_dir, "r2", "r1", minute=1)
    make_revision(versions_dir, "r3", "r2", minute=2)
    make_revision(versions_dir, "r4", "r3", minute=3)
    return RevisionGraph(load_scripts(versions_dir))


@pytest.fixture
def branched(versions_dir: Path) -> RevisionGraph:
    make_revision(versions_dir, "base", minute=0)
    make_revision(versions_dir, "left", "base", minute=1)
    make_revision(versions_dir, "right", "base", minute=2)
    return RevisionGraph(load_scripts(versions_dir))


def test_upgrade_to_head(linear: RevisionGraph) -> None:
    assert revs(plan_upgrade(linear, set())) == ["r1", "r2", "r3", "r4"]
    assert revs(plan_upgrade(linear, {"r1", "r2"})) == ["r3", "r4"]
    assert plan_upgrade(linear, {"r1", "r2", "r3", "r4"}) == []


def test_upgrade_to_revision_and_steps(linear: RevisionGraph) -> None:
    assert revs(plan_upgrade(linear, {"r1"}, "r3")) == ["r2", "r3"]
    assert revs(plan_upgrade(linear, {"r1"}, steps=1)) == ["r2"]
    assert plan_upgrade(linear, {"r1", "r2", "r3"}, "r2") == []
    with pytest.raises(ValidationError):
        plan_upgrade(linear, set(), steps=0)


def test_upgrade_retries_failed_gaps(linear: RevisionGraph) -> None:
    # r2 failed earlier (not in applied set) but r1 is applied: r2 is re-run first
    assert revs(plan_upgrade(linear, {"r1"})) == ["r2", "r3", "r4"]


def test_upgrade_with_multiple_heads(branched: RevisionGraph) -> None:
    with pytest.raises(MultipleHeadsError):
        plan_upgrade(branched, set(), "head")
    assert revs(plan_upgrade(branched, set(), "heads")) == ["base", "left", "right"]
    assert revs(plan_upgrade(branched, set(), "right")) == ["base", "right"]


def test_downgrade_default_one_step(linear: RevisionGraph) -> None:
    applied = {"r1", "r2", "r3"}
    assert revs(plan_downgrade(linear, applied)) == ["r3"]
    assert revs(plan_downgrade(linear, applied, steps=2)) == ["r3", "r2"]
    assert revs(plan_downgrade(linear, applied, "base")) == ["r3", "r2", "r1"]
    assert plan_downgrade(linear, set()) == []


def test_downgrade_to_revision_keeps_it(linear: RevisionGraph) -> None:
    applied = {"r1", "r2", "r3", "r4"}
    assert revs(plan_downgrade(linear, applied, "r2")) == ["r4", "r3"]
    with pytest.raises(RevisionNotFoundError, match="not applied"):
        plan_downgrade(linear, {"r1"}, "r3")
    with pytest.raises(ValidationError, match="not both"):
        plan_downgrade(linear, applied, "r2", steps=1)


def test_downgrade_branches_children_first(branched: RevisionGraph) -> None:
    applied = {"base", "left", "right"}
    plan = revs(plan_downgrade(branched, applied, "base"))
    assert plan[-1] == "base"
    assert set(plan[:2]) == {"left", "right"}


def test_stamp_set(linear: RevisionGraph) -> None:
    assert revs(stamp_set(linear, ["r2"])) == ["r1", "r2"]
    assert revs(stamp_set(linear, ["head"])) == ["r1", "r2", "r3", "r4"]
    assert stamp_set(linear, ["base"]) == []


def test_stamp_set_heads(branched: RevisionGraph) -> None:
    assert revs(stamp_set(branched, ["heads"])) == ["base", "left", "right"]


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ("email", [("email", 1)]),
        (["a", "b"], [("a", 1), ("b", 1)]),
        ([("a", 1), ("b", -1)], [("a", 1), ("b", -1)]),
        ({"loc": "2dsphere"}, [("loc", "2dsphere")]),
        ([("$**", 1)], [("$**", 1)]),
    ],
)
def test_normalize_index_keys(keys: object, expected: list) -> None:  # type: ignore[type-arg]
    assert normalize_index_keys(keys) == expected  # type: ignore[arg-type]


def test_normalize_index_keys_rejects_garbage() -> None:
    with pytest.raises(TypeError):
        normalize_index_keys([1, 2])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="at least one"):
        normalize_index_keys([])
