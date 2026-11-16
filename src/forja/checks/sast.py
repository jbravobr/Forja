"""Gate 10: Semgrep finds nothing new in the files the attempt changed (the ratchet).

Semgrep's own baseline mode needs a clean git tree, and the attempt's work is in the working tree,
so the old version is scanned instead: the parent commit's text of the changed files, in a private
copy of the worktree. A finding is counted per file and rule, so moving code, or renaming the file,
does not make an old finding look new. Without rules the check does not run: the user supplies
them, and "not applicable" is never a pass.
"""

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from forja.checks.base import Undecided, decided, rejected
from forja.checks.changes import Changes
from forja.checks.oldtree import old_tree
from forja.checks.tooling import Tools
from forja.core.adapters import CommandPlan
from forja.core.gates import (
    Approved,
    Finding,
    GateInput,
    GateResult,
    Inconclusive,
    InconclusiveReason,
    NotApplicable,
)

CONFIG = "semgrep_config"
Counts = Counter[tuple[str, str]]


@dataclass(frozen=True)
class SastCheck:
    tools: Tools
    worktree: Path
    changes: Changes
    before: dict[str, str]  # the parent commit's text of the changed files, by old path
    reports: Path  # a directory outside the worktree for the reports
    config: Path | None  # the rules the user supplied; none, no scan
    semgrep: tuple[str, ...] = ("semgrep",)
    id: str = "sast"
    gate: int = 10
    requires: frozenset[str] = frozenset()

    async def evaluate(self, data: GateInput) -> GateResult:
        return await decided(self._judge())

    async def _judge(self) -> GateResult:
        if self.config is None:
            return NotApplicable(missing_capability=CONFIG)
        files = self.changes.current()
        if not files:
            return Approved()
        self.reports.mkdir(parents=True, exist_ok=True)
        found = await self._scan(self.config, self.worktree, files, "sast")
        was = await self._before(self.config)
        findings = []
        for (path, rule), count in sorted(found.items()):
            old = was[(self._origin(path), rule)]
            if count > old:
                message = f"{count - old} new {rule} finding(s)"
                findings.append(Finding(message=message, target=path, measured=count, limit=old))
        return rejected(findings) if findings else Approved()

    def _origin(self, path: str) -> str:
        """Where the file was before this attempt: its old name if it was renamed."""
        for change in self.changes.files:
            if change.path == path:
                return change.before or path
        return path

    async def _before(self, config: Path) -> Counts:
        """The findings in the parent commit's text of the changed files, in a private copy of the
        worktree turned back into that commit so the rules see the files next to them."""
        if not self.before:
            return Counter()
        with old_tree(self.worktree, self.changes, self.before, "forja-sast-before-") as root:
            return await self._scan(config, root, tuple(sorted(self.before)), "sast_before")

    async def _scan(self, config: Path, root: Path, files: tuple[str, ...], name: str) -> Counts:
        report = self.reports / f"{name}.json"
        report.unlink(missing_ok=True)
        plan = CommandPlan(
            (
                *self.semgrep,
                "scan",
                "--config",
                str(config),
                "--json",
                f"--json-output={report}",
                "--metrics=off",
                "--disable-version-check",
                "--quiet",
                *(f"./{path}" if path.startswith("-") else path for path in files),
            ),
            root,
        )
        execution = await self.tools.run(name, plan)
        if not execution.finished:
            raise Undecided(execution.inconclusive(name))
        code = execution.result.exit_code
        if code != 0:
            raise _failed(f"{name}: semgrep ended with {code}", execution.operation_id)
        counts = _count(report, config)
        if counts is None:
            raise _failed(f"{name}: the semgrep report cannot be read", execution.operation_id)
        return counts


def _failed(detail: str, operation_id: str) -> Undecided:
    return Undecided(
        Inconclusive(
            reason=InconclusiveReason.TOOL_FAILED, detail=detail, tool_run_ids=(operation_id,)
        )
    )


def _rule(check_id: str, config: Path) -> str:
    """The rule as the person wrote it: Semgrep puts the path of the rules directory, with dots,
    before the id of every rule it reads from one."""
    return check_id.partition(f".{config.name}.")[2] or check_id


def _count(report: Path, config: Path) -> Counts | None:
    """Findings per (file, rule) of a Semgrep JSON report, ``None`` when it is not one."""
    try:
        results = json.loads(report.read_text(encoding="utf-8"))["results"]
        counts: Counts = Counter()
        for result in results:
            path = str(result["path"]).replace("\\", "/").removeprefix("./")
            counts[(path, _rule(str(result["check_id"]), config))] += 1
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return counts
