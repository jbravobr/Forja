"""Scripted ports: real processes, real git, real manifests; only the judgment is made up."""

import json
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forja.core.adapters.executor import ErrorActivity, ErrorKind
from forja.core.artifacts.llm_output import Claim, Criterion, DeclaredTest, LeftOut, LlmOutput
from forja.core.artifacts.models import Assumption, Executor
from forja.core.dependencies import is_dependency
from forja.core.events import OpenOperation
from forja.core.gates import (
    Approved,
    CheckRun,
    CheckSettings,
    Finding,
    FindingKind,
    Inconclusive,
    InconclusivePolicy,
    InconclusiveReason,
    Mode,
    PipelineOutcome,
    Rejected,
)
from forja.core.gates.effects import decide_effect
from forja.core.gates.models import GateResult
from forja.core.idempotency import (
    Decision,
    Reconciliation,
    reconcile_apply_diff,
    reconcile_prepare_workcopy,
)
from forja.core.manifest import build_manifest, find_violations
from forja.core.process import EventSink, ProcessRunner, ProcessSpec
from forja.orchestrator.config import Budget
from forja.orchestrator.operations import Step
from forja.orchestrator.ports import (
    AgentResult,
    AttemptContext,
    Baseline,
    Expectations,
    PlannedTask,
    RecordFile,
    RejectedWork,
    SinkFactory,
)

AGENT_SCRIPT = Path(__file__).with_name("agent_script.py")
BLOCKING = CheckSettings(Mode.BLOCKING, InconclusivePolicy.BLOCK)
PREMISE = CheckSettings(Mode.BLOCKING, InconclusivePolicy.PREMISE)


def outcome(result: GateResult, settings: CheckSettings = BLOCKING) -> PipelineOutcome:
    run = CheckRun("complexity", 8, settings, result, decide_effect(settings, result))
    return PipelineOutcome((run,))


PASSED = outcome(Approved())
REJECTED = outcome(Rejected(findings=(Finding(message="too complex", target="src/a.py::f"),)))
_NO_TEST = Rejected(
    findings=(
        Finding(
            message="no test runs this new line",
            target="src/a.py:2",
            kind=FindingKind.UNTESTED_LINE,
        ),
    )
)
UNTESTED = PipelineOutcome(
    (CheckRun("mutation", 7, BLOCKING, _NO_TEST, decide_effect(BLOCKING, _NO_TEST)),)
)
INCONCLUSIVE = outcome(
    Inconclusive(reason=InconclusiveReason.TOOL_FAILED, detail="the tool crashed")
)
PASSED_WITH_A_GAP = outcome(
    Inconclusive(reason=InconclusiveReason.TIMED_OUT, detail="slow"), PREMISE
)


