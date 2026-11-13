"""Gates 4 and 5: the project still builds, and its tests still pass.

Both run once per language the project has; what they find adds up.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue

from forja.checks.base import decided, measure, overrides_from, rejected
from forja.checks.tooling import Tools
from forja.core.adapters import LanguageAdapter, Operation, OperationRequest
from forja.core.gates import Approved, Finding, GateInput, GateResult

FAILED = ("failed", "error")


async def outcomes_of(
    tools: Tools,
    adapters: Sequence[LanguageAdapter],
    worktree: Path,
    values: Mapping[str, JsonValue],
) -> dict[str, str]:
    """The outcome of every test every adapter ran, by test id; ``values`` are the policy's."""
    outcomes, _ = await results_of(tools, adapters, worktree, values)
    return outcomes


async def results_of(
    tools: Tools,
    adapters: Sequence[LanguageAdapter],
    worktree: Path,
    values: Mapping[str, JsonValue],
) -> tuple[dict[str, str], dict[str, str]]:
    """The outcome of every test every adapter ran and, for the failed ones whose adapter says
    it (``test.failure``), what the failure was: the name of the exception."""
    outcomes: dict[str, str] = {}
    failures: dict[str, str] = {}
    for adapter in adapters:
        parsed = await measure(
            tools,
            adapter,
            Operation.TEST,
            "tests",
            OperationRequest(root=worktree),
            overrides_from(adapter, values),
        )
        outcomes |= {
            d.target.id: str(d.value) for d in parsed.drafts if d.measurement == "test.outcome"
        }
        failures |= {
            d.target.id: str(d.value) for d in parsed.drafts if d.measurement == "test.failure"
        }
    return outcomes, failures


@dataclass(frozen=True)
class BuildCheck:
    tools: Tools
    adapters: Sequence[LanguageAdapter]
    worktree: Path
    id: str = "build"
    gate: int = 4
    requires: frozenset[str] = frozenset()

    async def evaluate(self, data: GateInput) -> GateResult:
        return await decided(self._judge(data))

    async def _judge(self, data: GateInput) -> GateResult:
        request = OperationRequest(root=self.worktree)
        findings = []
        for adapter in self.adapters:
            overrides = overrides_from(adapter, data.values)
            parsed = await measure(
                self.tools, adapter, Operation.BUILD, "build", request, overrides
            )
            findings += [
                Finding(
                    message="the file does not compile"
                    if draft.measurement == "build.compile_error"
                    else "the build failed",
                    target=draft.target.id,
                )
                for draft in parsed.drafts
            ]
        return rejected(findings) if findings else Approved()


@dataclass(frozen=True)
class TestsCheck:
    """Every test passes, except the ones the base already showed to be unstable (quarantine)."""

    __test__ = False  # not a test class, whatever its name says to pytest

    tools: Tools
    adapters: Sequence[LanguageAdapter]
    worktree: Path
    quarantine: frozenset[str] = frozenset()
    passing: frozenset[str] = frozenset()  # tests that passed on the base, both times
    id: str = "tests"
    gate: int = 5
    requires: frozenset[str] = frozenset()

    async def evaluate(self, data: GateInput) -> GateResult:
        return await decided(self._judge(data))

    async def _judge(self, data: GateInput) -> GateResult:
        outcomes = await outcomes_of(self.tools, self.adapters, self.worktree, data.values)
        findings = [
            Finding(message=f"test {outcome}", target=test)
            for test, outcome in sorted(outcomes.items())
            if outcome in FAILED and test not in self.quarantine
        ]
        # a test that vanished, was skipped or deleted is a failure nobody reported
        findings += [
            Finding(
                message="a test that passed on the base is missing or did not pass", target=test
            )
            for test in sorted(self.passing - self.quarantine)
            if outcomes.get(test) != "passed" and test not in {f.target for f in findings}
        ]
        return rejected(findings) if findings else Approved()
