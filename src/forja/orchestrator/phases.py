"""The phases of an execution, from the confirmed run to the delivered branch.

``Orchestrator.run`` is called for a new run and again for a resumed one: the state in the log
says which phase is next, and the journal skips what each phase already did. A run that must stop
(a CLI that cannot continue, the network gone) is left ``paused`` with everything saved.
"""

import asyncio
import contextlib
import traceback
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from forja.core.artifacts.models import ArtifactType, Producer
from forja.core.atomic import atomic_write_bytes
from forja.core.confirmation import Confirmation
from forja.core.events import EventLog
from forja.core.events.state import OpenOperation
from forja.core.idempotency import OperationName, reconcile_call_agent_cli
from forja.core.resume import resume
from forja.core.states import (
    EXECUTION_MACHINE,
    AttemptState,
    ExecutionState,
    Level,
    TaskState,
)
from forja.orchestrator.config import RunConfig, confirmed_payload
from forja.orchestrator.outcome import ExitCode, Outcome, StopRun, exit_code_for
from forja.orchestrator.ports import Baseline, RunContext
from forja.orchestrator.schedule import Candidate, startable
from forja.orchestrator.summary import (
    METRICS,
    read_facts,
    render_summary,
    summary_body,
)
from forja.orchestrator.task_cycle import TaskCycle
from forja.orchestrator.workspace_git import HANDLED_OPERATIONS

AGENT_OPERATIONS = frozenset({OperationName.PREPARE_WORKCOPY, OperationName.APPLY_DIFF})
SUMMARY_NAME = "resumo.md"
FAILURE_NAME = "falha.log"  # the trace of an error nobody expected, beside the summary
FAILURE_REASON_LIMIT = 500  # characters of the error kept in the log
METRIC_LIMIT = 40
BASELINE_NOT_GREEN = "baseline_not_green"


def _baseline_message(problems: object) -> str:
    """The run that ended before any task because the base is not green, said to the person."""
    found = [str(p) for p in problems] if isinstance(problems, list) else []
    what = f" ({'; '.join(found)})" if found else ""
    return (
        f"A base já não está verde{what}: a Forja não parte de testes ou checagens que falham. "
        "Corrija-os, faça o commit e rode de novo."
    )


_ENDED = (ExecutionState.DELIVERED, ExecutionState.DISCARDED, ExecutionState.FAILED)


def confirm_run(log: EventLog, config: RunConfig, confirmation: Confirmation) -> None:
    """Create the run and record what the user confirmed; the point of no return."""
    run_id = log.state.run_id
    log.append_transition(Level.EXECUTION, run_id, None, ExecutionState.PREPARING)
    log.append_transition(
        Level.EXECUTION,
        run_id,
        ExecutionState.PREPARING,
        ExecutionState.CONFIRMED,
        payload=confirmed_payload(config, confirmation),
    )


