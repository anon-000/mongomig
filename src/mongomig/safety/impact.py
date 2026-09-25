"""Impact analysis: what a migration touches, how risky it is, and whether it deletes data.

Risk is an operational label (PRD §26), not a guarantee:

- HIGH: deletes data without a backup, would fail (duplicates for a unique index), crashed in
  the dry run, or rewrites >= 1M documents with a collection scan
- MEDIUM: rewrites >= 100k documents, builds an index on >= 1M documents, is irreversible, or
  contains custom code whose impact is only estimated
- LOW: everything else
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pymongo.collection import Collection

    from mongomig.migrations.dryrun import RecordedOp
    from mongomig.migrations.script import Script

LARGE_DOCS = 1_000_000
MEDIUM_DOCS = 100_000
CHECK_TIMEOUT_MS = 10_000


class Risk(IntEnum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2


@dataclass
class Assessment:
    risk: Risk = Risk.LOW
    reasons: list[str] = field(default_factory=list)

    def raise_to(self, risk: Risk, reason: str) -> None:
        self.risk = max(self.risk, risk)
        if reason not in self.reasons:
            self.reasons.append(reason)


def assess(
    ops: list[RecordedOp],
    *,
    reversible: bool,
    unavailable: str | None = None,
    error: str | None = None,
) -> Assessment:
    a = Assessment()
    if error:
        a.raise_to(Risk.HIGH, f"the dry run raised {error}")
    if unavailable:
        a.raise_to(Risk.MEDIUM, f"not fully simulated: {unavailable}")
    if not reversible:
        a.raise_to(Risk.MEDIUM, "irreversible (reversible = False)")
    for op in ops:
        docs = op.estimated_docs or 0
        where = f"{op.collection}.{op.operation}"
        if op.destructive:
            a.raise_to(Risk.HIGH, f"deletes data without a backup ({where})")
        if any("will fail" in w for w in op.warnings):
            a.raise_to(Risk.HIGH, f"expected to fail ({where})")
        if op.operation.startswith("create_index"):
            if docs >= LARGE_DOCS:
                a.raise_to(Risk.MEDIUM, f"index build over ~{_short(docs)} documents")
            continue
        if docs >= LARGE_DOCS and op.collection_scan:
            a.raise_to(Risk.HIGH, f"~{_short(docs)} documents with a collection scan ({where})")
        elif docs >= MEDIUM_DOCS:
            a.raise_to(Risk.MEDIUM, f"~{_short(docs)} documents ({where})")
        if not op.exact:
            a.raise_to(Risk.MEDIUM, "custom code: document counts are estimates")
    return a


def uses_collection_scan(
    coll: Collection[dict[str, Any]], filter: Mapping[str, Any] | None
) -> bool | None:
    """Does ``filter`` need a full collection scan? ``None`` if it can't be determined."""
    from pymongo.errors import PyMongoError

    if not filter:
        return True
    try:
        plan = coll.find(dict(filter)).explain()
    except PyMongoError:
        return None
    return _has_stage(plan.get("queryPlanner", {}).get("winningPlan", {}), "COLLSCAN")


def _has_stage(node: Any, stage: str) -> bool:
    if isinstance(node, Mapping):
        if node.get("stage") == stage:
            return True
        return any(_has_stage(v, stage) for v in node.values())
    if isinstance(node, list):
        return any(_has_stage(v, stage) for v in node)
    return False


def find_duplicates(
    coll: Collection[dict[str, Any]],
    keys: list[tuple[str, Any]],
    *,
    sparse: bool = False,
    partial: Mapping[str, Any] | None = None,
) -> tuple[bool | None, Any]:
    """Would a unique index on ``keys`` fail? Returns (has_duplicates, example key).

    ``None`` when the check timed out (large collection) or failed.
    """
    from pymongo.errors import PyMongoError

    pipeline: list[dict[str, Any]] = []
    if partial:
        pipeline.append({"$match": dict(partial)})
    if sparse:
        pipeline.append({"$match": {"$or": [{k: {"$exists": True}} for k, _ in keys]}})
    group_id = {k.replace(".", "__"): f"${k}" for k, _ in keys}
    pipeline += [
        {"$group": {"_id": group_id, "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
        {"$limit": 1},
    ]
    try:
        found = list(coll.aggregate(pipeline, maxTimeMS=CHECK_TIMEOUT_MS, allowDiskUse=True))
    except PyMongoError:
        return None, None
    if found:
        return True, found[0]["_id"]
    return False, None


# --- static analysis (no execution) ------------------------------------------------------

_DELETING_CALLS = frozenset(
    {
        "drop_collection",
        "unset_field",
        "drop",
        "delete_many",
        "delete_one",
        "find_one_and_delete",
        "drop_database",
    }
)


def destructive_calls(script: Script) -> list[str]:
    """Calls in the revision file that can delete data (reading the code, not running it).

    ``unset_field(..., backup=True)`` and ``drop_collection(..., backup=True)`` don't count:
    their data can be restored.
    """
    return destructive_calls_in(Path(script.path).read_text(encoding="utf-8"), "upgrade")


def destructive_calls_in(source: str, function: str) -> list[str]:
    tree = ast.parse(source)
    found: list[str] = []
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef) and node.name == function):
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            name = call.func.attr
            if name not in _DELETING_CALLS:
                continue
            if name in ("drop_collection", "unset_field") and _has_true_kwarg(call, "backup"):
                continue
            found.append(f"line {call.lineno}: {ast.unparse(call)[:80]}")
    return found


def _has_true_kwarg(call: ast.Call, name: str) -> bool:
    return any(
        kw.arg == name and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in call.keywords
    )


def _short(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)
