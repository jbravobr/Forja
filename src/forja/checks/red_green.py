"""Gate 5 for the test author: the new tests fail on the code with the defect, nothing else does."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from forja.checks.base import decided, rejected
from forja.checks.build_and_tests import results_of
from forja.checks.tooling import Tools
from forja.core.adapters import FileClass, LanguageAdapter
from forja.core.artifacts.llm_output import DeclaredTest
from forja.core.gates import Approved, Finding, GateInput, GateResult

FAILED = ("failed", "error")
# What a test raises when it is the test that is wrong: a name it uses and nobody defined is
# still undefined after the defect is repaired, so the test can never pass.
OWN_FAULT = frozenset({"NameError"})


@dataclass(frozen=True)
class RedGreenCheck:
    """Stands in for the plain tests check while the test author works.

    Every criterion has a declared test; every declared test exists and fails (an error of import
    or collection is not a failure of the defect, and neither is a failure the test brought on
    itself, ``OWN_FAULT``); no other test fails, and the tests that passed
    on the base still pass.
    """

    tools: Tools
    adapters: Sequence[LanguageAdapter]
    worktree: Path
    classify: Callable[[str], FileClass]
    criteria: frozenset[int]
    declared: tuple[DeclaredTest, ...]
    quarantine: frozenset[str] = frozenset()
    passing: frozenset[str] = frozenset()
    id: str = "red_green"
    gate: int = 5
    requires: frozenset[str] = frozenset()

    async def evaluate(self, data: GateInput) -> GateResult:
        findings = self._declaration()
        if findings:
            return rejected(findings)
        return await decided(self._judge(data))

    def _declaration(self) -> list[Finding]:
        findings = [
            Finding(message="no test is declared for this criterion", target=f"criterion {n}")
            for n in sorted(self.criteria - {t.criterion_id for t in self.declared})
        ]
        findings += [
            Finding(message="the declared test covers no criterion of the task", target=t.test_id)
            for t in self.declared
            if t.criterion_id not in self.criteria
        ]
        for test in self.declared:
            if not (self.worktree / test.path).is_file():
                findings.append(
                    Finding(message="the declared test file does not exist", target=test.path)
                )
            elif self.classify(test.path) is not FileClass.TEST:
                findings.append(
                    Finding(message="the declared file is not a test file", target=test.path)
                )
            elif not self._owns(test):
                findings.append(
                    Finding(
                        message=f"the test id does not belong to {test.path}", target=test.test_id
                    )
                )
        return findings

    def _owns(self, test: DeclaredTest) -> bool:
        """Whether the id can belong to the file: the adapter that claims the file says so; with
        no word from it, the id is pytest's, ``path::name``."""
        for adapter in self.adapters:
            if adapter.classify(test.path) is not None:
                owns = getattr(adapter, "owns_test", None)
                if owns is not None:
                    return bool(owns(test.test_id, test.path, self._text_of(test.path)))
                break
        return test.test_id.startswith(f"{test.path}::")

    def _text_of(self, path: str) -> str:
        try:
            return (self.worktree / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    async def _judge(self, data: GateInput) -> GateResult:
        outcomes, raised = await results_of(self.tools, self.adapters, self.worktree, data.values)
        wanted = {t.test_id for t in self.declared}
        findings = []
        for test in sorted(wanted):
            # a file that cannot be collected is reported under its own path, not its tests'
            outcome = outcomes.get(test) or outcomes.get(test.split("::")[0])
            if outcome == "failed":
                if raised.get(test) in OWN_FAULT:
                    message = (
                        f"the declared test fails on its own mistake ({raised[test]}), "
                        "which no change to the code can repair"
                    )
                    findings.append(Finding(message=message, target=test))
                continue
            message = {
                None: "the declared test did not run",
                "error": "the declared test errors out instead of failing (import or collection)",
                "passed": "the declared test passes on the code with the defect",
            }.get(outcome, f"the declared test is {outcome}, not failing")
            findings.append(Finding(message=message, target=test))
        reported = {f.target for f in findings}
        findings += [
            Finding(message=f"test {outcome}", target=test)
            for test, outcome in sorted(outcomes.items())
            if outcome in FAILED
            and test not in wanted
            and test not in self.quarantine
            and test not in {t.split("::")[0] for t in reported}
        ]
        findings += [
            Finding(
                message="a test that passed on the base is missing or did not pass", target=test
            )
            for test in sorted(self.passing - self.quarantine)
            if outcomes.get(test) != "passed" and test not in {f.target for f in findings}
        ]
        return rejected(findings) if findings else Approved()
