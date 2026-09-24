"""``schema_snapshot.json``: the committed "last known expected schema".

M1 only needs the empty document and a stable hash; the schema contents arrive in M3.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SNAPSHOT_FORMAT = 1


def empty_snapshot() -> dict[str, Any]:
    return {"mongomig_format": SNAPSHOT_FORMAT, "storage": None, "collections": {}}


def canonical_json(data: Any) -> str:
    """Deterministic serialisation so git diffs and hashes are stable."""
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def snapshot_hash(path: Path) -> str | None:
    """Hash of the snapshot's *content* (not formatting); ``None`` if there is no snapshot."""
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    compact = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(compact.encode("utf-8")).hexdigest()


def write_snapshot(path: Path, data: dict[str, Any]) -> None:
    path.write_text(canonical_json(data), encoding="utf-8")
