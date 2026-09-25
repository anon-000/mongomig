"""Diff → ``upgrade``/``downgrade`` code using ``ctx.ops``.

Rules (PRD §64, design D18/D19):

- Only changes autogenerate can do *correctly* become live code: backfills with a known value,
  renames you asked for, indexes, validators.
- Anything that needs a decision or could lose data (unsafe type conversions, values for new
  required fields, deleting removed fields, dropping collections) is written as a commented
  ``TODO(review)`` block, never as live code.
"""

from __future__ import annotations

import keyword
from dataclasses import dataclass, field
from typing import Any

from mongomig.schema.diff import Change, DiffResult, Severity
from mongomig.schema.models import INTEGER_TYPES, CollectionSchema, FieldSchema, IndexSchema

# Execution phases: upgrade runs them in this order, downgrade in reverse.
PHASE_COLLECTIONS = 0
PHASE_DATA = 1
PHASE_DROP_INDEXES = 2
PHASE_CREATE_INDEXES = 3
PHASE_VALIDATORS = 4

LINE_LIMIT = 96


@dataclass
class Block:
    phase: int
    up: list[str] = field(default_factory=list)
    down: list[str] = field(default_factory=list)


@dataclass
class GeneratedMigration:
    upgrade: list[str]
    downgrade: list[str]
    summary: list[str]  # one line per change, for the docstring
    review: list[str]  # MANUAL_REVIEW items the developer must look at

    def body(self, lines: list[str]) -> str:
        flat = [part for line in lines for part in line.split("\n")]
        if not any(line.strip() and not line.lstrip().startswith("#") for line in flat):
            flat.append("pass")  # only comments (e.g. all TODO(review)) is not a valid body
        return "\n".join(f"    {line}" if line else "" for line in flat)

    @property
    def upgrade_body(self) -> str:
        return self.body(self.upgrade)

    @property
    def downgrade_body(self) -> str:
        return self.body(self.downgrade)


def generate(diff: DiffResult) -> GeneratedMigration:
    blocks: list[Block] = [_block(change) for change in diff.changes]
    up: list[str] = []
    for phase in sorted({b.phase for b in blocks}):
        for b in (b for b in blocks if b.phase == phase and b.up):
            if up:
                up.append("")
            up.extend(b.up)
    down: list[str] = []
    for phase in sorted({b.phase for b in blocks}, reverse=True):
        for b in reversed([b for b in blocks if b.phase == phase and b.down]):
            if down:
                down.append("")
            down.extend(b.down)

    summary = [f"{c.collection}: {c.summary}  [{c.severity.name}]" for c in diff.changes]
    review = [
        f"{c.collection}.{c.path}: {c.note}" if c.path else f"{c.collection}: {c.note}"
        for c in diff.changes
        if c.severity >= Severity.MANUAL_REVIEW
    ]
    return GeneratedMigration(upgrade=up, downgrade=down, summary=summary, review=review)


# --- per-change code ---------------------------------------------------------------------


def _block(change: Change) -> Block:
    handler = _HANDLERS.get(change.kind)
    if handler is None:  # pragma: no cover - every kind has a handler
        return Block(PHASE_DATA, up=[f"# {change.collection}: {change.summary} (not handled)"])
    return handler(change)


def _collection_added(change: Change) -> Block:
    name = change.collection
    return Block(
        PHASE_COLLECTIONS,
        up=[f"ctx.ops.create_collection({lit(name)})"],
        down=[
            f"# {name} was created by this revision. Dropping it deletes all its data:",
            f"# ctx.ops.drop_collection({lit(name)})",
        ],
    )


def _collection_removed(change: Change) -> Block:
    name = change.collection
    return Block(
        PHASE_COLLECTIONS,
        up=[
            f"# TODO(review): collection {name} is no longer in the models; its data is kept.",
            "# To remove it, keeping a restorable backup (drop the backup later with",
            "# `mongomig backups --drop <revision>`):",
            f"# ctx.ops.drop_collection({lit(name)}, backup=True)",
        ],
        down=[f"# ctx.ops.restore_collection({lit(name)})"],
    )


