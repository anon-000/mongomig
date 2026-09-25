"""Render ``CollectionSchema`` trees as aligned text."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mongomig.schema.models import INTEGER_TYPES, FieldSchema

if TYPE_CHECKING:
    from rich.console import Console


def field_rows(
    fields: dict[str, FieldSchema], *, depth: int = 0, prefix: str = ""
) -> list[tuple[str, FieldSchema]]:
    """Flatten a field tree into (indented label, field) rows; array elements as ``name[]``."""
    rows: list[tuple[str, FieldSchema]] = []
    for name, f in fields.items():
        label = "  " * depth + prefix + name
        rows.append((label, f))
        if f.fields:
            rows.extend(field_rows(f.fields, depth=depth + 1))
        items = f.items
        if items is not None and items.fields:
            rows.extend(field_rows(items.fields, depth=depth + 1, prefix="[]."))
    return rows


def observed_types(f: FieldSchema) -> str:
    """``int 97.2% · string 2.8%`` (int/long merged) or just the type when uniform."""
    if f.stats is None:
        return f.type_label()
    shares: dict[str, float] = {}
    for t, share in sorted(f.stats.types.items(), key=lambda kv: -kv[1]):
        key = "int" if t in INTEGER_TYPES else t
        shares[key] = shares.get(key, 0.0) + share
    if f.items is not None and f.items.bson_types and "array" in shares:
        element = " | ".join(
            dict.fromkeys("int" if t in INTEGER_TYPES else t for t in f.items.bson_types)
        )
        shares = {("array<" + element + ">" if k == "array" else k): v for k, v in shares.items()}
    if len(shares) == 1:
        return next(iter(shares))
    return " · ".join(f"{t} {share:.1%}" for t, share in shares.items())


def print_observed(con: Console, fields: dict[str, FieldSchema]) -> None:
    from rich.markup import escape

    rows = field_rows(fields)
    width = max((len(label) for label, _ in rows), default=5) + 2
    con.print(f"  [dim]{'field':<{width}}{'presence':>8}   types[/dim]")
    for label, f in rows:
        presence = f"{f.stats.presence:>8.2%}" if f.stats else ""
        style = "" if f.stats is None or f.stats.presence == 1 else "[yellow]"
        end = "[/yellow]" if style else ""
        con.print(f"  {escape(label):<{width}}{style}{presence}{end}   {escape(observed_types(f))}")


def print_declared(con: Console, fields: dict[str, FieldSchema]) -> None:
    from rich.markup import escape

    rows = field_rows(fields)
    width = max((len(label) for label, _ in rows), default=5) + 2
    type_width = max((len(f.type_label()) for _, f in rows), default=4) + 2
    for label, f in rows:
        notes: list[str] = []
        if not f.required:
            notes.append("optional")
        if f.enum is not None:
            notes.append("enum " + ", ".join(repr(v) for v in f.enum))
        if f.has_default:
            notes.append(f"default={f.default!r}" if f.default_is_static else "default=<dynamic>")
        if f.open:
            notes.append("any keys")
        con.print(
            f"  {escape(label):<{width}}{escape(f.type_label()):<{type_width}}"
            f"[dim]{escape('  '.join(notes))}[/dim]"
        )