@dataclass
class ScriptedMeasurer:
    """Judges attempts in the order of ``verdicts``; the last one repeats."""

    verdicts: Sequence[PipelineOutcome] = (PASSED,)
    baseline_ok: bool = True
    integration_verdicts: Sequence[PipelineOutcome] = (PASSED,)
    judged: list[str] = field(default_factory=list)
    expected: list[Expectations | None] = field(default_factory=list)
    metrics: Mapping[str, float] = field(default_factory=lambda: {"complexity": 12.0})
    formatted: bool = True
    stage_numbers: Sequence[tuple[str, float | None, float | None]] = ()

    async def format_worktree(self, worktree: Path) -> None:
        if self.formatted:
            (worktree / "base.txt").write_text("formatted\n")

    async def baseline(self, worktree: Path, sinks: SinkFactory) -> Baseline:
        if not self.baseline_ok:
            return Baseline(False, ("tests fail on the base",))
        return Baseline(True, (), self.metrics)

    async def judge(
        self, ctx: AttemptContext, sinks: SinkFactory, expectations: Expectations | None = None
    ) -> PipelineOutcome:
        self.judged.append(ctx.attempt_id)
        self.expected.append(expectations)
        return self.verdicts[min(len(self.judged), len(self.verdicts)) - 1]

    async def integration(self, worktree: Path, sinks: SinkFactory) -> PipelineOutcome:
        self.integrations = getattr(self, "integrations", 0) + 1
        return self.integration_verdicts[min(self.integrations, len(self.integration_verdicts)) - 1]

    async def readme_report(self, ctx: AttemptContext) -> str:
        return ""

    async def adr_report(self, ctx: AttemptContext) -> str:
        return ""

    async def documents(self, ctx: AttemptContext) -> list[str]:
        return list(getattr(self, "documented", []))

    async def stage_metrics(
        self, ctx: AttemptContext, sinks: SinkFactory
    ) -> Sequence[tuple[str, float | None, float | None]]:
        return tuple(self.stage_numbers)

    async def change_record(
        self,
        ctx: AttemptContext,
        sinks: SinkFactory,
        claims: Sequence[Claim],
        gates_passed: Sequence[str],
        executors: Sequence[Executor],
    ) -> RecordFile:
        self.recorded = [*getattr(self, "recorded", []), (ctx.task.task_id, list(gates_passed))]
        return RecordFile(f"docs/mudancas/{ctx.task.task_id}.md", f"record of {ctx.task.task_id}\n")

    async def reconcile(self, operation: OpenOperation) -> Reconciliation:
        return Reconciliation(Decision.REDO)


@dataclass
class ScriptedPlanner:
    tasks: Sequence[PlannedTask] = (
        PlannedTask("reduce the complexity of f", ("src/a.py",), 8, 400),
    )

    async def plan(
        self, worktree: Path, baseline: Baseline, budget: Budget
    ) -> Sequence[PlannedTask]:
        return self.tasks


def workcopy_of(ctx: AttemptContext) -> Path:
    return ctx.worktree.with_name(f"{ctx.attempt_id}-copy")