class Orchestrator:
    def __init__(self, ctx: RunContext) -> None:
        self._ctx = ctx
        self._journal = ctx.journal
        self._log = ctx.journal.log
        self._run_id = ctx.journal.run_id
        self._baseline: Baseline | None = None

    @property
    def _state(self) -> ExecutionState:
        state = self._log.state.state_of(Level.EXECUTION, self._run_id)
        assert isinstance(state, ExecutionState)
        return state

    async def run(self) -> Outcome:
        """Take the run as far as it can go; the outcome says where it was left."""
        journal = self._journal
        journal.session_started()
        try:
            await self._settle_leftovers()
            self._leave_pause()
            await self._phases()
            outcome = self._outcome()
        except StopRun as stop:
            outcome = self._pause(stop.reason)
        except asyncio.CancelledError:
            self._interrupted()
            journal.session_ended("interrupted")
            raise
        except Exception as error:
            self._record_failure(error)
            raise
        journal.session_ended(outcome.state.value)
        return outcome

    def _record_failure(self, error: Exception) -> None:
        """An error nobody expected: the log says the run failed and why; a file keeps the trace.

        The error still leaves the orchestrator. Recording it is best effort: when the log itself
        is what broke, there is nothing to write to, and the resume settles the run as after a
        death of the process.
        """
        reason = f"{type(error).__name__}: {error}"[:FAILURE_REASON_LIMIT]
        with contextlib.suppress(Exception):
            trace = "".join(traceback.format_exception(error))
            atomic_write_bytes(self._ctx.run_dir / FAILURE_NAME, trace.encode("utf-8"))
        with contextlib.suppress(Exception):
            state = self._state
            # a run already paused or failed keeps the state it will be resumed from
            if ExecutionState.FAILED in EXECUTION_MACHINE.table[state] and state not in (
                ExecutionState.PAUSED,
                ExecutionState.FAILED,
            ):
                self._log.append_transition(
                    Level.EXECUTION,
                    self._run_id,
                    state,
                    ExecutionState.FAILED,
                    payload={"reason": reason},
                )
            self._journal.session_ended("failed")

    # -- starting up ----------------------------------------------------------------------------

    async def _settle_leftovers(self) -> None:
        await self._ctx.workspace.recover()
        await resume(self._log, self._reconcile)

    async def _reconcile(self, operation: OpenOperation) -> Any:
        ctx = self._ctx
        name = operation.operation
        if name in HANDLED_OPERATIONS:
            return await ctx.workspace.reconcile(operation)
        if name == OperationName.CALL_AGENT_CLI:
            return reconcile_call_agent_cli()
        if name in AGENT_OPERATIONS:
            return await ctx.agent.reconcile(operation)
        if name == OperationName.RUN_TOOL:
            return await ctx.measurer.reconcile(operation)
        raise KeyError(f"no reconciler for {name}")

    def _interrupted(self) -> None:
        """The person stopped the run: save where it was and leave it ready to resume.

        An attempt whose agent was cut short is dropped without counting against the task, as
        nothing about the work was judged. Everything else the log already settles on resume.
        """
        state = self._log.state
        for task in state.children_of(Level.TASK, self._run_id):
            for stage in state.children_of(Level.STAGE, task):
                for attempt in state.children_of(Level.ATTEMPT, stage):
                    if state.state_of(Level.ATTEMPT, attempt) is AttemptState.AGENT_RUNNING:
                        self._log.append_transition(
                            Level.ATTEMPT,
                            attempt,
                            AttemptState.AGENT_RUNNING,
                            AttemptState.DISCARDED,
                            payload={"reason": "interrupted", "infra_failure": False},
                        )
        if not EXECUTION_MACHINE.is_terminal(self._state):
            self._pause("interrupted")

    def _leave_pause(self) -> None:
        state = self._state
        if state in (ExecutionState.PAUSED, ExecutionState.FAILED):
            previous = self._log.state.previous_state(Level.EXECUTION, self._run_id)
            if previous is not None:
                self._log.append_transition(Level.EXECUTION, self._run_id, state, previous)

    def _pause(self, reason: str) -> Outcome:
        state = self._state
        if state is not ExecutionState.PAUSED:
            self._log.append_transition(
                Level.EXECUTION,
                self._run_id,
                state,
                ExecutionState.PAUSED,
                payload={"reason": reason},
            )
        return Outcome(ExecutionState.PAUSED, ExitCode.PAUSED, reason)

    def _outcome(self) -> Outcome:
        state = self._state
        return Outcome(state, exit_code_for(state), self._message(state))

    def _message(self, state: ExecutionState) -> str:
        if state is ExecutionState.DELIVERED:
            return (
                f"entregue em {self._ctx.workspace.run_branch}; resumo em "
                f"{self._ctx.run_dir / SUMMARY_NAME}"
            )
        payload = self._log.state.transition_payload(Level.EXECUTION, self._run_id, state) or {}
        if payload.get("reason") == BASELINE_NOT_GREEN:
            return _baseline_message(payload.get("problems"))
        return str(payload.get("reason", state.value))

    # -- the phases -----------------------------------------------------------------------------

    async def _phases(self) -> None:
        if self._state is ExecutionState.CONFIRMED:
            await self._prepare()
        if self._state is ExecutionState.MEASURING_BASELINE:
            await self._measure()
        if self._state is ExecutionState.PLANNING:
            await self._plan()
        if self._state is ExecutionState.EXECUTING:
            await self._execute()
        if self._state is ExecutionState.CLOSING:
            await self._close()

    def _go(self, to: ExecutionState, payload: dict[str, JsonValue] | None = None) -> None:
        self._log.append_transition(Level.EXECUTION, self._run_id, self._state, to, payload=payload)

    async def _prepare(self) -> None:
        ctx, journal, run_id = self._ctx, self._journal, self._run_id
        ws = ctx.workspace
        await journal.operate(
            OperationName.CREATE_RUN_BRANCH,
            Level.EXECUTION,
            run_id,
            run_id,
            ws.create_run_branch(ctx.config.base_commit),
        )
        await journal.operate(
            OperationName.CREATE_ATTEMPT_WORKTREE,
            Level.EXECUTION,
            run_id,
            "run",
            ws.create_run_worktree(),
        )
        await journal.operate(
            OperationName.NORMALIZE_STYLE,
            Level.EXECUTION,
            run_id,
            run_id,
            ws.normalize_style(ctx.measurer.format_worktree),
        )
        self._go(ExecutionState.MEASURING_BASELINE)

    async def _measure(self) -> None:
        ctx = self._ctx
        baseline = await ctx.measurer.baseline(ctx.workspace.run_worktree, ctx.sinks)
        self._baseline = baseline
        self._record_metrics("baseline", baseline)
        if not baseline.ok:
            problems: list[JsonValue] = [*baseline.problems[:10]]
            self._go(ExecutionState.FAILED, {"reason": BASELINE_NOT_GREEN, "problems": problems})
            return
        self._go(ExecutionState.PLANNING)

    def _record_metrics(self, phase: str, baseline: Baseline) -> None:
        values = dict(sorted(baseline.metrics.items())[:METRIC_LIMIT])
        self._journal.note(METRICS, {"phase": phase, "values": values})  # type: ignore[dict-item]

    async def _plan(self) -> None:
        ctx, journal = self._ctx, self._journal
        baseline = self._baseline or await ctx.measurer.baseline(
            ctx.workspace.run_worktree, ctx.sinks
        )
        planned = await ctx.planner.plan(ctx.workspace.run_worktree, baseline, ctx.config.budget)
        for number, task in enumerate(planned[: ctx.config.budget.tasks], start=1):
            payload: dict[str, JsonValue] = {
                "intent": task.intent,
                "scope": list(task.scope),
                "max_files": task.max_files,
                "max_lines": task.max_lines,
            }
            if task.stages != (Producer.IMPLEMENTER,):  # a log with one stage reads as it did
                payload["stages"] = [role.value for role in task.stages]
                payload["test_root"] = task.test_root
            journal.enter(
                Level.TASK, f"t{number}", TaskState.PENDING, parent=self._run_id, payload=payload
            )
        self._go(ExecutionState.EXECUTING)

    async def _execute(self) -> None:
        cycle = TaskCycle(self._ctx)
        if self._ctx.config.parallel_tasks > 1:
            await self._execute_together(cycle, self._ctx.config.parallel_tasks)
            self._go(ExecutionState.CLOSING)
            return
        state = self._log.state
        for task in state.children_of(Level.TASK, self._run_id):
            if state.state_of(Level.TASK, task) in (TaskState.INTEGRATED, TaskState.BLOCKED):
                continue
            if self._ctx.meter.exhausted():
                break
            await cycle.run(task)
        self._go(ExecutionState.CLOSING)

    def _candidates(self) -> list[Candidate]:
        state = self._log.state
        found = []
        for task in state.children_of(Level.TASK, self._run_id):
            payload = state.transition_payload(Level.TASK, task, TaskState.PENDING) or {}
            scope, parents = payload.get("scope"), payload.get("depends_on")
            found.append(
                Candidate(
                    task,
                    tuple(str(p) for p in scope) if isinstance(scope, list) else (),
                    tuple(str(p) for p in parents) if isinstance(parents, list) else (),
                )
            )
        return found

    async def _execute_together(self, cycle: TaskCycle, limit: int) -> None:
        """Run the tasks the schedule allows at once. A task that stops the run (``StopRun``, or
        any error) takes the others down with it; they start again from the log on a resume."""
        state = self._log.state
        running: dict[str, asyncio.Task[None]] = {}
        try:
            while True:
                candidates = self._candidates()
                done = {c.task_id: state.state_of(Level.TASK, c.task_id) for c in candidates}
                integrated = [t for t, s in done.items() if s is TaskState.INTEGRATED]
                closed = [
                    t for t, s in done.items() if s in (TaskState.INTEGRATED, TaskState.BLOCKED)
                ]
                if not self._ctx.meter.exhausted():
                    for task in startable(candidates, list(running), integrated, closed, limit):
                        running[task] = asyncio.create_task(cycle.run(task))
                if not running:
                    return
                finished, _ = await asyncio.wait(
                    running.values(), return_when=asyncio.FIRST_COMPLETED
                )
                for task_id in [t for t, job in running.items() if job in finished]:
                    running.pop(task_id).result()
        finally:
            for job in running.values():
                job.cancel()
            await asyncio.gather(*running.values(), return_exceptions=True)

    async def _close(self) -> None:
        ctx, journal = self._ctx, self._journal
        final = await ctx.measurer.baseline(ctx.workspace.run_worktree, ctx.sinks)
        self._record_metrics("final", final)
        store = ctx.store
        header = store.put(
            artifact_type=ArtifactType.EXECUTION_SUMMARY,
            producer=Producer.FORJA,
            body=summary_body(self._log.state),
            artifact_id="summary",
        )
        path = self._write_summary(ctx.run_dir / SUMMARY_NAME)
        await journal.operate(
            OperationName.REMOVE_WORKTREE,
            Level.EXECUTION,
            self._run_id,
            "run",
            ctx.workspace.remove_worktree("run"),
        )
        journal.enter(
            Level.EXECUTION,
            self._run_id,
            ExecutionState.DELIVERED,
            payload={"summary": str(path)},
            refs=(store.ref(header),),
        )

    def _write_summary(self, path: Path) -> Path:
        ctx = self._ctx
        facts = read_facts(self._log.path)
        text = render_summary(
            self._log.state, facts, ctx.config, ctx.workspace.run_branch, path, ctx.notices
        )
        atomic_write_bytes(path, text.encode("utf-8"))
        return path
