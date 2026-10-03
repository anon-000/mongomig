"""``mongomig squash``: replace a chain of revisions with one.

The squashed revision exists to build a *new, empty* database: an empty database has no
documents, so only schema operations matter: collections, indexes and validators. They're
found by reading (not running) each replaced revision's ``upgrade()``, following the
top-level ``ctx.ops`` calls in order, and keeping their net effect (an index created and
later dropped disappears; a renamed collection keeps its indexes).

Everything that can't be read that way is reported for review: custom code that may insert
documents (seed data), schema changes made outside ``ctx.ops``, ``ctx.ops`` calls inside
conditions or loops, or with non-literal arguments.

Databases that already ran the replaced revisions never run the squash: they *adopt* it.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from mongomig.migrations.script import Script
from mongomig.schema.indexes import build_index, default_index_name, normalize_index_keys
from mongomig.schema.models import IndexSchema

DATA_OPS = frozenset({"backfill", "unset_field", "rename_field", "restore_field"})
INSERTING_CALLS = frozenset({"insert_one", "insert_many", "bulk_write"})
UPSERTING_CALLS = frozenset(
    {"update_one", "update_many", "replace_one", "find_one_and_update", "find_one_and_replace"}
)
RAW_SCHEMA_CALLS = frozenset(
    {
        "create_index",
        "create_indexes",
        "drop_index",
        "drop_indexes",
        "drop",
        "rename",
        "create_collection",
        "drop_collection",
        "command",
    }
)


@dataclass
class CollectionState:
    options: dict[str, Any] = field(default_factory=dict)
    indexes: dict[str, IndexSchema] = field(default_factory=dict)
    validator: dict[str, Any] | None = None
    validation_level: str = "moderate"
    validation_action: str = "error"


@dataclass
class SquashAnalysis:
    collections: dict[str, CollectionState] = field(default_factory=dict)
    data_ops_skipped: int = 0
    review: list[str] = field(default_factory=list)

    @property
    def index_count(self) -> int:
        return sum(len(c.indexes) for c in self.collections.values())

    @property
    def validator_count(self) -> int:
        return sum(1 for c in self.collections.values() if c.validator is not None)


def linear_chain(graph: Any, to: str) -> list[str]:
    """The revisions from the base up to ``to``, which must form a straight line."""
    from mongomig.errors import ValidationError

    chain = [to]
    while graph.parents[chain[-1]]:
        parents = graph.parents[chain[-1]]
        if len(parents) > 1:
            raise ValidationError(
                f"Can't squash through merge revision {chain[-1]}.",
                suggestion="Squash up to the revision before the merge.",
            )
        chain.append(parents[0])
    chain.reverse()
    for rev in chain[:-1]:
        if len(graph.children[rev]) > 1:
            raise ValidationError(
                f"Can't squash: the history branches at {rev} "
                f"({', '.join(sorted(graph.children[rev]))}).",
                suggestion="Squash up to the branch point, or merge the branches first.",
            )
    if len(chain) < 2:
        raise ValidationError("Nothing to squash: that's the first revision.")
    return chain


def analyze(scripts: list[Script]) -> SquashAnalysis:
    analysis = SquashAnalysis()
    for script in scripts:
        source = script.path.read_text(encoding="utf-8")
        function = next(
            (
                n
                for n in ast.parse(source).body
                if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
            ),
            None,
        )
        if function is None:
            continue
        for statement in function.body:
            _statement(analysis, script, statement)
    return analysis


def _statement(analysis: SquashAnalysis, script: Script, node: ast.stmt) -> None:
    where = f"{script.revision} line {node.lineno}"
    if isinstance(node, ast.Pass) or (
        isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    ):
        return
    op = _ops_call(node)
    if op is not None:
        name, call = op
        try:
            args = [ast.literal_eval(a) for a in call.args]
            kwargs = {k.arg: ast.literal_eval(k.value) for k in call.keywords if k.arg}
        except ValueError:
            analysis.review.append(f"{where}: ctx.ops.{name}(...) has non-literal arguments")
            return
        if any(k.arg is None for k in call.keywords):
            analysis.review.append(f"{where}: ctx.ops.{name}(**...) can't be read")
            return
        _apply(analysis, where, name, args, kwargs)
        return
    _custom_code(analysis, where, node)


def _ops_call(node: ast.stmt) -> tuple[str, ast.Call] | None:
    """``ctx.ops.<name>(...)`` as a top-level expression statement."""
    if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
        return None
    func = node.value.func
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "ops"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "ctx"
    ):
        return func.attr, node.value
    return None


def _apply(  # noqa: PLR0912 (one branch per ctx.ops operation)
    analysis: SquashAnalysis, where: str, name: str, args: list[Any], kwargs: dict[str, Any]
) -> None:
    colls = analysis.collections
    if name in DATA_OPS:
        analysis.data_ops_skipped += 1  # a new database has no documents to change
        return
    if name == "create_collection":
        coll = colls.setdefault(args[0], CollectionState())
        validator = kwargs.pop("validator", None)
        level = kwargs.pop("validation_level", "moderate")
        action = kwargs.pop("validation_action", "error")
        if validator is not None:
            coll.validator, coll.validation_level, coll.validation_action = (
                validator,
                level,
                action,
            )
        coll.options.update(kwargs)
    elif name == "drop_collection":
        colls.pop(args[0], None)
    elif name == "rename_collection":
        old, new = args[0], args[1] if len(args) > 1 else kwargs["new"]
        if old in colls:
            colls[new] = colls.pop(old)
    elif name == "create_index":
        coll = colls.setdefault(args[0], CollectionState())
        keys = args[1] if len(args) > 1 else kwargs.pop("keys")
        index_name = kwargs.pop("name", None) or default_index_name(normalize_index_keys(keys))
        coll.indexes[index_name] = build_index(
            keys,
            name=index_name,
            unique=bool(kwargs.pop("unique", False)),
            sparse=bool(kwargs.pop("sparse", False)),
            **kwargs,
        )
    elif name == "drop_index":
        if args[0] in colls:
            colls[args[0]].indexes.pop(args[1], None)
    elif name == "set_validator":
        coll = colls.setdefault(args[0], CollectionState())
        coll.validator = args[1] if len(args) > 1 else kwargs["validator"]
        coll.validation_level = kwargs.get("level", "moderate")
        coll.validation_action = kwargs.get("action", "error")
    elif name == "remove_validator":
        if args[0] in colls:
            colls[args[0]].validator = None
    else:
        analysis.review.append(f"{where}: ctx.ops.{name}(...) isn't squashed; add it by hand")


def _custom_code(analysis: SquashAnalysis, where: str, node: ast.stmt) -> None:
    """Custom code only matters for a new database if it creates documents or schema."""
    findings: list[str] = []
    for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
        name = call.func.attr if isinstance(call.func, ast.Attribute) else None
        upsert = any(
            k.arg == "upsert" and isinstance(k.value, ast.Constant) and k.value.value is True
            for k in call.keywords
        )
        nested = _ops_call(ast.Expr(value=call))
        if nested is not None:  # ctx.ops.<x>(...) not at the top level of upgrade()
            if nested[0] not in DATA_OPS:
                findings.append(f"ctx.ops.{nested[0]} inside a condition or loop")
        elif name in INSERTING_CALLS or (name in UPSERTING_CALLS and upsert):
            findings.append(f"may insert documents ({name}): seed data to carry over?")
        elif name in RAW_SCHEMA_CALLS or name in {"set_validator", "create_collection"}:
            findings.append(f"changes the schema outside ctx.ops ({name})")
    if any(isinstance(n, ast.Attribute) and n.attr == "unsafe_db" for n in ast.walk(node)):
        findings.append("uses ctx.unsafe_db")
    for finding in dict.fromkeys(findings):
        analysis.review.append(f"{where}: {finding}")


# --- rendering ---------------------------------------------------------------------------


def render_bodies(analysis: SquashAnalysis) -> tuple[str, str]:
    """(upgrade body, downgrade body) as indented code."""
    from mongomig.generators.render import _call, _create_index

    up: list[str] = []
    down: list[str] = []
    for name, coll in analysis.collections.items():
        up.append(_call("ctx.ops.create_collection", name, **coll.options))
        for index in coll.indexes.values():
            up.append(_create_index(name, index))
        if coll.validator is not None:
            up.append(
                _call(
                    "ctx.ops.set_validator",
                    name,
                    coll.validator,
                    level=coll.validation_level,
                    action=coll.validation_action,
                )
            )
    for name, coll in reversed(analysis.collections.items()):
        if coll.validator is not None:
            down.append(_call("ctx.ops.remove_validator", name))
        for index in reversed(coll.indexes.values()):
            down.append(_call("ctx.ops.drop_index", name, index.name))
        down.append(f"# {_call('ctx.ops.drop_collection', name)}  # would delete its data")
    if analysis.review:
        up.append("")
        up.append(
            "# TODO(review): the replaced revisions also contain code squash can't carry over:"
        )
        up.extend(f"#   - {item}" for item in analysis.review)
    return _indent(up), _indent(down)


def _indent(lines: list[str]) -> str:
    flat = [part for line in lines for part in line.split("\n")]
    if not any(line.strip() and not line.lstrip().startswith("#") for line in flat):
        flat.append("pass")
    return "\n".join(f"    {line}" if line else "" for line in flat)