@dataclass
class ScriptedAgent:
    """Each call of the agent writes ``edits[n]`` (a file-name -> content map) into the work copy.

    ``errors[n]`` makes call ``n`` report an infrastructure error instead. ``meddle`` is run
    during the call, to stand for an agent that reaches outside its copy.
    """

    edits: Sequence[Mapping[str, str]] | None = None
    errors: Mapping[int, ErrorKind] = field(default_factory=dict)
    reason: str = "Split the long function in two."
    pause: float = 0.0
    meddle: Callable[[], object] | None = None
    reject_diff: str | None = None
    tests: tuple[DeclaredTest, ...] = ()
    criteria: tuple[Criterion, ...] = ()  # what a specifier declares
    left_out: tuple[LeftOut, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    claims: tuple[Claim, ...] = ()  # what an archaeologist or a documenter declares
    die_after_the_call: bool = False
    by_feedback: bool = False
    in_process: bool = True  # False starts a real process, as the resume tests need
    seen: list[AttemptContext] = field(default_factory=list)

    def prepare_workcopy(self, ctx: AttemptContext) -> Step:
        target = workcopy_of(ctx)

        async def payload() -> dict[str, Any]:
            digest = build_manifest(ctx.worktree, exclude={".git"}).digest()
            return {"workcopy": str(target), "expected_digest": digest}

        async def run() -> None:
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(ctx.worktree, target, ignore=shutil.ignore_patterns(".git"))

        return Step(payload, run)

    async def call(self, ctx: AttemptContext, operation_id: str, sink: EventSink) -> AgentResult:
        number = len(self.seen)
        self.seen.append(ctx)
        if self.meddle is not None:
            self.meddle()
        files: Mapping[str, str] = {f"src/{ctx.task.task_id}.py": "def f():\n    return 1\n"}
        if self.by_feedback:  # "bad" until the agent has been told why it was rejected
            verdict = "good" if ctx.previous_rejection else "bad"
            files = {f"src/{ctx.task.task_id}.py": verdict + "\n"}
        if self.edits:
            files = self.edits[min(number, len(self.edits) - 1)]
        if self.in_process and not self.pause:
            await self._write_in_process(ctx, operation_id, sink, files)
        else:
            await self._run_process(ctx, operation_id, sink, files)
        if self.die_after_the_call:
            raise RuntimeError("the process died right after the agent finished")
        if number in self.errors:
            return AgentResult(error=ErrorActivity("the service said no", self.errors[number]))
        return AgentResult(reason=self.reason)

    async def _write_in_process(
        self, ctx: AttemptContext, operation_id: str, sink: EventSink, files: Mapping[str, str]
    ) -> None:
        """What the process would have done and said, without starting one."""
        target = workcopy_of(ctx)
        log = ctx.worktree.parent / f"{operation_id}.log"
        sink.intent(
            operation_id,
            {"command": ["in-process-agent"], "cwd": str(target), "env_names": [],
             "timeout": 60, "grace": 5.0, "stdin_file": None},
        )  # fmt: skip
        for name, content in files.items():
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        log.write_text(f"[out] wrote {len(files)} file(s)\n", encoding="utf-8")
        sink.effect(
            operation_id,
            {"outcome": "exited", "exit_code": 0, "duration_seconds": 0.0, "log_path": str(log)},
        )

    async def _run_process(
        self, ctx: AttemptContext, operation_id: str, sink: EventSink, files: Mapping[str, str]
    ) -> None:
        spec = ProcessSpec(
            [sys.executable, str(AGENT_SCRIPT), json.dumps(files), str(self.pause)],
            workcopy_of(ctx),
            {"PATH": "", "SYSTEMROOT": "C:\\Windows"} if sys.platform == "win32" else {},
            60,
        )
        await ProcessRunner(sink).run(
            operation_id, spec, ctx.worktree.parent / f"{operation_id}.log"
        )

    def apply_diff(self, ctx: AttemptContext) -> Step:
        source = workcopy_of(ctx)

        async def payload() -> dict[str, Any]:
            if self.reject_diff is not None:
                raise RejectedWork(self.reject_diff)
            problems = find_violations(build_manifest(source), destination_root=ctx.worktree)
            if problems:
                raise RejectedWork("; ".join(p.detail for p in problems))
            return {
                "worktree": str(ctx.worktree),
                "expected_digest": build_manifest(source).digest(),
            }

        async def run() -> None:
            for entry in ctx.worktree.iterdir():
                if entry.name == ".git" or is_dependency(entry.name):
                    continue
                shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
            shutil.copytree(source, ctx.worktree, dirs_exist_ok=True)

        return Step(payload, run)

    def declared_tests(self, ctx: AttemptContext) -> tuple[DeclaredTest, ...]:
        return self.tests

    def declared_claims(self, ctx: AttemptContext) -> tuple[Claim, ...]:
        return self.claims

    def redact_diary(self, diary: str) -> str:
        return diary

    def declared_output(self, ctx: AttemptContext) -> LlmOutput:
        return LlmOutput(
            claims=self.claims,
            criteria=self.criteria,
            left_out=self.left_out,
            assumptions=self.assumptions,
            tests=self.tests,
        )

    async def reconcile(self, operation: OpenOperation) -> Reconciliation:
        p: Any = operation.payload
        if operation.operation == "prepare_workcopy":
            return reconcile_prepare_workcopy(Path(p["workcopy"]), p["expected_digest"])
        return reconcile_apply_diff(Path(p["worktree"]), p["expected_digest"])


class ContentMeasurer(ScriptedMeasurer):
    """Judges by what the agent wrote, so a restarted run judges the same way."""

    async def judge(
        self, ctx: AttemptContext, sinks: SinkFactory, expectations: Expectations | None = None
    ) -> PipelineOutcome:
        written = (ctx.worktree / "src" / f"{ctx.task.task_id}.py").read_text(encoding="utf-8")
        return REJECTED if written.startswith("bad") else PASSED
