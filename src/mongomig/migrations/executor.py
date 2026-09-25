"""Plan and run upgrades/downgrades under the migration lock."""

from __future__ import annotations

import importlib.util
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from mongomig.database.redact import redact_text
from mongomig.errors import (
    ChecksumMismatchError,
    ConfirmationRequiredError,
    IrreversibleMigrationError,
    MigrationExecutionError,
    RevisionNotFoundError,
    ScriptError,
    ValidationError,
)
from mongomig.migrations.context import MigrationContext
from mongomig.migrations.graph import RevisionGraph
from mongomig.migrations.lock import MigrationLock
from mongomig.migrations.reporting import Direction, Reporter
from mongomig.migrations.script import Script
from mongomig.migrations.tracker import (
    CurrentState,
    MigrationTracker,
    compute_state,
    run_metadata,
)

if TYPE_CHECKING:
    from pymongo.database import Database

    from mongomig.config.models import LoadedConfig
    from mongomig.migrations.dryrun import RecordedOp
    from mongomig.safety.impact import Assessment

# Called with the planned scripts before a downgrade runs; return False to abort.
ConfirmFn = Callable[[list[Script]], bool]
# Called before an upgrade that needs confirmation, with the reasons; return False to abort.
UpgradeConfirmFn = Callable[[list[Script], list[str]], bool]


@dataclass(frozen=True)
class StepResult:
    revision: str
    message: str
    direction: Direction
    duration_ms: int
    skipped_code: bool = False  # irreversible migration removed from tracking with --force


