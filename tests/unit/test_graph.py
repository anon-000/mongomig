from __future__ import annotations

from pathlib import Path

import pytest

from mongomig.errors import (
    AmbiguousRevisionError,
    ExitCode,
    MultipleHeadsError,
    RevisionConflictError,
    RevisionNotFoundError,
)
from mongomig.migrations.graph import RevisionGraph
from mongomig.migrations.script import load_scripts
from tests.helpers import make_revision, write_file


def graph_of(versions_dir: Path) -> RevisionGraph:
    return RevisionGraph(load_scripts(versions_dir))


def test_empty_graph(versions_dir: Path) -> None:
    graph = graph_of(versions_dir)
    assert graph.heads() == []
    assert graph.single_head() is None
    with pytest.raises(RevisionNotFoundError):
        graph.resolve("head")


def test_linear_chain(versions_dir: Path) -> None:
    make_revision(versions_dir, "aaaa00000001", minute=0)
    make_revision(versions_dir, "bbbb00000002", "aaaa00000001", minute=1)
    make_revision(versions_dir, "cccc00000003", "bbbb00000002", minute=2)
    graph = graph_of(versions_dir)
    assert graph.topological_order() == ["aaaa00000001", "bbbb00000002", "cccc00000003"]
    assert graph.heads() == ["cccc00000003"]
    assert graph.bases() == ["aaaa00000001"]
    assert graph.single_head() == "cccc00000003"
    assert graph.ancestors("cccc00000003") == {"aaaa00000001", "bbbb00000002"}


def test_order_follows_down_revision_not_filename(versions_dir: Path) -> None:
    # Child file sorts *before* its parent by name; the graph must still order parent first.
    make_revision(versions_dir, "child0000001", "parent000001", minute=0)
    make_revision(versions_dir, "parent000001", minute=5)
    assert graph_of(versions_dir).topological_order() == ["parent000001", "child0000001"]


def test_branch_and_merge(versions_dir: Path) -> None:
    make_revision(versions_dir, "base00000000", minute=0)
    make_revision(versions_dir, "left00000000", "base00000000", minute=1)
    make_revision(versions_dir, "right0000000", "base00000000", minute=2)
    graph = graph_of(versions_dir)
    assert graph.heads() == ["left00000000", "right0000000"]
    with pytest.raises(MultipleHeadsError) as exc:
        graph.single_head()
    assert exc.value.exit_code == ExitCode.CONFLICT

    make_revision(versions_dir, "merge0000000", ("left00000000", "right0000000"), minute=3)
    graph = graph_of(versions_dir)
    assert graph.heads() == ["merge0000000"]
    assert graph.scripts["merge0000000"].is_merge
    assert graph.topological_order()[-1] == "merge0000000"


def test_missing_parent(versions_dir: Path) -> None:
    make_revision(versions_dir, "orphan000000", "doesnotexist")
    with pytest.raises(RevisionConflictError, match="does not exist"):
        graph_of(versions_dir)


def test_cycle(versions_dir: Path) -> None:
    make_revision(versions_dir, "aaaa00000001", "bbbb00000002")
    make_revision(versions_dir, "bbbb00000002", "aaaa00000001")
    with pytest.raises(RevisionConflictError, match="cycle"):
        graph_of(versions_dir)


def test_resolve(versions_dir: Path) -> None:
    make_revision(versions_dir, "abcd11110000", minute=0)
    make_revision(versions_dir, "abcd22220000", "abcd11110000", minute=1)
    graph = graph_of(versions_dir)
    assert graph.resolve("abcd11110000") == "abcd11110000"
    assert graph.resolve("abcd2") == "abcd22220000"
    assert graph.resolve("head") == "abcd22220000"
    with pytest.raises(AmbiguousRevisionError):
        graph.resolve("abcd")
    with pytest.raises(RevisionNotFoundError, match="Unknown") as exc:
        graph.resolve("abc")  # too short for prefix matching
    assert "4 characters" in (exc.value.suggestion or "")
    with pytest.raises(RevisionNotFoundError):
        graph.resolve("ffff")


def test_branch_labels_and_depends_on(versions_dir: Path) -> None:
    make_revision(versions_dir, "aaaa00000001", minute=0)
    write_file(
        versions_dir / "20260924_1300_bbbb00000002_feature.py",
        'revision = "bbbb00000002"\ndown_revision = None\nbranch_labels = ("feature",)\n'
        "def upgrade(ctx): pass\ndef downgrade(ctx): pass\n",
    )
    write_file(
        versions_dir / "20260924_1100_cccc00000003_dep.py",
        'revision = "cccc00000003"\ndown_revision = "aaaa00000001"\ndepends_on = "feature"\n'
        "def upgrade(ctx): pass\ndef downgrade(ctx): pass\n",
    )
    graph = graph_of(versions_dir)
    assert graph.resolve("feature") == "bbbb00000002"
    order = graph.topological_order()
    assert order.index("bbbb00000002") < order.index("cccc00000003")
    # depends_on is an ordering constraint, not a parent (same as Alembic): the labelled
    # branch stays its own head.
    assert set(graph.heads()) == {"bbbb00000002", "cccc00000003"}


def test_unknown_depends_on(versions_dir: Path) -> None:
    write_file(
        versions_dir / "x.py",
        'revision = "x1"\ndepends_on = "nope"\ndef upgrade(ctx): pass\ndef downgrade(ctx): pass\n',
    )
    with pytest.raises(RevisionConflictError, match="depends_on"):
        graph_of(versions_dir)
