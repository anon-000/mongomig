"""Compare two sets of schemas (snapshot → current models) and classify every change.

Classification (most to least severe):

- ``DESTRUCTIVE``: data would be lost (never generated as live code by autogenerate)
- ``MANUAL_REVIEW``: needs a human decision (a value to backfill, an unsafe conversion)
- ``REQUIRES_DATA_MIGRATION``: existing documents must change; autogenerate writes it
- ``WARNING``: operationally risky or leaves data behind (unique index, removed field)
- ``SAFE``: compatible with existing data
"""

from __future__ import annotations

import difflib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from enum import IntEnum
from typing import Any

from mongomig.schema.models import INTEGER_TYPES, CollectionSchema, FieldSchema, IndexSchema

ID_INDEX = "_id_"
RENAME_HINT_THRESHOLD = 0.6


class Severity(IntEnum):
    SAFE = 0
    WARNING = 1
    REQUIRES_DATA_MIGRATION = 2
    MANUAL_REVIEW = 3
    DESTRUCTIVE = 4


@dataclass(frozen=True)
class Change:
    kind: str  # collection_added, field_added, field_type_changed, index_added, ...
    collection: str
    severity: Severity
    summary: str  # one line for humans, e.g. "+ age: int | null"
    note: str = ""  # why it's classified this way / what autogenerate does
    path: str | None = None  # dotted field path ("profile.verified", "items[].sku")
    old: Any = None  # FieldSchema | IndexSchema | dict | None
    new: Any = None
    target: str | None = None  # new path, for field_renamed

    @property
    def in_array(self) -> bool:
        return self.path is not None and "[]" in self.path

    def to_dict(self) -> dict[str, Any]:
        def dump(value: Any) -> Any:
            return value.to_dict() if hasattr(value, "to_dict") else value

        return {
            "kind": self.kind,
            "collection": self.collection,
            "path": self.path,
            "severity": self.severity.name,
            "summary": self.summary,
            "note": self.note,
            "old": dump(self.old),
            "new": dump(self.new),
            "target": self.target,
        }


@dataclass(frozen=True)
class RenameHint:
    collection: str
    old_path: str
    new_path: str
    similarity: float

    @property
    def flag(self) -> str:
        return f"--rename {self.collection}.{self.old_path}:{self.new_path.rsplit('.', 1)[-1]}"


@dataclass
class DiffResult:
    changes: list[Change] = field(default_factory=list)
    rename_hints: list[RenameHint] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)

    @property
    def max_severity(self) -> Severity | None:
        return max((c.severity for c in self.changes), default=None)

    def by_collection(self) -> dict[str, list[Change]]:
        grouped: dict[str, list[Change]] = {}
        for change in self.changes:
            grouped.setdefault(change.collection, []).append(change)
        return grouped

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for change in self.changes:
            counts[change.severity.name] = counts.get(change.severity.name, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_changes": self.has_changes,
            "max_severity": self.max_severity.name if self.max_severity is not None else None,
            "counts": self.counts(),
            "changes": [c.to_dict() for c in self.changes],
            "rename_hints": [
                {
                    "collection": h.collection,
                    "old": h.old_path,
                    "new": h.new_path,
                    "similarity": round(h.similarity, 3),
                    "flag": h.flag,
                }
                for h in self.rename_hints
            ],
            "warnings": self.warnings,
        }


def parse_renames(specs: list[str]) -> dict[tuple[str, str], str]:
    """``["users.first_name:given_name"]`` → ``{("users", "first_name"): "given_name"}``.

    The new name is relative to the old field's parent: ``users.profile.bio:about`` renames
    ``profile.bio`` to ``profile.about``.
    """
    from mongomig.errors import ValidationError

    result: dict[tuple[str, str], str] = {}
    for spec in specs:
        old, sep, new = spec.partition(":")
        collection, dot, path = old.partition(".")
        if not sep or not dot or not path or not new or "." in new:
            raise ValidationError(
                f"Invalid --rename {spec!r}.",
                suggestion="Use --rename COLLECTION.OLD_FIELD:NEW_FIELD, "
                "e.g. --rename users.first_name:given_name",
            )
        result[(collection, path)] = new
    return result


