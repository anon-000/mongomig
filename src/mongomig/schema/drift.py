"""Drift: how far the stored data is from what the models declare (PRD §30, §66).

``diff`` compares models with the snapshot (code vs code). ``drift`` compares models with
*sampled documents* (code vs data), finding legacy documents, writes from other services,
and migrations that were never run. Data findings fail above a configurable percentage;
structural findings (indexes, validators, missing collections) always fail.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from mongomig.schema.models import INTEGER_TYPES, CollectionSchema, FieldSchema, IndexSchema

Status = Literal["fail", "warn"]

ID_INDEX = "_id_"
# Stored types a declared type reads without trouble (Pydantic coerces them).
COMPATIBLE = {"double": {"int"}, "decimal": {"int", "double"}}
# A declared type mostly stored as another suggests the wrong storage profile.
PROFILE_HINTS = {
    ("date", "string"): "dates are stored as strings: should env.py use storage='json'?",
    ("objectId", "string"): "ObjectIds are stored as strings: should env.py use storage='json'?",
    ("binData", "string"): "UUIDs are stored as strings: should env.py use storage='json'?",
    ("decimal", "string"): "Decimals are stored as strings: should env.py use storage='json'?",
    ("string", "date"): "strings are stored as dates: should env.py use storage='python'?",
}
FIX_HINTS = {
    "missing_field": "backfill it in a migration, filtering on {field: {$exists: false}}",
    "unexpected_type": "convert the values in a migration, or widen the model's type",
    "unexpected_field": "add it to the model, or remove it: ctx.ops.unset_field(..., backup=True)",
    "collection_missing": "run `mongomig upgrade` (pending migrations?)",
    "index_missing": "run `mongomig upgrade`, or check if it was dropped by hand",
    "index_mismatch": "write a migration that drops and recreates it",
    "index_unexpected": "add it to the model's indexes, or drop it in a migration",
    "validator_missing": "run `mongomig upgrade`, or check if it was changed by hand",
    "validator_mismatch": "run `mongomig upgrade`, or check if it was changed by hand",
}


@dataclass(frozen=True)
class Thresholds:
    missing_field_percent: float = 0.5
    unexpected_type_percent: float = 0.5
    unexpected_field_percent: float = 1.0


@dataclass(frozen=True)
class DriftFinding:
    kind: str  # missing_field, unexpected_type, unexpected_field, index_*, validator_*, ...
    collection: str
    status: Status
    summary: str  # "expected int | null · observed string 5.7%"
    path: str | None = None
    share: float | None = None  # fraction (0-1) of documents/values affected
    threshold: float | None = None  # percentage
    hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "collection": self.collection,
            "path": self.path,
            "status": self.status,
            "summary": self.summary,
            "percent": round(self.share * 100, 3) if self.share is not None else None,
            "threshold_percent": self.threshold,
            "hint": self.hint,
        }


@dataclass
class CollectionDrift:
    name: str
    findings: list[DriftFinding] = field(default_factory=list)
    documents_scanned: int = 0
    estimated_total: int = 0
    complete: bool = False
    exists: bool = True

    @property
    def failed(self) -> list[DriftFinding]:
        return [f for f in self.findings if f.status == "fail"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "exists": self.exists,
            "documents_scanned": self.documents_scanned,
            "estimated_total": self.estimated_total,
            "complete": self.complete,
            "failed": len(self.failed),
            "findings": [f.to_dict() for f in self.findings],
        }


def compare(
    declared: CollectionSchema, observed: CollectionSchema, thresholds: Thresholds
) -> list[DriftFinding]:
    """All drift between a declared schema and an observed one (with stats)."""
    findings: list[DriftFinding] = []
    # `_id` is always present in a sampled document, so no fields means no documents.
    sampled = bool(observed.fields)
    _compare_fields(
        declared.name, declared.fields, observed.fields, thresholds, "", findings, sampled
    )
    findings.extend(compare_indexes(declared.name, declared.indexes, observed.indexes))
    findings.extend(compare_validator(declared, observed))
    return findings


# --- fields ------------------------------------------------------------------------------


def _compare_fields(
    collection: str,
    declared: Mapping[str, FieldSchema],
    observed: Mapping[str, FieldSchema],
    t: Thresholds,
    prefix: str,
    out: list[DriftFinding],
    parents_seen: bool,
) -> None:
    """``parents_seen``: at least one parent document/object was sampled, so a field that
    never appeared is genuinely missing (not just unobserved)."""
    for name, want in declared.items():
        path = prefix + name
        have = observed.get(name)
        if have is None or have.stats is None:
            if want.required and parents_seen:
                out.append(_missing(collection, path, 1.0, t))
            continue
        if want.required and have.stats.presence < 1:
            out.append(_missing(collection, path, 1 - have.stats.presence, t))
        _check_types(collection, path, want, have, t, out)
        if want.fields is not None and have.fields is not None and not want.open:
            _compare_fields(collection, want.fields, have.fields, t, f"{path}.", out, True)
        if want.items is not None and have.items is not None:
            _check_types(collection, f"{path}[]", want.items, have.items, t, out)
            if want.items.fields is not None and have.items.fields is not None:
                _compare_fields(
                    collection, want.items.fields, have.items.fields, t, f"{path}[].", out, True
                )

    for name, have in observed.items():
        if name in declared or have.stats is None:
            continue
        share = have.stats.presence
        out.append(
            DriftFinding(
                "unexpected_field",
                collection,
                _status(share, t.unexpected_field_percent),
                f"not in the model · present in {_pct(share)} of documents",
                path=prefix + name,
                share=share,
                threshold=t.unexpected_field_percent,
                hint=FIX_HINTS["unexpected_field"],
            )
        )


def _missing(collection: str, path: str, share: float, t: Thresholds) -> DriftFinding:
    return DriftFinding(
        "missing_field",
        collection,
        _status(share, t.missing_field_percent),
        f"required by the model · missing in {_pct(share)} of documents",
        path=path,
        share=share,
        threshold=t.missing_field_percent,
        hint=FIX_HINTS["missing_field"],
    )


def _check_types(
    collection: str,
    path: str,
    want: FieldSchema,
    have: FieldSchema,
    t: Thresholds,
    out: list[DriftFinding],
) -> None:
    if not want.bson_types or have.stats is None:  # declared Any, or nothing observed
        return
    allowed = {_family(x) for x in want.bson_types}
    for declared_type in list(allowed):
        allowed |= COMPATIBLE.get(declared_type, set())
    if want.nullable:
        allowed.add("null")

    unexpected: dict[str, float] = {}
    for stored, share in have.stats.types.items():
        family = _family(stored)
        if family not in allowed:
            unexpected[family] = unexpected.get(family, 0.0) + share
    if not unexpected:
        return
    total = sum(unexpected.values())
    observed_text = " · ".join(
        f"{name} {_pct(share)}" for name, share in sorted(unexpected.items(), key=lambda kv: -kv[1])
    )
    hint = FIX_HINTS["unexpected_type"]
    dominant = max(unexpected, key=lambda k: unexpected[k])
    if unexpected[dominant] >= 0.5:
        for declared_type in want.bson_types:
            profile_hint = PROFILE_HINTS.get((_family(declared_type), dominant))
            if profile_hint:
                hint = profile_hint
    out.append(
        DriftFinding(
            "unexpected_type",
            collection,
            _status(total, t.unexpected_type_percent),
            f"expected {want.type_label()} · observed {observed_text}",
            path=path,
            share=total,
            threshold=t.unexpected_type_percent,
            hint=hint,
        )
    )


def _family(bson_type: str) -> str:
    return "int" if bson_type in INTEGER_TYPES else bson_type


def _status(share: float, threshold_percent: float) -> Status:
    # Rounded: 1 - 0.99 is 0.010000000000000009, which must not exceed a 1% threshold.
    return "fail" if round(share * 100, 9) > threshold_percent else "warn"


def _pct(share: float) -> str:
    percent = share * 100
    if percent == 0:
        return "0%"
    if percent < 0.01:
        return "<0.01%"
    return f"{percent:.2f}%" if percent < 10 else f"{percent:.1f}%"


# --- indexes and validators --------------------------------------------------------------


def compare_indexes(
    collection: str, declared: list[IndexSchema], observed: list[IndexSchema]
) -> list[DriftFinding]:
    have = {ix.name: ix for ix in observed if ix.name != ID_INDEX}
    want = {ix.name: ix for ix in declared if ix.name != ID_INDEX}
    findings: list[DriftFinding] = []
    for name, ix in want.items():
        server = have.get(name)
        if server is None:
            findings.append(
                DriftFinding(
                    "index_missing",
                    collection,
                    "fail",
                    f"index {ix.describe()} is not in the database",
                    hint=FIX_HINTS["index_missing"],
                )
            )
        elif not index_matches(ix, server):
            findings.append(
                DriftFinding(
                    "index_mismatch",
                    collection,
                    "fail",
                    f"index {name}: declared {ix.describe()}, database has {server.describe()}",
                    hint=FIX_HINTS["index_mismatch"],
                )
            )
    for name in sorted(set(have) - set(want)):
        findings.append(
            DriftFinding(
                "index_unexpected",
                collection,
                "warn",
                f"index {have[name].describe()} is not declared",
                hint=FIX_HINTS["index_unexpected"],
            )
        )
    return findings


def index_matches(declared: IndexSchema, server: IndexSchema) -> bool:
    """Same index? Options the server adds on its own (collation defaults, text-index
    versions, weights) are ignored; only what was declared is compared."""
    if (declared.unique, declared.sparse) != (server.unique, server.sparse):
        return False
    if _key_signature(declared.keys, None) != _key_signature(
        server.keys, server.options_dict.get("weights")
    ):
        return False
    server_options = server.options_dict
    for key, value in declared.options:
        if key == "collation" and isinstance(value, Mapping):
            stored = server_options.get("collation") or {}
            if any(stored.get(k) != v for k, v in value.items()):
                return False
        elif server_options.get(key) != value:
            return False
    return True


def _key_signature(keys: tuple[tuple[str, Any], ...], weights: Any) -> tuple[Any, ...]:
    """Text indexes are stored as {_fts: "text", _ftsx: 1} plus weights; normalise both forms
    to (non-text keys..., frozenset(text fields))."""
    plain = tuple((k, v) for k, v in keys if v != "text" and k not in ("_fts", "_ftsx"))
    text_fields = {k for k, v in keys if v == "text" and k != "_fts"}
    if isinstance(weights, Mapping):
        text_fields |= set(weights)
    return (*plain, frozenset(text_fields))


def compare_validator(declared: CollectionSchema, observed: CollectionSchema) -> list[DriftFinding]:
    if declared.validator is None:  # not managed by the models
        return []
    from mongomig.schema.indexes import canonical

    name = declared.name
    if observed.validator is None:
        return [
            DriftFinding(
                "validator_missing",
                name,
                "fail",
                "the model's validator is not set in the database",
                hint=FIX_HINTS["validator_missing"],
            )
        ]
    same_rules = canonical(declared.validator) == canonical(observed.validator)
    same_level = (declared.validation_level or "moderate") == (
        observed.validation_level or "strict"
    )
    same_action = (declared.validation_action or "error") == (observed.validation_action or "error")
    if same_rules and same_level and same_action:
        return []
    what = []
    if not same_rules:
        what.append("rules differ")
    if not same_level:
        what.append(f"level {observed.validation_level} (declared {declared.validation_level})")
    if not same_action:
        what.append(f"action {observed.validation_action} (declared {declared.validation_action})")
    return [
        DriftFinding(
            "validator_mismatch",
            name,
            "fail",
            "validator: " + ", ".join(what),
            hint=FIX_HINTS["validator_mismatch"],
        )
    ]


# --- running against a database ----------------------------------------------------------


def check_collections(
    db: Any,
    declared: Mapping[str, CollectionSchema],
    thresholds: Thresholds,
    *,
    only: list[str] | None = None,
    sample_size: int = 10000,
    sample_percent: float | None = None,
    full_scan: bool = False,
) -> list[CollectionDrift]:
    """Sample each declared collection (``only``: a subset) and compare it with its model."""
    from mongomig.schema.inference import inspect_collection

    existing = set(db.list_collection_names())
    results: list[CollectionDrift] = []
    for name in only or sorted(declared):
        if name not in existing:
            results.append(
                CollectionDrift(
                    name,
                    findings=[
                        DriftFinding(
                            "collection_missing",
                            name,
                            "fail",
                            "declared by the models but not in the database",
                            hint=FIX_HINTS["collection_missing"],
                        )
                    ],
                    exists=False,
                )
            )
            continue
        inspected = inspect_collection(
            db, name, sample_size=sample_size, sample_percent=sample_percent, full_scan=full_scan
        )
        results.append(
            CollectionDrift(
                name,
                findings=compare(declared[name], inspected.schema, thresholds),
                documents_scanned=inspected.documents_scanned,
                estimated_total=inspected.estimated_total,
                complete=inspected.is_complete,
            )
        )
    return results