def _field_added(change: Change) -> Block:
    new: FieldSchema = change.new
    path = change.path or ""
    coll = change.collection
    if change.severity == Severity.SAFE:
        return Block(PHASE_DATA)  # optional field: nothing to do
    value = new.default if new.has_default and new.default_is_static else None
    if change.severity == Severity.REQUIRES_DATA_MIGRATION:
        return Block(
            PHASE_DATA,
            up=[
                f"# {path}: new required field; backfill existing documents",
                _call("ctx.ops.backfill", coll, _missing_filter(path), {"$set": {path: value}}),
            ],
            down=[_call("ctx.ops.unset_field", coll, path)],
        )
    return Block(
        PHASE_DATA,
        up=[
            f"# TODO(review): {path}: {change.note}",
            *_commented(
                _call("ctx.ops.backfill", coll, _missing_filter(path), {"$set": {path: _TODO}})
            ),
        ],
        down=_commented(_call("ctx.ops.unset_field", coll, path)) if "[]" not in path else [],
    )


def _field_removed(change: Change) -> Block:
    path = change.path or ""
    if "[]" in path:
        return Block(
            PHASE_DATA,
            up=[f"# {path} was removed from the model; existing data is kept (inside an array)."],
        )
    return Block(
        PHASE_DATA,
        up=[
            f"# {path} was removed from the model; existing data is kept.",
            "# To delete it from all documents, keeping a restorable backup:",
            *_commented(_call("ctx.ops.unset_field", change.collection, path, backup=True)),
        ],
        down=_commented(_call("ctx.ops.restore_field", change.collection, path)),
    )


def _field_renamed(change: Change) -> Block:
    old_path = change.path or ""
    new_path = change.target or ""
    coll = change.collection
    return Block(
        PHASE_DATA,
        up=[_call("ctx.ops.rename_field", coll, old_path, new_path)],
        down=[_call("ctx.ops.rename_field", coll, new_path, old_path)],
    )


def _field_type_changed(change: Change) -> Block:
    if change.severity == Severity.SAFE:
        return Block(PHASE_DATA)
    old: FieldSchema = change.old
    new: FieldSchema = change.new
    path = change.path or ""
    if "[]" in path:
        return Block(PHASE_DATA, up=[f"# TODO(review): {path}: {change.note} (inside an array)"])
    target = _convert_target(new)
    lost = sorted(_family(old) - _family(new))
    header = (
        f"# TODO(review): {path}: {change.note}"
        if change.severity >= Severity.MANUAL_REVIEW
        else f"# {path}: {change.note}"
    )
    conversion = _call(
        "ctx.ops.backfill",
        change.collection,
        {path: {"$type": _expand_int(lost)}},
        [
            {
                "$set": {
                    path: {
                        "$convert": {
                            "input": f"${path}",
                            "to": target,
                            "onError": None,
                            "onNull": None,
                        }
                    }
                }
            }
        ],
    )
    return Block(
        PHASE_DATA,
        up=[
            header,
            f"# Values that can't be converted to {target} become null; check them first.",
            *_commented(conversion),
        ],
    )


def _field_nullable_changed(change: Change) -> Block:
    new: FieldSchema = change.new
    path = change.path or ""
    if change.severity == Severity.SAFE or "[]" in path:
        return Block(
            PHASE_DATA,
            up=[]
            if change.severity == Severity.SAFE
            else [f"# TODO(review): {path}: {change.note}"],
        )
    null_filter = {path: {"$type": "null"}}
    if change.severity == Severity.REQUIRES_DATA_MIGRATION:
        return Block(
            PHASE_DATA,
            up=[
                f"# {path}: no longer nullable; replace existing nulls with the default",
                _call(
                    "ctx.ops.backfill",
                    change.collection,
                    null_filter,
                    {"$set": {path: new.default}},
                ),
            ],
        )
    return Block(
        PHASE_DATA,
        up=[
            f"# TODO(review): {path}: {change.note}",
            *_commented(
                _call("ctx.ops.backfill", change.collection, null_filter, {"$set": {path: _TODO}})
            ),
        ],
    )