def diff_schemas(
    old: Mapping[str, CollectionSchema],
    new: Mapping[str, CollectionSchema],
    *,
    renames: Mapping[tuple[str, str], str] | None = None,
) -> DiffResult:
    """Changes needed to go from ``old`` (snapshot) to ``new`` (current models)."""
    result = DiffResult()
    renames = dict(renames or {})
    used_renames: set[tuple[str, str]] = set()

    for name in sorted(set(old) | set(new)):
        before, after = old.get(name), new.get(name)
        if before is None and after is not None:
            result.changes.append(
                Change(
                    "collection_added",
                    name,
                    Severity.SAFE,
                    f"+ collection {name}",
                    "new collection",
                    new=after,
                )
            )
            result.changes.extend(_index_changes(name, [], after.indexes))
            result.changes.extend(_validator_changes(name, None, after))
        elif after is None and before is not None:
            result.changes.append(
                Change(
                    "collection_removed",
                    name,
                    Severity.WARNING,
                    f"- collection {name}",
                    "no longer in the models; data is kept (drop it manually if intended)",
                    old=before,
                )
            )
        elif before is not None and after is not None:
            field_changes = _field_changes(
                name, before.fields, after.fields, renames, result.rename_hints
            )
            used_renames |= {
                (name, c.path or "") for c in field_changes if c.kind == "field_renamed"
            }
            if after.validator is not None and after.validation_level == "strict":
                field_changes = [_strict_backfill(c) for c in field_changes]
            result.changes.extend(field_changes)
            result.changes.extend(_index_changes(name, before.indexes, after.indexes))
            result.changes.extend(_validator_changes(name, before, after))

    unused = [f"{c}.{p}" for (c, p) in renames if (c, p) not in used_renames]
    if unused:
        result.warnings.append(
            "--rename did not match a removed field: " + ", ".join(sorted(unused))
        )
    return result


# --- fields ------------------------------------------------------------------------------


def _field_changes(
    collection: str,
    old: Mapping[str, FieldSchema],
    new: Mapping[str, FieldSchema],
    renames: Mapping[tuple[str, str], str],
    hints: list[RenameHint],
    prefix: str = "",
) -> list[Change]:
    changes: list[Change] = []

    removed = [k for k in old if k not in new]
    added = [k for k in new if k not in old]

    # explicit renames first: they consume one removed + one added field
    for key in list(removed):
        target = renames.get((collection, prefix + key))
        if target is not None and target in added:
            removed.remove(key)
            added.remove(target)
            changes.append(
                Change(
                    "field_renamed",
                    collection,
                    Severity.REQUIRES_DATA_MIGRATION,
                    f"~ {prefix}{key} → {prefix}{target}",
                    "rename in existing documents",
                    path=prefix + key,
                    old=old[key],
                    new=new[target],
                    target=prefix + target,
                )
            )
            changes.extend(
                _compare_field(collection, prefix + target, old[key], new[target], renames, hints)
            )

    for key in new:
        path = prefix + key
        if key in added:
            changes.append(_field_added(collection, path, new[key]))
        elif key in old:
            changes.extend(_compare_field(collection, path, old[key], new[key], renames, hints))

    for key in removed:
        changes.append(
            Change(
                "field_removed",
                collection,
                Severity.WARNING,
                f"- {prefix}{key}",
                "removed from the model; existing data is kept (not deleted)",
                path=prefix + key,
                old=old[key],
            )
        )

    if "[]" not in prefix:  # rename_field can't reach into arrays
        hints.extend(_rename_hints(collection, prefix, removed, added, old, new))
    return changes


def _strict_backfill(change: Change) -> Change:
    """Under a strict validator, documents missing a required field reject every update, so
    a field defaulting to None must be backfilled after all."""
    if change.kind == "field_added" and change.severity == Severity.SAFE and change.new.required:
        return replace(
            change,
            severity=Severity.REQUIRES_DATA_MIGRATION,
            note="strict validator requires it: backfill existing documents with None",
        )
    return change


