"""Gate 3: the files are in the canonical format, and lint has no new violation (the ratchet).

Each file goes to the adapter that claims it. An adapter that offers no such operation (a linter
the project does not use) leaves its files alone; the check is not applicable only when none does.
"""

import shutil
import tempfile
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from forja.checks.base import Undecided, decided, measure, overrides_from, rejected
from forja.checks.changes import ChangeKind, Changes, write_files
from forja.checks.tooling import Tools
from forja.core.adapters import (
    AdapterRegistry,
    EvidenceDraft,
    LanguageAdapter,
    Operation,
    OperationRequest,
    Parsed,
)
from forja.core.adapters.projects import owner_of
from forja.core.adapters.scoped import ScopedAdapter
from forja.core.gates import (
    Approved,
    Finding,
    GateInput,
    GateResult,
    Inconclusive,
    InconclusiveReason,
    NotApplicable,
)


def by_adapter(
    projects: Sequence[ScopedAdapter], files: tuple[str, ...]
) -> list[tuple[ScopedAdapter, tuple[str, ...]]]:
    """The files of each project (the one that owns them), in the order the files are given."""
    groups: dict[int, list[str]] = {}
    for path in files:
        owner = owner_of(projects, path)
        if owner is not None:
            groups.setdefault(projects.index(owner), []).append(path)
    return [(projects[index], tuple(paths)) for index, paths in groups.items()]


def own_files(adapters: AdapterRegistry) -> Callable[[str], bool]:
    return lambda path: adapters.for_file(path) is not None


def owned_by(projects: Sequence[ScopedAdapter]) -> Callable[[str], bool]:
    return lambda path: owner_of(projects, path) is not None


async def measured(probe: Awaitable[Parsed]) -> Parsed | None:
    """What the operation measured, ``None`` when the adapter does not offer it."""
    try:
        return await probe
    except Undecided as stop:
        if isinstance(stop.result, NotApplicable):
            return None
        raise


@dataclass(frozen=True)
class FormatCheck:
    """Whatever the attempt wrote must already be what the formatter would write."""

    tools: Tools
    projects: Sequence[ScopedAdapter]
    worktree: Path
    changes: Changes
    id: str = "format"
    gate: int = 3
    requires: frozenset[str] = frozenset()

    async def evaluate(self, data: GateInput) -> GateResult:
        return await decided(self._judge(data))

    async def _judge(self, data: GateInput) -> GateResult:
        files = self.changes.current(owned_by(self.projects))
        if not files:
            return Approved()
        findings: list[Finding] = []
        offered = False
        for adapter, own in by_adapter(self.projects, files):
            request = OperationRequest(root=self.worktree, files=own, check_only=True)
            overrides = overrides_from(adapter, data.values)
            parsed = await measured(
                measure(self.tools, adapter, Operation.FORMAT, "format", request, overrides)
            )
            if parsed is None:
                continue
            offered = True
            for draft in parsed.drafts:
                if draft.measurement == "format.invalid_syntax":
                    message = "the file does not parse"
                else:
                    message = "the file is not in the canonical format"
                findings.append(Finding(message=message, target=draft.target.id))
        if not offered:
            return NotApplicable(missing_capability=Operation.FORMAT.value)
        return rejected(findings) if findings else Approved()


def skip_git_and_links(directory: str, names: list[str]) -> set[str]:
    """What the private copy leaves out: git's own directory and any link, which could lead out of
    the copy when the old texts are written into it."""
    base = Path(directory)
    return {n for n in names if n == ".git" or (base / n).is_symlink() or (base / n).is_junction()}


def violations(drafts: tuple[EvidenceDraft, ...], family: str = "lint") -> Counter[tuple[str, str]]:
    """Violations per (file, rule); ``family`` is the part of the measurement before the rule."""
    counts: Counter[tuple[str, str]] = Counter()
    for draft in drafts:
        counts[(draft.target.id, draft.measurement.removeprefix(f"{family}."))] += int(draft.value)
    return counts


