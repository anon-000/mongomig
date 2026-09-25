"""Revision ids, file names and rendering of new revision files."""

from __future__ import annotations

import json
import re
import secrets
import unicodedata
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from string import Template

from mongomig.errors import ScriptError

REVISION_ID_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")

DEFAULT_UPGRADE_BODY = """\
    # Examples:
    #   ctx.ops.create_index("users", "email", unique=True)
    #   ctx.ops.backfill("users", {"status": {"$exists": False}}, {"$set": {"status": "active"}})
    #   users = ctx.collection("users")  # plain PyMongo collection for custom logic
    pass"""
DEFAULT_DOWNGRADE_BODY = "    pass"
MAX_SLUG_LENGTH = 40


def new_revision_id() -> str:
    """12 random hex chars: collisions between developers are practically impossible."""
    return secrets.token_hex(6)


def validate_revision_id(rev_id: str) -> str:
    if not REVISION_ID_RE.match(rev_id):
        raise ScriptError(
            f"Invalid revision id {rev_id!r}.",
            suggestion="Use 1-64 letters, digits or underscores.",
        )
    return rev_id


def slugify(message: str) -> str:
    ascii_text = (
        unicodedata.normalize("NFKD", message).encode("ascii", "ignore").decode("ascii").lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")
    slug = slug[:MAX_SLUG_LENGTH].rstrip("_")
    return slug or "revision"


def revision_filename(rev_id: str, message: str, created: datetime) -> str:
    """``YYYYMMDD_HHMM_<id>_<slug>.py`` — sorts chronologically in a file browser."""
    return f"{created:%Y%m%d_%H%M}_{rev_id}_{slugify(message)}.py"


def render_revision(
    *,
    rev_id: str,
    message: str,
    down_revision: str | tuple[str, ...] | None,
    snapshot_hash: str | None,
    created: datetime,
    upgrade_body: str | None = None,
    downgrade_body: str | None = None,
    notes: str = "",
    reversible: bool = True,
) -> str:
    template = Template(
        files("mongomig").joinpath("templates/revision.py.tmpl").read_text(encoding="utf-8")
    )
    if isinstance(down_revision, tuple):
        down_text = ", ".join(down_revision)
    else:
        down_text = down_revision or "<base>"
    return template.substitute(
        # The docstring must not be terminated early by the message itself.
        message=_docstring_safe(message).strip() or "empty message",
        notes=f"\n{_docstring_safe(notes.rstrip())}\n" if notes.strip() else "",
        reversible=repr(reversible),
        upgrade_body=upgrade_body or DEFAULT_UPGRADE_BODY,
        downgrade_body=downgrade_body or DEFAULT_DOWNGRADE_BODY,
        revision=rev_id,
        down_revision_text=down_text,
        created=f"{created:%Y-%m-%d %H:%M:%S}",
        revision_repr=_py_literal(rev_id),
        down_revision_repr=_py_literal(down_revision),
        snapshot_hash_repr=_py_literal(snapshot_hash),
    )


def _docstring_safe(text: str) -> str:
    """Text that can't terminate or break the module docstring."""
    return text.replace("\\", "\\\\").replace('"""', "'''")


def _py_literal(value: str | tuple[str, ...] | None) -> str:
    """Python literal using double quotes (black/ruff style) for generated files."""
    if value is None:
        return "None"
    if isinstance(value, tuple):
        return "(" + ", ".join(json.dumps(v) for v in value) + ")"
    return json.dumps(value)


def write_revision(
    versions_dir: Path,
    *,
    message: str,
    down_revision: str | tuple[str, ...] | None,
    snapshot_hash: str | None,
    rev_id: str | None = None,
    now: datetime | None = None,
    upgrade_body: str | None = None,
    downgrade_body: str | None = None,
    notes: str = "",
    reversible: bool = True,
) -> tuple[str, Path]:
    rev_id = validate_revision_id(rev_id) if rev_id else new_revision_id()
    created = now or datetime.now(UTC)
    path = versions_dir / revision_filename(rev_id, message, created)
    if path.exists():
        raise ScriptError(f"Refusing to overwrite existing file {path}")
    content = render_revision(
        rev_id=rev_id,
        message=message,
        down_revision=down_revision,
        snapshot_hash=snapshot_hash,
        created=created,
        upgrade_body=upgrade_body,
        downgrade_body=downgrade_body,
        notes=notes,
        reversible=reversible,
    )
    import ast

    try:
        ast.parse(content)
    except SyntaxError as exc:  # never write a broken file
        raise ScriptError(f"Generated revision is not valid Python: {exc}") from exc
    versions_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return rev_id, path