def _field_added(collection: str, path: str, new: FieldSchema) -> Change:
    label = f"+ {path}: {new.type_label()}"
    if new.has_default and new.default_is_static:
        label += f" = {new.default!r}"
    kind = "field_added"
    in_array = "[]" in path

    if not new.required:
        return Change(kind, collection, Severity.SAFE, label, "optional field", path=path, new=new)
    if new.has_default and new.default_is_static and new.default is None:
        # Pydantic reads a missing field as None, and {field: null} also matches missing fields,
        # so writing null into every existing document would be pure cost.
        return Change(
            kind,
            collection,
            Severity.SAFE,
            label,
            "defaults to None: existing documents need no backfill",
            path=path,
            new=new,
        )
    if in_array:
        return Change(
            kind,
            collection,
            Severity.MANUAL_REVIEW,
            label,
            "required field inside an array: backfill array elements manually",
            path=path,
            new=new,
        )
    if new.has_default and new.default_is_static:
        return Change(
            kind,
            collection,
            Severity.REQUIRES_DATA_MIGRATION,
            label,
            f"backfill existing documents with {new.default!r}",
            path=path,
            new=new,
        )
    if new.nullable and not new.has_default:
        return Change(
            kind,
            collection,
            Severity.REQUIRES_DATA_MIGRATION,
            label,
            "required but nullable: backfill existing documents with null",
            path=path,
            new=new,
        )
    reason = "default is computed at runtime" if new.has_default else "required, with no default"
    return Change(
        kind,
        collection,
        Severity.MANUAL_REVIEW,
        label,
        f"{reason}: choose a value for existing documents",
        path=path,
        new=new,
    )


def _compare_field(
    collection: str,
    path: str,
    old: FieldSchema,
    new: FieldSchema,
    renames: Mapping[tuple[str, str], str],
    hints: list[RenameHint],
) -> list[Change]:
    changes: list[Change] = []
    old_types, new_types = _type_family(old), _type_family(new)

    if old_types != new_types:
        changes.append(_type_change(collection, path, old, new, old_types, new_types))

    if old.nullable and not new.nullable:
        static = new.has_default and new.default_is_static and new.default is not None
        changes.append(
            Change(
                "field_nullable_changed",
                collection,
                Severity.REQUIRES_DATA_MIGRATION if static else Severity.MANUAL_REVIEW,
                f"~ {path}: nullable → not nullable",
                f"replace nulls with {new.default!r}" if static else "existing nulls need a value",
                path=path,
                old=old,
                new=new,
            )
        )
    elif not old.nullable and new.nullable:
        changes.append(
            Change(
                "field_nullable_changed",
                collection,
                Severity.SAFE,
                f"~ {path}: now nullable",
                "compatible",
                path=path,
                old=old,
                new=new,
            )
        )

    if not old.required and new.required:
        added = _field_added(collection, path, new)
        changes.append(
            Change(
                "field_required_changed",
                collection,
                added.severity if added.severity != Severity.SAFE else Severity.WARNING,
                f"~ {path}: optional → required",
                added.note,
                path=path,
                old=old,
                new=new,
            )
        )
    elif old.required and not new.required:
        changes.append(
            Change(
                "field_required_changed",
                collection,
                Severity.SAFE,
                f"~ {path}: required → optional",
                "compatible",
                path=path,
                old=old,
                new=new,
            )
        )

    enum_change = _enum_change(collection, path, old, new)
    if enum_change is not None:
        changes.append(enum_change)

    if old.fields is not None and new.fields is not None and not (old.open or new.open):
        changes.extend(
            _field_changes(collection, old.fields, new.fields, renames, hints, prefix=f"{path}.")
        )
    if old.items is not None and new.items is not None:
        changes.extend(
            _compare_field(collection, f"{path}[]", old.items, new.items, renames, hints)
        )
    return changes


# PRD §37: conversions that never lose information.
_SAFE_WIDENINGS = {("int", "double"), ("int", "decimal"), ("double", "decimal")}


def _type_family(f: FieldSchema) -> frozenset[str]:
    return frozenset("int" if t in INTEGER_TYPES else t for t in f.bson_types)


def _type_change(
    collection: str,
    path: str,
    old: FieldSchema,
    new: FieldSchema,
    old_types: frozenset[str],
    new_types: frozenset[str],
) -> Change:
    summary = f"~ {path}: {old.type_label()} → {new.type_label()}"
    if not new_types:  # became Any
        return Change(
            "field_type_changed",
            collection,
            Severity.SAFE,
            summary,
            "now any type",
            path=path,
            old=old,
            new=new,
        )
    lost = old_types - new_types
    if not old_types or not lost:
        return Change(
            "field_type_changed",
            collection,
            Severity.SAFE,
            summary,
            "widened: existing values still valid",
            path=path,
            old=old,
            new=new,
        )
    if all(any((t, n) in _SAFE_WIDENINGS for n in new_types) for t in lost):
        return Change(
            "field_type_changed",
            collection,
            Severity.WARNING,
            summary,
            "safe conversion; existing values are read fine, optionally convert them",
            path=path,
            old=old,
            new=new,
        )
    return Change(
        "field_type_changed",
        collection,
        Severity.MANUAL_REVIEW,
        summary,
        "existing values must be converted (possible data loss): review the generated conversion",
        path=path,
        old=old,
        new=new,
    )