@dataclass(frozen=True)
class LintCheck:
    """Only violations the attempt added reprove: the old ones are a task for the planner.

    A violation is counted per file and rule, so moving code inside a file, or renaming the file,
    does not make an old violation look new. An adapter whose linter runs on the new files but not
    on the old version has nothing to compare with: the answer is inconclusive, never a pass.

    The same ratchet judges the type errors of an adapter that offers them (``TypesCheck``): only
    the operation, the name of its runs and the words of the message change.
    """

    tools: Tools
    projects: Sequence[ScopedAdapter]
    worktree: Path
    changes: Changes
    before: dict[str, str]  # the parent commit's text of the changed files, by old path
    id: str = "lint"
    gate: int = 3
    requires: frozenset[str] = frozenset()
    operation: Operation = Operation.LINT
    name: str = "lint"  # the name of its tool runs and the family of its measurements
    noun: str = "violation"
    tool: str = "linter"
    unmeasurable: InconclusiveReason = InconclusiveReason.TOOL_FAILED  # why, when refused

    async def evaluate(self, data: GateInput) -> GateResult:
        return await decided(self._judge(data))

    async def _judge(self, data: GateInput) -> GateResult:
        files = self.changes.current(owned_by(self.projects))
        if not files:
            return Approved()
        self._refused(files)
        findings: list[Finding] = []
        offered = False
        for adapter, own in by_adapter(self.projects, files):
            overrides = overrides_from(adapter, data.values)
            request = OperationRequest(root=self.worktree, files=own)
            parsed = await measured(
                measure(self.tools, adapter, self.operation, self.name, request, overrides)
            )
            if parsed is None:
                continue
            was = await self._before(adapter, overrides)
            if was is None:
                raise Undecided(
                    Inconclusive(
                        reason=InconclusiveReason.TOOL_FAILED,
                        detail=(
                            f"{self.name}: the {adapter.language} {self.tool} "
                            "cannot run on the old files"
                        ),
                    )
                )
            offered = True
            for (path, rule), count in sorted(violations(parsed.drafts, self.name).items()):
                old = was[(self._origin(path), rule)]
                if count > old:
                    message = f"{count - old} new {rule} {self.noun}(s)"
                    findings.append(
                        Finding(message=message, target=path, measured=count, limit=old)
                    )
        if not offered:
            return NotApplicable(missing_capability=self.operation.value)
        return rejected(findings) if findings else Approved()

    def _refused(self, files: tuple[str, ...]) -> None:
        """An adapter may say it cannot measure a project the way the project is meant to be
        measured (its linter is not installed, its settings load plugins the Forja does not
        run). That is not a language without the operation: the answer is inconclusive, never
        a pass and never a count."""
        for adapter, _ in by_adapter(self.projects, files):
            ask = getattr(adapter.inner, "unmeasurable", None)
            why = ask(self.operation, adapter.directory(self.worktree)) if callable(ask) else None
            if why:
                detail = f"{self.name}: {why}"
                raise Undecided(Inconclusive(reason=self.unmeasurable, detail=detail))

    def _origin(self, path: str) -> str:
        """Where the file was before this attempt: its old name if it was renamed."""
        for change in self.changes.files:
            if change.path == path:
                return change.before or path
        return path

    async def _before(
        self, adapter: LanguageAdapter, overrides: dict[str, dict[str, tuple[str, ...]]]
    ) -> Counter[tuple[str, str]] | None:
        """Lint of the parent commit's version of the adapter's files, in a private copy of the
        worktree turned back into that commit: a linter that type-checks needs the files next to
        the ones it is asked about. ``None`` when the linter cannot run there."""
        texts = {
            path: text
            for path, text in self.before.items()
            if owner_of(self.projects, path) is adapter
        }
        if not texts:
            return Counter()
        with tempfile.TemporaryDirectory(prefix="forja-before-") as directory:
            root = Path(directory)
            shutil.copytree(
                self.worktree,
                root,
                symlinks=True,
                dirs_exist_ok=True,
                ignore=skip_git_and_links,
            )
            for change in self.changes.files:
                if change.kind in (ChangeKind.ADDED, ChangeKind.RENAMED):
                    (root / change.path).unlink(missing_ok=True)
            write_files(texts, root)
            request = OperationRequest(root=root, files=tuple(sorted(texts)))
            parsed = await measured(
                measure(
                    self.tools, adapter, self.operation, f"{self.name}_before", request, overrides
                )
            )
            return None if parsed is None else violations(parsed.drafts, self.name)


@dataclass(frozen=True)
class TypesCheck(LintCheck):
    """Only type errors the attempt added reprove, by file and error code (gate 3, observation)."""

    id: str = "types"
    operation: Operation = Operation.TYPES
    name: str = "types"
    noun: str = "error"
    tool: str = "type checker"
    # settings that load plugins: the errors counted without them are not the project's
    unmeasurable: InconclusiveReason = InconclusiveReason.UNSUPPORTED_CONSTRUCT
