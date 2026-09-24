"""The revision DAG built from ``down_revision`` (and ``depends_on``) links."""

from __future__ import annotations

import heapq
from collections.abc import Iterable

from mongomig.errors import (
    AmbiguousRevisionError,
    MultipleHeadsError,
    RevisionConflictError,
    RevisionNotFoundError,
)
from mongomig.migrations.script import Script

MIN_PREFIX_LENGTH = 4


class RevisionGraph:
    def __init__(self, scripts: Iterable[Script]) -> None:
        self.scripts: dict[str, Script] = {}
        for script in scripts:
            if script.revision in self.scripts:
                raise RevisionConflictError(f"Duplicate revision id {script.revision!r}.")
            self.scripts[script.revision] = script

        self.children: dict[str, set[str]] = {rev: set() for rev in self.scripts}
        for script in self.scripts.values():
            for parent in script.down_revisions:
                if parent not in self.scripts:
                    raise RevisionConflictError(
                        f"Revision {script.revision} ({script.path.name}) points to "
                        f"down_revision {parent!r}, which does not exist.",
                        suggestion="Restore the missing revision file or fix down_revision.",
                        details={"path": str(script.path)},
                    )
                self.children[parent].add(script.revision)
            for dep in script.depends_on:
                if self._lookup_label(dep) is None and dep not in self.scripts:
                    raise RevisionConflictError(
                        f"Revision {script.revision} depends_on {dep!r}, which does not exist.",
                        details={"path": str(script.path)},
                    )

        self._order = self._topological_order()

    def __len__(self) -> int:
        return len(self.scripts)

    def __contains__(self, rev: object) -> bool:
        return rev in self.scripts

    # --- structure ------------------------------------------------------------------------

    def heads(self) -> list[str]:
        """Revisions nothing builds on, in chronological order."""
        return [rev for rev in self._order if not self.children[rev]]

    def bases(self) -> list[str]:
        return [rev for rev in self._order if self.scripts[rev].is_base]

    def topological_order(self) -> list[str]:
        """Parents before children; ties broken by file name (i.e. creation time)."""
        return list(self._order)

    def single_head(self) -> str | None:
        heads = self.heads()
        if len(heads) > 1:
            raise MultipleHeadsError(
                f"Multiple heads: {', '.join(heads)}.",
                suggestion="Pass --head <revision> to choose a parent, or merge the branches.",
                details={"heads": heads},
            )
        return heads[0] if heads else None

    def ancestors(self, rev: str) -> set[str]:
        """All revisions ``rev`` builds on (excluding itself)."""
        seen: set[str] = set()
        stack = list(self._dependencies(rev))
        while stack:
            current = stack.pop()
            if current not in seen:
                seen.add(current)
                stack.extend(self._dependencies(current))
        return seen

    # --- lookup ---------------------------------------------------------------------------

    def resolve(self, ref: str) -> str:
        """Resolve a full id, a unique prefix (>= 4 chars), a branch label, or ``head``."""
        ref = ref.strip()
        if ref == "head":
            head = self.single_head()
            if head is None:
                raise RevisionNotFoundError("There are no revisions yet.")
            return head
        if ref in self.scripts:
            return ref
        labelled = self._lookup_label(ref)
        if labelled is not None:
            return labelled
        if len(ref) >= MIN_PREFIX_LENGTH:
            matches = sorted(rev for rev in self.scripts if rev.startswith(ref))
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise AmbiguousRevisionError(
                    f"Revision prefix {ref!r} is ambiguous: {', '.join(matches)}.",
                    suggestion="Use more characters of the revision id.",
                )
        hint = (
            f"Prefixes need at least {MIN_PREFIX_LENGTH} characters."
            if len(ref) < MIN_PREFIX_LENGTH
            else "Run `mongomig history` to list revisions."
        )
        raise RevisionNotFoundError(f"Unknown revision {ref!r}.", suggestion=hint)

    # --- internals ------------------------------------------------------------------------

    def _dependencies(self, rev: str) -> tuple[str, ...]:
        script = self.scripts[rev]
        deps = [self._lookup_label(d) or d for d in script.depends_on]
        return (*script.down_revisions, *deps)

    def _lookup_label(self, label: str) -> str | None:
        found = [rev for rev, s in self.scripts.items() if label in s.branch_labels]
        if len(found) > 1:
            raise RevisionConflictError(
                f"Branch label {label!r} is used by several revisions: {', '.join(found)}."
            )
        return found[0] if found else None

    def _topological_order(self) -> list[str]:
        indegree = {rev: 0 for rev in self.scripts}
        dependents: dict[str, list[str]] = {rev: [] for rev in self.scripts}
        for rev in self.scripts:
            for dep in set(self._dependencies(rev)):
                indegree[rev] += 1
                dependents[dep].append(rev)

        def key(rev: str) -> tuple[str, str]:
            return (self.scripts[rev].path.name, rev)

        ready = [key(rev) for rev, n in indegree.items() if n == 0]
        heapq.heapify(ready)
        order: list[str] = []
        while ready:
            _, rev = heapq.heappop(ready)
            order.append(rev)
            for child in dependents[rev]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    heapq.heappush(ready, key(child))

        if len(order) != len(self.scripts):
            cyclic = sorted(rev for rev, n in indegree.items() if n > 0)
            raise RevisionConflictError(
                f"Revision graph contains a cycle involving: {', '.join(cyclic)}.",
                suggestion="Fix down_revision/depends_on so revisions don't reference each other.",
            )
        return order