def _enum_change(collection: str, path: str, old: FieldSchema, new: FieldSchema) -> Change | None:
    if old.enum == new.enum or new.enum is None:
        return None
    if old.enum is None:
        return Change(
            "field_enum_changed",
            collection,
            Severity.WARNING,
            f"~ {path}: now restricted to {list(new.enum)}",
            "existing documents may hold other values",
            path=path,
            old=old,
            new=new,
        )
    removed = [v for v in old.enum if v not in new.enum]
    if not removed:
        return Change(
            "field_enum_changed",
            collection,
            Severity.SAFE,
            f"~ {path}: allowed values added",
            "compatible",
            path=path,
            old=old,
            new=new,
        )
    return Change(
        "field_enum_changed",
        collection,
        Severity.MANUAL_REVIEW,
        f"~ {path}: values removed {removed}",
        "documents holding removed values must be updated",
        path=path,
        old=old,
        new=new,
    )


def _rename_hints(
    collection: str,
    prefix: str,
    removed: list[str],
    added: list[str],
    old: Mapping[str, FieldSchema],
    new: Mapping[str, FieldSchema],
) -> Iterator[RenameHint]:
    for r in removed:
        best: tuple[float, str] | None = None
        for a in added:
            score = difflib.SequenceMatcher(None, r, a).ratio()
            if _type_family(old[r]) == _type_family(new[a]):
                score = min(1.0, score + 0.25)
            if score >= RENAME_HINT_THRESHOLD and (best is None or score > best[0]):
                best = (score, a)
        if best is not None:
            yield RenameHint(collection, prefix + r, prefix + best[1], best[0])


# --- indexes and validators --------------------------------------------------------------


def _index_changes(collection: str, old: list[IndexSchema], new: list[IndexSchema]) -> list[Change]:
    before = {ix.name: ix for ix in old if ix.name != ID_INDEX}
    after = {ix.name: ix for ix in new if ix.name != ID_INDEX}
    changes: list[Change] = []
    for name in sorted(after):
        ix = after[name]
        if name not in before:
            severity, note = _index_risk(ix)
            changes.append(
                Change(
                    "index_added", collection, severity, f"+ index {ix.describe()}", note, new=ix
                )
            )
        elif before[name] != ix:
            changes.append(
                Change(
                    "index_changed",
                    collection,
                    Severity.WARNING,
                    f"~ index {before[name].describe()} → {ix.describe()}",
                    "dropped and recreated; queries may be slow meanwhile",
                    old=before[name],
                    new=ix,
                )
            )
    for name in sorted(set(before) - set(after)):
        changes.append(
            Change(
                "index_removed",
                collection,
                Severity.WARNING,
                f"- index {before[name].describe()}",
                "queries relying on it may become slow",
                old=before[name],
            )
        )
    return changes


def _index_risk(ix: IndexSchema) -> tuple[Severity, str]:
    options = ix.options_dict
    if "expireAfterSeconds" in options:
        return Severity.WARNING, "TTL index: MongoDB will start deleting expired documents"
    if ix.unique:
        return Severity.WARNING, "fails if existing documents contain duplicates"
    return Severity.SAFE, "built in the background by MongoDB"


def _validator_changes(
    collection: str, old: CollectionSchema | None, new: CollectionSchema
) -> list[Change]:
    before = old.validator if old is not None else None
    after = new.validator
    if before is None and after is None:
        return []
    if before is None:
        return [
            Change(
                "validator_added",
                collection,
                Severity.WARNING,
                f"+ validator (level={new.validation_level})",
                "new writes must conform",
                new=new,
            )
        ]
    if after is None:
        return [
            Change(
                "validator_removed",
                collection,
                Severity.WARNING,
                "- validator (no longer managed)",
                "validator left in place; remove it manually if intended",
                old=old,
            )
        ]
    assert old is not None
    if (before, old.validation_level, old.validation_action) == (
        after,
        new.validation_level,
        new.validation_action,
    ):
        return []
    return [
        Change(
            "validator_changed",
            collection,
            Severity.WARNING,
            f"~ validator (level={new.validation_level})",
            "new writes must conform to the updated validator",
            old=old,
            new=new,
        )
    ]
