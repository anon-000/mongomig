"""Read revision files.

Metadata is read by parsing the file's AST instead of importing it, so offline commands
(``history``, ``heads``, ``revision``) are fast and never execute migration code or require
the application's dependencies. The module is only imported when a migration actually runs.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mongomig.errors import RevisionConflictError, ScriptError
from mongomig.migrations.revision import REVISION_ID_RE

SUPPORTED_FORMAT = 1
_METADATA_NAMES = frozenset(
    {
        "revision",
        "down_revision",
        "branch_labels",
        "depends_on",
        "reversible",
        "snapshot_hash",
        "mongomig_format",
    }
)


@dataclass(frozen=True)
class Script:
    revision: str
    down_revisions: tuple[str, ...]
    path: Path
    message: str
    branch_labels: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    reversible: bool = True
    snapshot_hash: str | None = None
    format_version: int = SUPPORTED_FORMAT
    checksum: str = ""
    has_downgrade: bool = True

    @property
    def is_base(self) -> bool:
        return not self.down_revisions

    @property
    def is_merge(self) -> bool:
        return len(self.down_revisions) > 1


def file_checksum(source: bytes) -> str:
    """sha256 over the file with normalised newlines (CRLF checkouts must not look modified)."""
    normalised = source.replace(b"\r\n", b"\n")
    return "sha256:" + hashlib.sha256(normalised).hexdigest()


def load_script(path: Path) -> Script:
    try:
        source = path.read_bytes()
    except OSError as exc:
        raise ScriptError(f"Cannot read {path}: {exc.strerror}") from None
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise ScriptError(
            f"Syntax error in {path.name} line {exc.lineno}: {exc.msg}",
            details={"path": str(path)},
        ) from None

    values, functions = _top_level(tree, path)

    def fail(message: str, suggestion: str | None = None) -> ScriptError:
        return ScriptError(
            f"{path.name}: {message}", suggestion=suggestion, details={"path": str(path)}
        )

    revision = values.get("revision")
    if not isinstance(revision, str) or not REVISION_ID_RE.match(revision):
        raise fail("missing or invalid `revision` (expected a string id).")

    fmt = values.get("mongomig_format", SUPPORTED_FORMAT)
    if not isinstance(fmt, int) or fmt < 1:
        raise fail("`mongomig_format` must be a positive integer.")
    if fmt > SUPPORTED_FORMAT:
        raise fail(
            f"written for migration format {fmt}; this mongomig supports up to {SUPPORTED_FORMAT}.",
            "Upgrade mongomig: pip install -U mongomig",
        )

    reversible = values.get("reversible", True)
    if not isinstance(reversible, bool):
        raise fail("`reversible` must be True or False.")

    if "upgrade" not in functions:
        raise fail("missing `def upgrade(ctx)`.")
    has_downgrade = "downgrade" in functions
    if not has_downgrade and reversible:
        raise fail(
            "missing `def downgrade(ctx)`.",
            "Add a downgrade function, or set `reversible = False` if it cannot be undone.",
        )

    snapshot = values.get("snapshot_hash")
    if snapshot is not None and not isinstance(snapshot, str):
        raise fail("`snapshot_hash` must be a string or None.")

    return Script(
        revision=revision,
        down_revisions=_id_tuple(values.get("down_revision"), "down_revision", fail),
        path=path,
        message=_message(tree),
        branch_labels=_id_tuple(values.get("branch_labels"), "branch_labels", fail),
        depends_on=_id_tuple(values.get("depends_on"), "depends_on", fail),
        reversible=reversible,
        snapshot_hash=snapshot,
        format_version=fmt,
        checksum=file_checksum(source),
        has_downgrade=has_downgrade,
    )


def load_scripts(versions_dir: Path) -> list[Script]:
    """Load every revision file in ``versions_dir`` (non-recursive; ``_*`` and ``.*`` skipped)."""
    if not versions_dir.is_dir():
        return []
    scripts: list[Script] = []
    seen: dict[str, Path] = {}
    for path in sorted(versions_dir.glob("*.py")):
        if path.name.startswith(("_", ".")):
            continue
        script = load_script(path)
        if script.revision in seen:
            raise RevisionConflictError(
                f"Duplicate revision id {script.revision!r}.",
                suggestion="Give one of the files a new id; revision ids must be unique.",
                details={"files": [str(seen[script.revision]), str(path)]},
            )
        seen[script.revision] = path
        scripts.append(script)
    return scripts


def _top_level(tree: ast.Module, path: Path) -> tuple[dict[str, Any], set[str]]:
    """Literal metadata assignments and function names defined at module level."""
    values: dict[str, Any] = {}
    functions: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            functions.add(node.name)
            continue
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in _METADATA_NAMES:
                values[target.id] = _literal(value, target.id, path)
    return values, functions


def _literal(node: ast.expr, name: str, path: Path) -> Any:
    try:
        return ast.literal_eval(node)
    except ValueError:
        raise ScriptError(
            f"{path.name}: `{name}` must be a literal value (string, tuple, bool, None).",
            details={"path": str(path)},
        ) from None


def _id_tuple(value: Any, name: str, fail: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items: tuple[Any, ...] = (value,)
    elif isinstance(value, list | tuple):
        items = tuple(value)
    else:
        raise fail(f"`{name}` must be None, a string, or a tuple of strings.")
    if not all(isinstance(item, str) and item for item in items):
        raise fail(f"`{name}` must contain only non-empty strings.")
    if len(set(items)) != len(items):
        raise fail(f"`{name}` contains duplicates.")
    return items


def _message(tree: ast.Module) -> str:
    doc = ast.get_docstring(tree)
    if not doc:
        return ""
    return doc.strip().splitlines()[0].strip()
