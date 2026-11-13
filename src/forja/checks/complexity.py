"""Gate 8 (the part Forja has today): no function got more complex than the limit allows.

The tool is lizard, which reads many languages, so this check is not tied to one. The limit comes
from the policy per language. A function above it reproves only when it is new or got worse: old
complexity is debt for the planner, not a reason to refuse an unrelated improvement.
"""

import csv
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue

from forja.checks.base import Undecided, decided, rejected
from forja.checks.changes import Changes, write_files
from forja.checks.tooling import Tools
from forja.core.adapters import CommandPlan
from forja.core.gates import Approved, Finding, GateInput, GateResult, Inconclusive
from forja.core.gates.models import InconclusiveReason

DEFAULT_MAX_CCN = 15  # lizard's own default
ROW_AT_LEAST = 4  # more fields than this is a row of the report, not a warning lizard printed
FIELDS = 11  # NLOC, CCN, tokens, params, length, "name@lines@file", file, name, long name, from, to

Functions = dict[tuple[str, str], int]


def lizard_plan(lizard: tuple[str, ...], root: Path, files: tuple[str, ...]) -> CommandPlan:
    """Lizard over ``files`` as CSV, one row per function."""
    return CommandPlan((*lizard, "--csv", "--", *files), root, env_set={"NO_COLOR": "1"})


def max_ccn(data: GateInput, language: str) -> int:
    return ccn_limit(data.values, language)


def ccn_limit(values: Mapping[str, JsonValue], language: str) -> int:
    """The limit the policy sets for ``language``, or lizard's own."""
    thresholds = values.get("thresholds")
    per_language = thresholds.get(language) if isinstance(thresholds, dict) else None
    value = per_language.get("max_ccn") if isinstance(per_language, dict) else None
    return int(value) if isinstance(value, int | float) and value > 0 else DEFAULT_MAX_CCN


@dataclass(frozen=True)
class Function:
    ccn: int
    first: int  # first and last line of the function
    last: int


def parse_spans(lines: list[str]) -> dict[tuple[str, str], Function]:
    """Complexity and place by (file, function). Methods with the same name keep the worst one."""
    found: dict[tuple[str, str], Function] = {}
    for row in csv.reader(lines):
        if len(row) != FIELDS or not (row[1].isdigit() and row[9].isdigit() and row[10].isdigit()):
            continue
        key = (row[6].replace("\\", "/"), row[7])
        function = Function(int(row[1]), int(row[9]), int(row[10]))
        worst = found.get(key)
        if worst is None or function.ccn > worst.ccn:
            found[key] = function
    return found


def parse_functions(lines: list[str]) -> Functions:
    """Complexity by (file, function). Methods with the same name keep the worst one."""
    return {key: function.ccn for key, function in parse_spans(lines).items()}


def origin(changes: Changes, path: str) -> str:
    """Where ``path`` was before the attempt, which differs when the attempt renamed it."""
    for change in changes.files:
        if change.path == path:
            return change.before or path
    return path


async def read_spans(
    tools: Tools, lizard: tuple[str, ...], root: Path, files: tuple[str, ...]
) -> dict[tuple[str, str], Function]:
    plan = lizard_plan(lizard, root, files)
    execution = await tools.run("complexity", plan)
    if not execution.finished:
        raise Undecided(execution.inconclusive("complexity"))
    if execution.result.exit_code != 0:
        detail = f"complexity: lizard ended with {execution.result.exit_code}"
        raise Undecided(
            Inconclusive(
                reason=InconclusiveReason.TOOL_FAILED,
                detail=detail,
                tool_run_ids=(execution.operation_id,),
            )
        )
    spans = parse_spans(execution.lines)
    if not spans and any(len(row) > ROW_AT_LEAST for row in csv.reader(execution.lines)):
        # lizard printed rows and none is one this reader knows (another version, other
        # columns): no function read is not the same as no function too complex
        raise Undecided(
            Inconclusive(
                reason=InconclusiveReason.TOOL_FAILED,
                detail="complexity: lizard printed its report in a form this reader does not know",
                tool_run_ids=(execution.operation_id,),
            )
        )
    return spans


@dataclass(frozen=True)
class ComplexityCheck:
    tools: Tools
    worktree: Path
    changes: Changes
    files: tuple[str, ...]  # production files the attempt changed that lizard should read
    before: dict[str, str]  # the parent commit's text of those files, by old path
    lizard: tuple[str, ...]
    language: str = "python"
    id: str = "complexity"
    gate: int = 8
    requires: frozenset[str] = frozenset({"structural_meters"})

    async def evaluate(self, data: GateInput) -> GateResult:
        return await decided(self._judge(data))

    async def _judge(self, data: GateInput) -> GateResult:
        if not self.files:
            return Approved()
        limit = max_ccn(data, self.language)
        after = await self._read(self.worktree, self.files)
        was = await self._before()
        findings = []
        for (path, name), ccn in sorted(after.items()):
            old = was.get((self._origin(path), name))
            if ccn > limit and (old is None or ccn > old):
                message = f"function {name} is too complex"
                findings.append(
                    Finding(message=message, target=f"{path}::{name}", measured=ccn, limit=limit)
                )
        return rejected(findings) if findings else Approved()

    def _origin(self, path: str) -> str:
        return origin(self.changes, path)

    async def _before(self) -> Functions:
        if not self.before:
            return {}
        with tempfile.TemporaryDirectory(prefix="forja-before-") as directory:
            write_files(self.before, Path(directory))
            return await self._read(Path(directory), tuple(sorted(self.before)))

    async def _read(self, root: Path, files: tuple[str, ...]) -> Functions:
        spans = await read_spans(self.tools, self.lizard, root, files)
        return {key: function.ccn for key, function in spans.items()}