def _field_required_changed(change: Change) -> Block:
    if change.severity == Severity.SAFE:
        return Block(PHASE_DATA)
    new: FieldSchema = change.new
    path = change.path or ""
    if "[]" in path:
        return Block(PHASE_DATA, up=[f"# TODO(review): {path}: {change.note}"])
    if change.severity == Severity.REQUIRES_DATA_MIGRATION:
        value = new.default if new.has_default and new.default_is_static else None
        return Block(
            PHASE_DATA,
            up=[
                f"# {path}: now required; fill documents where it is missing",
                _call(
                    "ctx.ops.backfill",
                    change.collection,
                    _missing_filter(path),
                    {"$set": {path: value}},
                ),
            ],
        )
    return Block(
        PHASE_DATA,
        up=[
            f"# TODO(review): {path}: {change.note}",
            *_commented(
                _call(
                    "ctx.ops.backfill",
                    change.collection,
                    _missing_filter(path),
                    {"$set": {path: _TODO}},
                )
            ),
        ],
    )


def _field_enum_changed(change: Change) -> Block:
    if change.severity < Severity.MANUAL_REVIEW:
        return Block(PHASE_DATA)
    old: FieldSchema = change.old
    new: FieldSchema = change.new
    path = change.path or ""
    removed = [v for v in (old.enum or ()) if v not in (new.enum or ())]
    if "[]" in path:
        return Block(PHASE_DATA, up=[f"# TODO(review): {path}: {change.note}"])
    return Block(
        PHASE_DATA,
        up=[
            f"# TODO(review): {path}: {change.note}",
            *_commented(
                _call(
                    "ctx.ops.backfill",
                    change.collection,
                    {path: {"$in": removed}},
                    {"$set": {path: _TODO}},
                )
            ),
        ],
    )


def _index_added(change: Change) -> Block:
    ix: IndexSchema = change.new
    return Block(
        PHASE_CREATE_INDEXES,
        up=[_create_index(change.collection, ix)],
        down=[_call("ctx.ops.drop_index", change.collection, ix.name)],
    )


def _index_removed(change: Change) -> Block:
    ix: IndexSchema = change.old
    return Block(
        PHASE_DROP_INDEXES,
        up=[_call("ctx.ops.drop_index", change.collection, ix.name)],
        down=[_create_index(change.collection, ix)],
    )


def _index_changed(change: Change) -> Block:
    old: IndexSchema = change.old
    new: IndexSchema = change.new
    coll = change.collection
    return Block(
        PHASE_DROP_INDEXES,
        up=[_call("ctx.ops.drop_index", coll, old.name), _create_index(coll, new)],
        down=[_call("ctx.ops.drop_index", coll, new.name), _create_index(coll, old)],
    )


def _validator_added(change: Change) -> Block:
    new: CollectionSchema = change.new
    return Block(
        PHASE_VALIDATORS,
        up=[_set_validator(change.collection, new)],
        down=[_call("ctx.ops.remove_validator", change.collection)],
    )


def _validator_changed(change: Change) -> Block:
    return Block(
        PHASE_VALIDATORS,
        up=[_set_validator(change.collection, change.new)],
        down=[_set_validator(change.collection, change.old)],
    )


def _validator_removed(change: Change) -> Block:
    return Block(
        PHASE_VALIDATORS,
        up=[
            "# The validator is no longer managed by the models and is left in place.",
            "# To remove it:",
            *_commented(_call("ctx.ops.remove_validator", change.collection)),
        ],
    )


_HANDLERS = {
    "collection_added": _collection_added,
    "collection_removed": _collection_removed,
    "field_added": _field_added,
    "field_removed": _field_removed,
    "field_renamed": _field_renamed,
    "field_type_changed": _field_type_changed,
    "field_nullable_changed": _field_nullable_changed,
    "field_required_changed": _field_required_changed,
    "field_enum_changed": _field_enum_changed,
    "index_added": _index_added,
    "index_removed": _index_removed,
    "index_changed": _index_changed,
    "validator_added": _validator_added,
    "validator_changed": _validator_changed,
    "validator_removed": _validator_removed,
}


# --- helpers -----------------------------------------------------------------------------


class _Placeholder:
    def __repr__(self) -> str:
        return "..."


