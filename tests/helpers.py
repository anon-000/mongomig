"""Shared test helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from mongomig.migrations.revision import write_revision
from mongomig.migrations.script import Script, load_script

_BASE_TIME = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def make_revision(
    versions_dir: Path,
    rev_id: str,
    down: str | tuple[str, ...] | None = None,
    message: str | None = None,
    minute: int = 0,
) -> Script:
    """Write a real revision file (via the production renderer) and load it back."""
    _, path = write_revision(
        versions_dir,
        message=message or f"rev {rev_id}",
        down_revision=down,
        snapshot_hash=None,
        rev_id=rev_id,
        now=_BASE_TIME + timedelta(minutes=minute),
    )
    return load_script(path)


def write_file(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
