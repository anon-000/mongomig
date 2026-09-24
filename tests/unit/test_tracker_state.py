from __future__ import annotations

from pathlib import Path

from mongomig.migrations.graph import RevisionGraph
from mongomig.migrations.script import load_scripts
from mongomig.migrations.tracker import AppliedRecord, compute_state
from tests.helpers import make_revision


def rec(rev: str, status: str = "applied") -> AppliedRecord:
    return AppliedRecord.from_doc({"_id": rev, "status": status})


def build(versions_dir: Path) -> RevisionGraph:
    make_revision(versions_dir, "r1", minute=0)
    make_revision(versions_dir, "r2", "r1", minute=1)
    make_revision(versions_dir, "r3", "r2", minute=2)
    return RevisionGraph(load_scripts(versions_dir))


def test_nothing_applied(versions_dir: Path) -> None:
    state = compute_state(build(versions_dir), [])
    assert state.applied_heads == []
    assert state.pending == ["r1", "r2", "r3"]


def test_partially_applied(versions_dir: Path) -> None:
    state = compute_state(build(versions_dir), [rec("r1"), rec("r2")])
    assert state.applied_heads == ["r2"]
    assert state.pending == ["r3"]
    assert state.unknown == []


def test_failed_and_unknown(versions_dir: Path) -> None:
    state = compute_state(build(versions_dir), [rec("r1"), rec("r2", "failed"), rec("zz")])
    assert state.applied_heads == ["r1"]
    assert state.pending == ["r2", "r3"]  # failed counts as not applied
    assert state.failed == ["r2"]
    assert state.unknown == ["zz"]


def test_record_from_doc_down_revision_shapes() -> None:
    assert AppliedRecord.from_doc({"_id": "a"}).down_revisions == ()
    assert AppliedRecord.from_doc({"_id": "a", "down_revision": "b"}).down_revisions == ("b",)
    assert AppliedRecord.from_doc({"_id": "a", "down_revision": ["b", "c"]}).down_revisions == (
        "b",
        "c",
    )