@dataclass
class MigrationAnalysis:
    """What a dry run of one migration found."""

    script: Script
    direction: Direction
    ops: list[RecordedOp]
    assessment: Assessment
    unavailable: str | None = None  # couldn't be fully simulated (e.g. ctx.unsafe_db)
    error: str | None = None  # the migration raised during the dry run

    @property
    def destructive(self) -> bool:
        return any(op.destructive for op in self.ops)

    @property
    def resumable(self) -> bool | None:
        """ctx.ops-only migrations are idempotent, so re-running after a crash is safe."""
        if self.unavailable or self.error:
            return None
        return all(op.exact for op in self.ops) or None

    @property
    def estimated_docs(self) -> int:
        return sum(
            op.estimated_docs or 0
            for op in self.ops
            if not op.operation.startswith(
                ("create_index", "drop_index", "set_validator", "remove_validator")
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.script.revision,
            "message": self.script.message,
            "direction": self.direction,
            "risk": self.assessment.risk.name,
            "reasons": self.assessment.reasons,
            "reversible": self.script.reversible,
            "destructive": self.destructive,
            "resumable": self.resumable,
            "estimated_docs": self.estimated_docs,
            "unavailable": self.unavailable,
            "error": self.error,
            "operations": [op.to_dict() for op in self.ops],
        }


@dataclass
class RunResult:
    direction: Direction
    steps: list[StepResult] = field(default_factory=list)
    aborted: bool = False

    @property
    def revisions(self) -> list[str]:
        return [s.revision for s in self.steps]


# --- planning (pure functions, unit-tested) ----------------------------------------------


def plan_upgrade(
    graph: RevisionGraph,
    applied: frozenset[str] | set[str],
    target: str = "head",
    *,
    steps: int | None = None,
) -> list[Script]:
    """Revisions to apply, parents first.

    ``target``: ``head`` (the single head; error if several), ``heads`` (all), or a revision.
    ``steps``: only the next N pending revisions.
    """
    order = graph.topological_order()
    if steps is not None:
        if steps < 1:
            raise ValidationError("--steps must be at least 1.")
        pending = [rev for rev in order if rev not in applied]
        return [graph.scripts[rev] for rev in pending[:steps]]

    if target == "heads":
        targets = graph.heads()
    elif target == "head":
        head = graph.single_head()
        targets = [head] if head else []
    else:
        targets = [graph.resolve(target)]

    wanted: set[str] = set()
    for rev in targets:
        wanted |= graph.ancestors(rev) | {rev}
    return [graph.scripts[rev] for rev in order if rev in wanted and rev not in applied]


def plan_downgrade(
    graph: RevisionGraph,
    applied: frozenset[str] | set[str],
    target: str | None = None,
    *,
    steps: int | None = None,
) -> list[Script]:
    """Revisions to undo, children first.

    ``target``: ``base`` (undo everything) or a revision (undo everything applied *after*
    it; it stays applied). Default: one step.
    """
    applied_known = [rev for rev in reversed(graph.topological_order()) if rev in applied]

    if target is None:
        count = 1 if steps is None else steps
        if count < 1:
            raise ValidationError("--steps must be at least 1.")
        return [graph.scripts[rev] for rev in applied_known[:count]]
    if steps is not None:
        raise ValidationError("Use either a target revision or --steps, not both.")
    if target == "base":
        return [graph.scripts[rev] for rev in applied_known]

    keep = graph.resolve(target)
    if keep not in applied:
        raise RevisionNotFoundError(
            f"Revision {keep} is not applied, so there is nothing to downgrade to.",
            suggestion="Run `mongomig current` to see what is applied.",
        )
    return [graph.scripts[rev] for rev in applied_known if keep in graph.ancestors(rev)]


def stamp_set(graph: RevisionGraph, targets: list[str]) -> list[Script]:
    """``stamp`` targets → the exact set of revisions to mark as applied."""
    if targets == ["base"]:
        return []
    revs: set[str] = set()
    for target in targets:
        resolved = graph.heads() if target == "heads" else [graph.resolve(target)]
        for rev in resolved:
            revs |= graph.ancestors(rev) | {rev}
    return [graph.scripts[rev] for rev in graph.topological_order() if rev in revs]


# --- execution ---------------------------------------------------------------------------


class Executor:
    def __init__(
        self,
        config: LoadedConfig,
        graph: RevisionGraph,
        db: Database[dict[str, Any]],
        *,
        reporter: Reporter | None = None,
    ) -> None:
        self.config = config
        self.graph = graph
        self.db = db
        self.reporter = reporter or Reporter()
        settings = config.settings
        self.tracker = MigrationTracker(db, settings.migrations.tracking_collection)
        self.lock = MigrationLock(
            db, settings.migrations.lock_collection, ttl_seconds=settings.execution.lock_ttl_seconds
        )

    def state(self) -> CurrentState:
        return compute_state(self.graph, self.tracker.records())

    def upgrade(
        self,
        target: str = "head",
        *,
        steps: int | None = None,
        lock_timeout: float = 0,
        confirm: UpgradeConfirmFn | None = None,
    ) -> RunResult:
        """Apply pending migrations.

        If the plan needs confirmation (see ``execution.confirm``), ``confirm`` is called with
        the reasons; without a ``confirm`` callback such a plan raises
        ``ConfirmationRequiredError`` instead of running.
        """
        result = RunResult(direction="upgrade")
        with self.lock.hold(lock_timeout):
            self.tracker.ensure()
            state = self._checked_state()
            plan = plan_upgrade(self.graph, state.applied, target, steps=steps)
            reasons = confirmation_reasons(plan, self.config.settings.execution.confirm)
            if reasons:
                if confirm is None:
                    raise ConfirmationRequiredError(
                        "These migrations need confirmation: " + "; ".join(reasons),
                        suggestion="Review them (`mongomig plan`), then re-run with --yes.",
                        details={"reasons": reasons},
                    )
                if not confirm(plan, reasons):
                    result.aborted = True
                    return result
            meta = self._meta()
            for script in plan:
                self.lock.check()
                result.steps.append(self._run(script, "upgrade", meta))
        return result

    def downgrade(
        self,
        target: str | None = None,
        *,
        steps: int | None = None,
        allow_irreversible: bool = False,
        confirm: ConfirmFn | None = None,
        lock_timeout: float = 0,
    ) -> RunResult:
        result = RunResult(direction="downgrade")
        with self.lock.hold(lock_timeout):
            state = self._checked_state()
            plan = plan_downgrade(self.graph, state.applied, target, steps=steps)
            if not plan:
                return result

            irreversible = [s for s in plan if not s.reversible]
            if irreversible and not allow_irreversible:
                first = irreversible[0]
                raise IrreversibleMigrationError(
                    f"Cannot downgrade: {first.revision} ({first.message}) is marked "
                    "reversible = False.",
                    suggestion=(
                        "Restore from a backup instead, or pass --force to run whatever "
                        "downgrade code exists and un-track the rest (data is NOT restored)."
                    ),
                    details={"irreversible": [s.revision for s in irreversible]},
                )
            if confirm is not None and not confirm(plan):
                result.aborted = True
                return result

            meta = self._meta()
            for script in plan:
                self.lock.check()
                result.steps.append(self._run(script, "downgrade", meta))
        return result

    def stamp(self, targets: list[str], *, lock_timeout: float = 0) -> list[Script]:
        scripts = stamp_set(self.graph, targets)
        with self.lock.hold(lock_timeout):
            self.tracker.ensure()
            meta = self._meta()
            self.tracker.stamp(scripts, meta=meta)
        return scripts

    def analyze(
        self,
        direction: Direction = "upgrade",
        target: str | None = None,
        *,
        steps: int | None = None,
    ) -> list[MigrationAnalysis]:
        """Dry-run the planned migrations: record their operations, estimate their impact.

        Nothing is written and the lock isn't taken. Each migration is simulated against the
        *current* data, so estimates for later migrations in a chain are approximate.
        """
        state = self._checked_state()
        if direction == "upgrade":
            plan = plan_upgrade(self.graph, state.applied, target or "head", steps=steps)
        else:
            plan = plan_downgrade(self.graph, state.applied, target, steps=steps)
        return [self._simulate(script, direction) for script in plan]

    # --- internals -----------------------------------------------------------------------

    def _simulate(self, script: Script, direction: Direction) -> MigrationAnalysis:
        from mongomig.migrations.dryrun import DryRunUnavailable, Recorder
        from mongomig.safety.impact import assess

        recorder = Recorder()
        ctx = MigrationContext(
            self.db,
            batch_size=self.config.settings.execution.batch_size,
            reporter=Reporter(),
            revision=script.revision,
            direction=direction,
            environment=self.config.environment,
            recorder=recorder,
        )
        unavailable = error = None
        if direction == "downgrade" and not script.has_downgrade:
            unavailable = "no downgrade() function"
        else:
            module = load_migration_module(script, self.config.root_dir)
            try:
                getattr(module, direction)(ctx)
            except DryRunUnavailable as exc:
                unavailable = str(exc)
            except Exception as exc:
                error = redact_text(f"{type(exc).__name__}: {exc}")
        assessment = assess(
            recorder.ops, reversible=script.reversible, unavailable=unavailable, error=error
        )
        from mongomig.safety.impact import Risk, is_sharded

        data_ops = {op.collection for op in recorder.ops if op.estimated_docs}
        for name in sorted(data_ops):
            if is_sharded(self.db, name):
                assessment.raise_to(
                    Risk.MEDIUM,
                    f"{name} is sharded: broad updates fan out to every shard; shard-key "
                    "changes must be written by hand",
                )
        return MigrationAnalysis(
            script=script,
            direction=direction,
            ops=recorder.ops,
            assessment=assessment,
            unavailable=unavailable,
            error=error,
        )

    def _checked_state(self) -> CurrentState:
        state = self.state()
        if state.modified:
            heads = " ".join(state.applied_heads) or "<rev>"
            raise ChecksumMismatchError(
                "Applied migration file(s) were modified after they ran: "
                + ", ".join(state.modified),
                suggestion=(
                    "Executed migrations must not be edited; revert the changes and put new "
                    "changes in a new revision. If the edit is harmless (comments), "
                    f"re-record checksums with `mongomig stamp {heads}`."
                ),
                details={
                    "revisions": state.modified,
                    "paths": [str(self.graph.scripts[r].path) for r in state.modified],
                },
            )
        if state.unknown:
            self.reporter.warn(
                "database has revisions with no file here: "
                + ", ".join(state.unknown)
                + " (is this code older than the database?)"
            )
        return state

    def _meta(self) -> dict[str, Any]:
        return run_metadata(self.config.environment, self.config.root_dir)

    def _run(self, script: Script, direction: Direction, meta: dict[str, Any]) -> StepResult:
        self.reporter.migration_started(script, direction)
        execution = self.config.settings.execution
        ctx = MigrationContext(
            self.db,
            batch_size=execution.batch_size,
            sleep_ms_between_batches=execution.sleep_ms_between_batches,
            max_retries=execution.max_retries,
            reporter=self.reporter,
            revision=script.revision,
            direction=direction,
            environment=self.config.environment,
            lock=self.lock,
        )

        if direction == "downgrade" and not script.has_downgrade:
            self.reporter.warn(f"{script.revision} has no downgrade(); only un-tracking it")
            self.tracker.remove(script.revision)
            self.reporter.migration_finished(script, direction, 0)
            return StepResult(script.revision, script.message, direction, 0, skipped_code=True)

        module = load_migration_module(script, self.config.root_dir)
        fn = getattr(module, direction)
        if direction == "upgrade":
            self.tracker.record_running(script, meta=meta)

        start = time.perf_counter()
        try:
            fn(ctx)
        except Exception as exc:
            error = redact_text(f"{type(exc).__name__}: {exc}")
            self.tracker.record_failed(script, error=f"{direction}: {error}", meta=meta)
            self.reporter.migration_failed(script, direction, exc)
            raise _execution_error(script, direction, ctx, exc, error) from exc

        duration_ms = int((time.perf_counter() - start) * 1000)
        if direction == "upgrade":
            self.tracker.record_applied(script, execution_time_ms=duration_ms, meta=meta)
        else:
            self.tracker.remove(script.revision)
        self.reporter.migration_finished(script, direction, duration_ms)
        return StepResult(script.revision, script.message, direction, duration_ms)


def confirmation_reasons(plan: list[Script], mode: str) -> list[str]:
    """Why an upgrade needs confirmation (empty: it doesn't). Reads code; runs nothing."""
    from mongomig.safety.impact import destructive_calls

    if mode == "never" or not plan:
        return []
    reasons: list[str] = []
    for script in plan:
        calls = destructive_calls(script)
        if calls:
            more = f" (+{len(calls) - 1} more)" if len(calls) > 1 else ""
            reasons.append(f"{script.revision} can delete data: {calls[0]}{more}")
        if not script.reversible:
            reasons.append(f"{script.revision} is irreversible")
    if mode == "always" and not reasons:
        reasons.append(f"{len(plan)} revision(s) will be applied (execution.confirm = always)")
    return reasons


def load_migration_module(script: Script, project_root: Path) -> ModuleType:
    """Import a revision file so its functions can run (project root importable)."""
    root = str(project_root)
    if root not in sys.path:
        sys.path.insert(0, root)
    name = f"_mongomig_revision_{script.revision}"
    spec = importlib.util.spec_from_file_location(name, script.path)
    if spec is None or spec.loader is None:
        raise ScriptError(f"Cannot load {script.path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(name, None)
        raise ScriptError(
            f"Error importing {script.path.name}: {type(exc).__name__}: {exc}",
            suggestion="Fix the error at the top level of the migration file (often an import).",
            details={"revision": script.revision, "path": str(script.path)},
        ) from exc
    return module


def _execution_error(
    script: Script,
    direction: Direction,
    ctx: MigrationContext,
    exc: BaseException,
    error: str,
) -> MigrationExecutionError:
    details: dict[str, Any] = {
        "revision": script.revision,
        "direction": direction,
        "path": str(script.path),
        "error": error,
    }
    where = ""
    if ctx.current is not None:
        details.update(
            operation=ctx.current.operation,
            collection=ctx.current.collection,
            processed=ctx.current.processed,
            batch=ctx.current.batch,
        )
        where = f" in {ctx.current.operation}"
        if ctx.current.collection:
            where += f" on {ctx.current.collection}"
        if ctx.current.processed:
            where += f" after {ctx.current.processed:,} documents"

    from pymongo.errors import DuplicateKeyError

    if isinstance(exc, DuplicateKeyError):
        suggestion = (
            "Resolve the duplicate values (see error), then re-run; completed batches are kept."
        )
    else:
        suggestion = (
            f"Fix the cause and re-run `mongomig {direction}`. ctx.ops operations are "
            "idempotent, so already-processed documents are not changed twice."
        )
    return MigrationExecutionError(
        f"Migration {script.revision} ({script.message}) failed during {direction}{where}: {error}",
        suggestion=suggestion,
        details=details,
    )
