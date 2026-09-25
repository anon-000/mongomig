from __future__ import annotations

from pathlib import Path

import pytest

from mongomig.migrations.dryrun import RecordedOp, Recorder, update_summary
from mongomig.migrations.executor import confirmation_reasons
from mongomig.migrations.script import load_script
from mongomig.safety.impact import Risk, assess, destructive_calls_in
from tests.helpers import write_migration


def op(**kw: object) -> RecordedOp:
    base: dict[str, object] = {"collection": "users", "operation": "backfill"}
    base.update(kw)
    return RecordedOp(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("ops", "kwargs", "risk"),
    [
        ([], {}, Risk.LOW),
        ([op(estimated_docs=50_000)], {}, Risk.LOW),
        ([op(estimated_docs=200_000)], {}, Risk.MEDIUM),
        ([op(estimated_docs=2_000_000, collection_scan=False)], {}, Risk.MEDIUM),
        ([op(estimated_docs=2_000_000, collection_scan=True)], {}, Risk.HIGH),
        ([op(operation="create_index", estimated_docs=2_000_000)], {}, Risk.MEDIUM),
        (
            [op(operation="create_index", estimated_docs=10, warnings=["will fail: dup"])],
            {},
            Risk.HIGH,
        ),
        ([op(operation="unset_field", destructive=True, estimated_docs=1)], {}, Risk.HIGH),
        ([op(exact=False, estimated_docs=1)], {}, Risk.MEDIUM),
        ([], {"reversible": False}, Risk.MEDIUM),
        ([], {"unavailable": "uses ctx.unsafe_db"}, Risk.MEDIUM),
        ([], {"error": "KeyError: 'x'"}, Risk.HIGH),
    ],
)
def test_risk_rules(ops: list[RecordedOp], kwargs: dict, risk: Risk) -> None:  # type: ignore[type-arg]
    kwargs.setdefault("reversible", True)
    result = assess(ops, **kwargs)
    assert result.risk == risk
    assert bool(result.reasons) == (risk != Risk.LOW)


def test_destructive_calls_static_scan() -> None:
    source = """
def upgrade(ctx):
    # ctx.ops.unset_field("users", "commented")   <- comments don't count
    ctx.ops.unset_field("users", "safe", backup=True)
    ctx.ops.drop_collection("tmp", backup=True)
    ctx.ops.unset_field("users", "legacy")
    ctx.collection("logs").delete_many({})
    ctx.collection("x").drop()
    ctx.ops.drop_index("users", "a_1")   # not data loss

def downgrade(ctx):
    ctx.ops.drop_collection("orders")
"""
    found = destructive_calls_in(source, "upgrade")
    assert len(found) == 3
    assert "legacy" in found[0]
    assert "delete_many" in found[1]
    assert "drop()" in found[2]
    assert len(destructive_calls_in(source, "downgrade")) == 1


def test_confirmation_modes(versions_dir: Path) -> None:
    safe = load_script(
        write_migration(versions_dir, "safe1", upgrade='ctx.ops.create_index("u", "a")')
    )
    risky = load_script(
        write_migration(
            versions_dir, "risky2", "safe1", upgrade='ctx.ops.unset_field("u", "x")', minute=1
        )
    )
    oneway = load_script(
        write_migration(
            versions_dir, "oneway3", "risky2", reversible=False, downgrade=None, minute=2
        )
    )

    assert confirmation_reasons([safe], "destructive") == []
    reasons = confirmation_reasons([safe, risky, oneway], "destructive")
    assert len(reasons) == 2
    assert reasons[0].startswith("risky2 can delete data: line")
    assert reasons[1] == "oneway3 is irreversible"
    assert confirmation_reasons([safe], "always") == [
        "1 revision(s) will be applied (execution.confirm = always)"
    ]
    assert confirmation_reasons([risky], "never") == []
    assert confirmation_reasons([], "always") == []


def test_recorder_merges_loops_of_raw_writes() -> None:
    rec = Recorder()
    for _ in range(3):
        rec.record(op(operation="update_one", exact=False, estimated_docs=1))
    rec.record(op(operation="backfill"))
    rec.record(op(operation="backfill"))  # ctx.ops calls are never merged
    assert [(o.operation, o.calls) for o in rec.ops] == [
        ("update_one", 3),
        ("backfill", 1),
        ("backfill", 1),
    ]
    assert rec.ops[0].estimated_docs == 3


def test_update_summary() -> None:
    assert update_summary({"$set": {"a": 1, "b": 2}, "$unset": {"c": ""}}) == "$set a, b; $unset c"
    assert update_summary([{"$set": {}}]) == "pipeline update"