_TODO = _Placeholder()  # renders as `...` inside commented-out code


def _missing_filter(path: str) -> dict[str, Any]:
    """Documents lacking ``path`` (only where its parent object exists, for nested paths)."""
    if "." not in path:
        return {path: {"$exists": False}}
    parent = path.rsplit(".", 1)[0]
    return {parent: {"$type": "object"}, path: {"$exists": False}}


def _family(f: FieldSchema) -> set[str]:
    return {"int" if t in INTEGER_TYPES else t for t in f.bson_types}


def _expand_int(types: list[str]) -> str | list[str]:
    expanded: list[str] = []
    for t in types:
        expanded.extend(("int", "long") if t == "int" else (t,))
    return expanded[0] if len(expanded) == 1 else expanded


_CONVERT_TARGETS = {
    "int": "long",
    "long": "long",
    "double": "double",
    "decimal": "decimal",
    "string": "string",
    "bool": "bool",
    "date": "date",
    "objectId": "objectId",
}


def _convert_target(new: FieldSchema) -> str:
    for t in new.bson_types:
        if t in _CONVERT_TARGETS:
            return _CONVERT_TARGETS[t]
    return new.bson_types[0] if new.bson_types else "string"


def _create_index(collection: str, ix: IndexSchema) -> str:
    keys: Any = ix.keys[0][0] if len(ix.keys) == 1 and ix.keys[0][1] == 1 else list(ix.keys)
    kwargs: dict[str, Any] = {"name": ix.name}
    if ix.unique:
        kwargs["unique"] = True
    if ix.sparse:
        kwargs["sparse"] = True
    kwargs.update(ix.options_dict)
    return _call("ctx.ops.create_index", collection, keys, **kwargs)


def _set_validator(collection: str, schema: CollectionSchema) -> str:
    return _call(
        "ctx.ops.set_validator",
        collection,
        schema.validator,
        level=schema.validation_level or "moderate",
        action=schema.validation_action or "error",
    )


def _commented(code: str) -> list[str]:
    return [f"# {line}" for line in code.splitlines()]


def _call(func: str, *args: Any, **kwargs: Any) -> str:
    """Render ``func(args..., k=v...)``, wrapping one argument per line when too long."""
    rendered = [lit(a) for a in args]
    extra: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key.isidentifier() and not keyword.iskeyword(key):
            rendered.append(f"{key}={lit(value)}")
        else:
            extra[key] = value
    if extra:
        rendered.append(f"**{lit(extra)}")
    one_line = f"{func}({', '.join(rendered)})"
    if len(one_line) <= LINE_LIMIT and "\n" not in one_line:
        return one_line
    lines = [f"{func}("]
    for arg in rendered:
        lines.extend("    " + line for line in arg.splitlines())
        lines[-1] += ","
    lines.append(")")
    return "\n".join(lines)


def lit(value: Any, indent: int = 0) -> str:
    """Python literal with double quotes; long containers are split over several lines."""
    flat = _lit_flat(value)
    if len(flat) + indent <= LINE_LIMIT - 8 or not isinstance(value, dict | list | tuple):
        return flat
    pad = "    " * (indent // 4 + 1)
    close = "    " * (indent // 4)
    if isinstance(value, dict):
        items = [f"{pad}{_lit_flat(k)}: {lit(v, indent + 4)}," for k, v in value.items()]
        return "{\n" + "\n".join(items) + f"\n{close}}}"
    items = [f"{pad}{lit(v, indent + 4)}," for v in value]
    open_, end = ("[", "]") if isinstance(value, list) else ("(", ")")
    return open_ + "\n" + "\n".join(items) + f"\n{close}{end}"


def _lit_flat(value: Any) -> str:  # noqa: PLR0911 (type dispatch)
    if isinstance(value, _Placeholder):
        return "..."
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if value is None or isinstance(value, bool | int | float):
        return repr(value)
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_lit_flat(k)}: {_lit_flat(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_lit_flat(v) for v in value) + "]"
    if isinstance(value, tuple):
        inner = ", ".join(_lit_flat(v) for v in value)
        return f"({inner},)" if len(value) == 1 else f"({inner})"
    return repr(value)
