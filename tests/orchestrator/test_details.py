"""The details a delivered run must get exactly right: names, ids, text, and what is passed on.

Each test pins something the broader scenarios only exercise: the operation ids a resume depends on,
the arguments each port receives, and the words of the summary.
"""

import asyncio
import dataclasses
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue

from forja.core.adapters.executor import ErrorActivity, ErrorKind
from forja.core.artifacts.schemas import TaskOutcome, TaskOutcomeKind
from forja.core.events import EventLog, LogUnusable, OpenOperation
from forja.core.gates import (
    CheckRun,
    CheckSettings,
    Effect,
    Finding,
    InconclusivePolicy,
    Mode,
    NotApplicable,
    Rejected,
)
from forja.core.idempotency import Decision, Reconciliation, reconcile_call_agent_cli
from forja.core.states import AttemptState, ExecutionState, Level, StageState, TaskState
from forja.orchestrator.budget import Meter
from forja.orchestrator.config import Budget, ExecutorRef, RunConfig, confirmed_payload
from forja.orchestrator.outcome import StopRun
from forja.orchestrator.phases import SUMMARY_NAME, Orchestrator, confirm_run
from forja.orchestrator.ports import (
    AgentResult,
    AttemptContext,
    Baseline,
    Expectations,
    PlannedTask,
    RunContext,
    SinkFactory,
)
from forja.orchestrator.summary import Facts, Premise, render_summary, summary_body
from forja.orchestrator.task_cycle import TaskCycle, _count, describe_stop
from tests.orchestrator.crash import Counting, CrashAfter, Interrupted
from tests.orchestrator.fakes import (
    PASSED,
    PASSED_WITH_A_GAP,
    REJECTED,
    ScriptedAgent,
    ScriptedMeasurer,
    ScriptedPlanner,
)
from tests.orchestrator.harness import ISOLATION, RUN, TWO_TASKS, MemoryWorld


@pytest.fixture
def world(tmp_path: Path) -> MemoryWorld:
    return MemoryWorld(tmp_path)


def intents(log: EventLog) -> list[str]:
    """The ids of the operations begun, in order."""
    lines = [json.loads(raw) for raw in log.path.read_bytes().split(b"\n") if raw]
    return [e["operation_id"] for e in lines if e["type"] == "intent"]


# -- operation ids: the idempotency table is keyed by them ----------------------------------------


def test_a_run_with_a_rejection_begins_these_operations_in_this_order(world: MemoryWorld) -> None:
    _, log = world.run(measurer=ScriptedMeasurer(verdicts=(REJECTED, PASSED)))

    assert intents(log) == [
        f"create_run_branch:{RUN}",
        "create_attempt_worktree:run",
        f"normalize_style:{RUN}",
        "create_task_branch:t1",
        "create_attempt_worktree:t1-a1",
        "prepare_workcopy:t1-a1",
        "call_agent_cli:t1-a1",
        "apply_diff:t1-a1",
        "remove_worktree:t1-a1",
        "create_attempt_worktree:t1-a2",
        "prepare_workcopy:t1-a2",
        "call_agent_cli:t1-a2",
        "apply_diff:t1-a2",
        "commit_stage:t1-a2",
        "remove_worktree:t1-a2",
        "integrate_task:t1",
        "remove_worktree:run",
    ]


def test_a_rolled_back_integration_is_an_operation_of_the_task(world: MemoryWorld) -> None:
    measurer = ScriptedMeasurer(integration_verdicts=(REJECTED,))

    _, log = world.run(measurer=measurer)

    assert intents(log)[-3:] == [
        "integrate_task:t1",
        "rollback_integration:t1",
        "remove_worktree:run",
    ]
    blocked = log.state.transition_payload(Level.TASK, "t1", TaskState.BLOCKED)
    assert blocked is not None and blocked["reason"] == "integration_gate"
    assert blocked["detail"] == "gate 8 (complexity): too complex [src/a.py::f]"


def test_the_delivery_cites_the_stored_summary(world: MemoryWorld) -> None:
    _, log = world.run()

    delivered = [
        json.loads(raw)
        for raw in log.path.read_bytes().split(b"\n")
        if raw and json.loads(raw).get("to_state") == "delivered"
    ]
    assert len(delivered) == 1 and len(delivered[0]["refs"]) == 1
    assert delivered[0]["refs"][0]["id"] == "summary"


# -- what each port is handed ----------------------------------------------------------------------


class Spy(ScriptedMeasurer):
    """Remembers what it was given."""

    def __init__(self, **options: Any) -> None:
        super().__init__(**options)
        self.baselines: list[tuple[Path, SinkFactory]] = []
        self.judged_with: list[SinkFactory] = []
        self.integrated: list[tuple[Path, SinkFactory]] = []

    async def baseline(self, worktree: Path, sinks: SinkFactory) -> Baseline:
        self.baselines.append((worktree, sinks))
        return await super().baseline(worktree, sinks)

    async def judge(
        self, ctx: AttemptContext, sinks: SinkFactory, expectations: Expectations | None = None
    ) -> Any:
        self.judged_with.append(sinks)
        return await super().judge(ctx, sinks)

    async def integration(self, worktree: Path, sinks: SinkFactory) -> Any:
        self.integrated.append((worktree, sinks))
        return await super().integration(worktree, sinks)


class SpyPlanner(ScriptedPlanner):
    def __init__(self) -> None:
        super().__init__()
        self.asked: list[tuple[Path, Baseline, Budget]] = []

    async def plan(self, worktree: Path, baseline: Baseline, budget: Budget) -> Any:
        self.asked.append((worktree, baseline, budget))
        return await super().plan(worktree, baseline, budget)


def test_the_measurer_and_the_planner_get_the_run_worktree_and_the_runs_sinks(
    world: MemoryWorld,
) -> None:
    measurer, planner = Spy(metrics={"m": 1.0}), SpyPlanner()
    budget = Budget(tasks=3)

    _, log = world.run(measurer=measurer, planner=planner, budget=budget)

    run_tree = world.workspace.run_worktree
    ((tree, sinks), (final_tree, final_sinks)) = measurer.baselines
    assert tree == final_tree == run_tree and callable(sinks) and callable(final_sinks)
    assert measurer.judged_with and all(callable(s) for s in measurer.judged_with)
    ((integrated_tree, integrated_sinks),) = measurer.integrated
    assert integrated_tree == run_tree and callable(integrated_sinks)
    ((planned_tree, baseline, planned_budget),) = planner.asked
    assert planned_tree == run_tree
    assert baseline == Baseline(True, (), {"m": 1.0})
    assert planned_budget.tasks == 3


def test_the_plan_does_not_measure_the_base_again(world: MemoryWorld) -> None:
    measurer = Spy()

    world.run(measurer=measurer)

    assert (
        len(measurer.baselines) == 2
    )  # before the plan and after the work, not before the plan twice


class Dying(ScriptedPlanner):
    async def plan(self, worktree: Path, baseline: Baseline, budget: Budget) -> Any:
        raise Interrupted("while planning")


def test_a_run_reopened_while_planning_measures_the_base_again_for_the_planner(
    world: MemoryWorld,
) -> None:
    with pytest.raises(Interrupted):
        world.run(planner=Dying())
    measurer, planner = Spy(metrics={"m": 3.0}), SpyPlanner()

    outcome, _ = world.run(measurer=measurer, planner=planner, log=world.open())

    assert outcome.state is ExecutionState.DELIVERED
    ((tree, sinks), _final) = measurer.baselines
    assert tree == world.workspace.run_worktree and callable(sinks)
    ((_, baseline, _),) = planner.asked
    assert baseline.metrics == {"m": 3.0}


# -- task information ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"), [(5, 5), (0, 0), (True, 0), ("7", 0), (None, 0), (2.5, 0)]
)
def test_only_a_real_integer_is_a_count(value: object, expected: int) -> None:
    assert _count(value) == expected


def test_the_task_info_is_what_the_planner_said(world: MemoryWorld) -> None:
    planner = ScriptedPlanner((PlannedTask("do it", ("a.py", "b.py"), 3, 99),))
    _, log = world.run(planner=planner)
    cycle = TaskCycle(
        world.context(log, measurer=ScriptedMeasurer(), agent=ScriptedAgent(), planner=planner)
    )

    info = cycle.info("t1")

    assert (info.task_id, info.stage_id, info.intent) == ("t1", "t1-s", "do it")
    assert (info.scope, info.max_files, info.max_lines) == (("a.py", "b.py"), 3, 99)


def test_a_task_without_details_has_empty_ones(world: MemoryWorld) -> None:
    _, log = world.run()
    ctx = world.context(
        log, measurer=ScriptedMeasurer(), agent=ScriptedAgent(), planner=ScriptedPlanner()
    )
    log.append_transition(Level.TASK, "bare", None, TaskState.PENDING, parent_id=RUN)

    info = TaskCycle(ctx).info("bare")

    assert (info.intent, info.scope, info.max_files, info.max_lines) == ("", (), 0, 0)


# -- the sentences the agent and the reviewer read -------------------------------------------------


def test_every_difference_git_shows_is_named_in_the_rejection(world: MemoryWorld) -> None:
    def meddle() -> None:
        world.memory.create_branch("one")
        world.memory.create_branch("two")

    _, log = world.run(agent=ScriptedAgent(meddle=meddle), budget=Budget(attempts_per_task=1))

    payload = log.state.transition_payload(Level.ATTEMPT, "t1-a1", AttemptState.REJECTED)
    assert payload is not None
    prefix = "git changed while the agent ran: "
    reason = str(payload["reason"])
    assert reason.startswith(prefix)
    first, second = reason.removeprefix(prefix).split("; ")
    assert first.startswith("ref_added refs/heads/one: None -> ")
    assert second.startswith("ref_added refs/heads/two: None -> ")
    assert second.endswith(first.rsplit(" ", 1)[1])  # both point at the same commit


def test_the_agent_is_handed_a_sink_for_its_own_call(world: MemoryWorld) -> None:
    sinks: list[Any] = []

    class Recording(ScriptedAgent):
        async def call(self, ctx: AttemptContext, operation_id: str, sink: Any) -> Any:
            sinks.append(sink)
            return await super().call(ctx, operation_id, sink)

    world.run(agent=Recording())

    (sink,) = sinks
    assert (sink._level, sink._target_id, sink._operation) == (  # noqa: SLF001
        Level.ATTEMPT,
        "t1-a1",
        "call_agent_cli",
    )


# -- the token budget between attempts -------------------------------------------------------------


def test_a_task_whose_tokens_run_out_between_attempts_is_blocked_for_them(
    world: MemoryWorld,
) -> None:
    class Slow(ScriptedMeasurer):
        async def judge(
            self, ctx: AttemptContext, sinks: SinkFactory, expectations: Expectations | None = None
        ) -> Any:
            # the first attempt spends all the tokens there are
            usage: dict[str, JsonValue] = {"type": "usage", "input": 10_000, "output": 0}
            log.append_progress(Level.ATTEMPT, ctx.attempt_id, "agent_activity", payload=usage)
            return await super().judge(ctx, sinks)

    log = world.open()
    confirm_run(log, world.config, ISOLATION)
    ctx = world.context(
        log, measurer=Slow(verdicts=(REJECTED,)), agent=ScriptedAgent(), planner=ScriptedPlanner()
    )
    ctx.meter = Meter(100, log.path, enforce=True)

    outcome = asyncio.run(Orchestrator(ctx).run())

    assert outcome.state is ExecutionState.DELIVERED
    assert log.state.state_of(Level.STAGE, "t1-s") is StageState.EXHAUSTED
    exhausted = log.state.transition_payload(Level.STAGE, "t1-s", StageState.EXHAUSTED)
    assert exhausted == {"reason": "token_budget_exhausted"}


# -- resuming a task already settled ---------------------------------------------------------------


def test_a_resume_skips_the_tasks_already_integrated_or_blocked_and_runs_the_rest(
    world: MemoryWorld,
) -> None:
    starts: list[str] = []

    def hook(point: str) -> None:
        if point == "create_task_branch:after_intent":
            starts.append(point)
            if len(starts) == 2:  # the second task is about to begin
                raise Interrupted(point)

    measurer = ScriptedMeasurer(verdicts=(REJECTED, REJECTED, PASSED))
    with pytest.raises(Interrupted):
        world.run(
            measurer=measurer,
            planner=ScriptedPlanner(TWO_TASKS),
            budget=Budget(attempts_per_task=2),
            hook=hook,
        )

    outcome, log = world.run(
        planner=ScriptedPlanner(TWO_TASKS), budget=Budget(attempts_per_task=2), log=world.open()
    )

    assert outcome.state is ExecutionState.DELIVERED
    assert log.state.state_of(Level.TASK, "t1") is TaskState.BLOCKED
    assert log.state.state_of(Level.TASK, "t2") is TaskState.INTEGRATED


def test_a_run_left_in_a_middle_state_reports_that_state(world: MemoryWorld) -> None:
    log = world.open()
    log.append_transition(Level.EXECUTION, RUN, None, ExecutionState.PREPARING)

    outcome, _ = world.run(log=log)

    assert outcome.state is ExecutionState.PREPARING
    assert outcome.message == "preparing"


# -- the summary ----------------------------------------------------------------------------------


class Moving(ScriptedMeasurer):
    """Measures one thing before the work and another after it."""

    def __init__(self, **options: Any) -> None:
        super().__init__(**options)
        self.calls = 0

    async def baseline(self, worktree: Path, sinks: SinkFactory) -> Baseline:
        self.calls += 1
        values: Mapping[str, float] = (
            {"a": 1.0, "b": 2.5} if self.calls == 1 else {"b": 2.0, "c": 3.0}
        )
        return Baseline(True, (), values)


def test_the_summary_says_everything_in_this_order(world: MemoryWorld) -> None:
    measurer = Moving(verdicts=(PASSED_WITH_A_GAP, REJECTED, REJECTED, PASSED))
    planner = ScriptedPlanner(
        (*TWO_TASKS, PlannedTask("reduce the complexity of h", ("src/c.py",), 8, 400))
    )

    world.run(measurer=measurer, planner=planner, budget=Budget(attempts_per_task=2))

    text = (world.run_dir / SUMMARY_NAME).read_text(encoding="utf-8")
    base = world.config.base_commit[:12]
    assert text == "\n".join(
        [
            f"# Resumo da execução {RUN}",
            "",
            f"Branch da entrega: `{world.workspace.run_branch}`, criada a partir de `{base}`.",
            "",
            "## Premissas assumidas",
            "",
            f"- `{Premise('t1', '', 'complexity').id}` complexity: não foi possível medir; "
            "a tarefa `t1` seguiu "
            "(tentativa `t1-a1`). Commits com `Stage-Id: t1-s`.",
            "",
            "## Tarefas bloqueadas",
            "",
            "- `t2` (reduce the complexity of g): attempts_exhausted",
            "  - tentativa `t2-a1` rejeitada: gate 8 (complexity): too complex [src/a.py::f]",
            "  - tentativa `t2-a2` rejeitada: gate 8 (complexity): too complex [src/a.py::f]",
            "",
            "## Diário de bordo",
            "",
            "- `t1` (reduce the complexity of f):",
            "  - `t1-a1` aprovada; disse: Split the long function in two.",
            "- `t2` (reduce the complexity of g):",
            "  - `t2-a1` rejeitada; disse: Split the long function in two.; "
            "motivo: gate 8 (complexity): too complex [src/a.py::f]",
            "  - `t2-a2` rejeitada; disse: Split the long function in two.; "
            "motivo: gate 8 (complexity): too complex [src/a.py::f]",
            "  - decisão da Forja: etapa esgotada `t2-s`: attempts_exhausted",
            "  - decisão da Forja: tarefa bloqueada `t2`: attempts_exhausted",
            "- `t3` (reduce the complexity of h):",
            "  - `t3-a1` aprovada; disse: Split the long function in two.",
            "",
            "## Mudanças de comportamento",
            "",
            "Não se aplica nesta versão.",
            "",
            "## Refatorações",
            "",
            "- `t1`: reduce the complexity of f (1 tentativa(s), 0 rejeitada(s))",
            "- `t3`: reduce the complexity of h (1 tentativa(s), 0 rejeitada(s))",
            "",
            "## Rastreabilidade",
            "",
            "Nenhuma tarefa integrada com critérios e testes.",
            "",
            "## Documentação",
            "",
            "Nenhuma tarefa integrada com documentação.",
            "",
            "## Métricas antes e depois",
            "",
            "| Métrica | Antes | Depois |",
            "| --- | --- | --- |",
            "| a | 1 | - |",
            "| b | 2.5 | 2 |",
            "| c | - | 3 |",
            "",
            "## Ambiente e garantias",
            "",
            "A Forja rodou no terminal do usuário e não cria sandbox: a infraestrutura e as "
            "permissões em volta são do usuário ou da empresa.",
            "",
            "- cópia do agente sem `.git`: a Forja impôs.",
            "- arquivos sensíveis ausentes da cópia do agente: a Forja impôs.",
            "- ambiente filtrado para a CLI do agente: a Forja impôs.",
            "- processos presos à Forja (não sobrevivem a ela): a Forja impôs.",
            "- uso de git em outro caminho: a Forja só detecta (git conferido antes e depois de "
            "cada chamada).",
            "- leitura fora da cópia por código executado: ficou por conta do ambiente em que a "
            "Forja rodou.",
            "- acesso à rede: ficou por conta do ambiente em que a Forja rodou.",
            "- segredos do ambiente do usuário: ficou por conta do ambiente em que a Forja rodou.",
            "",
            "O agente pôde rodar os testes do projeto (`forja agente testar`).",
            "",
            "- Quem implementa: `fake-cli` 1.0.",
            "- Quem documenta: o mesmo que implementa, sem comandos e só em documentação.",
            "",
            "## Para publicar",
            "",
            "A Forja não faz push. Se aprovar o resultado: "
            f"`git push -u <remoto> {world.workspace.run_branch}`.",
            f"Este resumo fica em `{world.run_dir / SUMMARY_NAME}` e não vai para o histórico.",
            "",
        ]
    )


def test_an_empty_run_has_nothing_to_list(world: MemoryWorld) -> None:
    world.run(planner=ScriptedPlanner(()), measurer=ScriptedMeasurer(metrics={}))

    text = (world.run_dir / SUMMARY_NAME).read_text(encoding="utf-8")
    assert "## Premissas assumidas\n\nNenhuma.\n" in text
    assert "## Tarefas bloqueadas\n\nNenhuma.\n" in text
    assert "## Diário de bordo\n\nNenhuma tentativa.\n" in text
    assert "## Refatorações\n\nNenhuma.\n" in text
    assert "## Métricas antes e depois\n\nSem medições registradas.\n" in text


def test_a_log_that_stops_in_the_middle_still_gives_the_facts_before_it(world: MemoryWorld) -> None:
    _, log = world.run(measurer=ScriptedMeasurer(metrics={"m": 4.0}))
    log.close()
    with world.run_dir.joinpath("events.jsonl").open("ab") as raw:
        raw.write(b"{not json}\n")
        raw.write(json.dumps({"type": "progress", "payload": {"kind": "metrics"}}).encode() + b"\n")

    from forja.orchestrator.summary import read_facts

    facts = read_facts(world.run_dir / "events.jsonl")

    assert facts.baseline == {"m": 4.0} and facts.final == {"m": 4.0}


def test_a_blocked_task_with_no_reason_or_intent_is_still_listed(world: MemoryWorld) -> None:
    _, log = world.run()
    log.append_transition(Level.TASK, "odd", None, TaskState.PENDING, parent_id=RUN)
    log.append_transition(Level.TASK, "odd", TaskState.PENDING, TaskState.IN_PROGRESS)
    log.append_transition(Level.TASK, "odd", TaskState.IN_PROGRESS, TaskState.BLOCKED)

    text = render_summary(log.state, Facts(), world.config, "branch", Path("x"))

    assert "- `odd` (): motivo não registrado" in text


def test_a_blocked_task_shows_the_detail_of_the_conflict_after_the_reason(
    world: MemoryWorld,
) -> None:
    _, log = world.run()
    log.append_transition(Level.TASK, "odd", None, TaskState.PENDING, parent_id=RUN)
    log.append_transition(Level.TASK, "odd", TaskState.PENDING, TaskState.IN_PROGRESS)
    log.append_transition(
        Level.TASK,
        "odd",
        TaskState.IN_PROGRESS,
        TaskState.BLOCKED,
        payload={"reason": "integration_conflict", "detail": "forja/odd conflicts with main"},
    )

    text = render_summary(log.state, Facts(), world.config, "branch", Path("x"))

    assert "- `odd` (): integration_conflict: forja/odd conflicts with main" in text


def test_the_facts_of_the_log_keep_each_premise_once_and_only_numbers(world: MemoryWorld) -> None:
    _, log = world.run()
    for check in ("c1", "c1", "c2"):
        log.append_progress(
            Level.EXECUTION, RUN, "premise", payload={"task": "t", "attempt": "a", "check": check}
        )
    log.append_progress(
        Level.EXECUTION,
        RUN,
        "metrics",
        payload={"phase": "baseline", "values": {"n": 1, "f": 1.5, "flag": True, "s": "x"}},
    )
    log.append_progress(
        Level.EXECUTION, RUN, "metrics", payload={"phase": "final", "values": {"n": 2}}
    )
    log.close()

    from forja.orchestrator.summary import Premise, read_facts

    facts = read_facts(world.run_dir / "events.jsonl")

    assert facts.premises == [Premise("t", "a", "c1"), Premise("t", "a", "c2")]
    assert facts.baseline["n"] == 1.0 and facts.baseline["f"] == 1.5
    assert "flag" not in facts.baseline and "s" not in facts.baseline
    assert facts.final["n"] == 2.0


# -- cuts that the fast suite must still cover -----------------------------------------------------


def line_count_at(root: Path, wanted: Callable[[Any], bool], **parts: Any) -> int:
    """How many lines an uninterrupted run has written when the first wanted event lands."""
    counter, found = Counting(), list[int]()
    reference = MemoryWorld(root)
    log = reference.open(write=counter)
    log.subscribe(lambda e: found.append(counter.count) if wanted(e) and not found else None)
    reference.run(log=log, **parts)
    return found[0]


def crash_then_resume(world: MemoryWorld, cut: int, **parts: Any) -> tuple[Any, EventLog]:
    with pytest.raises((Interrupted, LogUnusable)):
        world.run(log=world.open(write=CrashAfter(cut)), **parts)
    return world.run(log=world.open(), **parts)


def test_a_cut_between_the_attempt_and_the_stage_being_committed_does_not_start_another_attempt(
    tmp_path: Path,
) -> None:
    cut = line_count_at(
        tmp_path / "reference",
        lambda e: e.type == "transition" and e.level is Level.ATTEMPT and e.to_state == "committed",
    )

    outcome, log = crash_then_resume(MemoryWorld(tmp_path / "world"), cut)

    assert outcome.state is ExecutionState.DELIVERED
    assert list(log.state.children_of(Level.ATTEMPT, "t1-s")) == ["t1-a1"]
    assert log.state.state_of(Level.STAGE, "t1-s") is StageState.COMMITTED
    assert log.state.state_of(Level.TASK, "t1") is TaskState.INTEGRATED


def test_a_cut_after_the_rollback_blocks_the_task_without_judging_the_integration_again(
    tmp_path: Path,
) -> None:
    rejecting = ScriptedMeasurer(integration_verdicts=(REJECTED,))
    cut = line_count_at(
        tmp_path / "reference",
        lambda e: e.type == "effect" and e.operation == "rollback_integration",
        measurer=rejecting,
    )
    spy = Spy(integration_verdicts=(REJECTED,))

    outcome, log = crash_then_resume(MemoryWorld(tmp_path / "world"), cut, measurer=spy)

    assert outcome.state is ExecutionState.DELIVERED
    assert REJECTED.stopped_by is not None
    assert log.state.transition_payload(Level.TASK, "t1", TaskState.BLOCKED) == {
        "reason": "integration_gate",
        "detail": describe_stop(REJECTED.stopped_by),  # the same a run with no cut records
    }
    assert len(spy.integrated) == 1  # the process that died; the resumed one did not judge again


# -- who reconciles what ---------------------------------------------------------------------------


class Reconciling(ScriptedAgent):
    def __init__(self) -> None:
        super().__init__()
        self.asked: list[OpenOperation] = []

    async def reconcile(self, operation: OpenOperation) -> Reconciliation:
        self.asked.append(operation)
        return Reconciliation(Decision.DISCARD_ATTEMPT)


class ReconcilingMeasurer(ScriptedMeasurer):
    def __init__(self) -> None:
        super().__init__()
        self.asked: list[OpenOperation] = []

    async def reconcile(self, operation: OpenOperation) -> Reconciliation:
        self.asked.append(operation)
        return Reconciliation(Decision.ALREADY_DONE)


def an_open(name: str) -> OpenOperation:
    return OpenOperation(name, f"{name}:t1-a1", Level.ATTEMPT, "t1-a1", 1)


def reconcile_with(
    world: MemoryWorld, agent: ScriptedAgent, measurer: ScriptedMeasurer, name: str
) -> Any:
    ctx = world.context(world.open(), measurer=measurer, agent=agent, planner=ScriptedPlanner())
    return asyncio.run(Orchestrator(ctx)._reconcile(an_open(name)))  # noqa: SLF001


def test_an_open_agent_call_is_reconciled_by_the_table_not_asked_of_the_agent(
    world: MemoryWorld,
) -> None:
    agent, measurer = Reconciling(), ReconcilingMeasurer()

    decision = reconcile_with(world, agent, measurer, "call_agent_cli")

    assert decision == reconcile_call_agent_cli()
    assert agent.asked == [] and measurer.asked == []


@pytest.mark.parametrize("name", ["prepare_workcopy", "apply_diff"])
def test_the_agents_own_operations_are_reconciled_by_the_agent(
    world: MemoryWorld, name: str
) -> None:
    agent, measurer = Reconciling(), ReconcilingMeasurer()

    decision = reconcile_with(world, agent, measurer, name)

    assert decision == Reconciliation(Decision.DISCARD_ATTEMPT)
    assert agent.asked == [an_open(name)] and measurer.asked == []


def test_an_open_tool_run_is_reconciled_by_the_measurer(world: MemoryWorld) -> None:
    agent, measurer = Reconciling(), ReconcilingMeasurer()

    decision = reconcile_with(world, agent, measurer, "run_tool")

    assert decision == Reconciliation(Decision.ALREADY_DONE)
    assert measurer.asked == [an_open("run_tool")] and agent.asked == []


def test_an_operation_nobody_knows_stops_the_resume_naming_it(world: MemoryWorld) -> None:
    with pytest.raises(KeyError, match="no reconciler for teleport"):
        reconcile_with(world, Reconciling(), ReconcilingMeasurer(), "teleport")


# -- the rest of the phases ------------------------------------------------------------------------


def test_the_confirmation_carries_the_config_and_the_isolation(world: MemoryWorld) -> None:
    log = world.open()

    confirm_run(log, world.config, ISOLATION)

    confirmed = log.state.transition_payload(Level.EXECUTION, RUN, ExecutionState.CONFIRMED)
    assert confirmed == confirmed_payload(world.config, ISOLATION)


def test_a_baseline_that_is_not_green_reports_at_most_ten_problems(world: MemoryWorld) -> None:
    class Red(ScriptedMeasurer):
        async def baseline(self, worktree: Path, sinks: SinkFactory) -> Baseline:
            return Baseline(False, tuple(f"p{n}" for n in range(12)))

    _, log = world.run(measurer=Red())

    payload = log.state.transition_payload(Level.EXECUTION, RUN, ExecutionState.FAILED)
    assert payload is not None and payload["problems"] == [f"p{n}" for n in range(10)]


def outcome_of(task: str, kind: TaskOutcomeKind, attempts: int, rejected: int) -> TaskOutcome:
    return TaskOutcome(task_id=task, outcome=kind, attempts=attempts, rejected_attempts=rejected)


def test_the_summary_artifact_says_how_each_task_ended(world: MemoryWorld) -> None:
    measurer = Moving(verdicts=(PASSED_WITH_A_GAP, REJECTED, REJECTED, PASSED))
    planner = ScriptedPlanner(
        (*TWO_TASKS, PlannedTask("reduce the complexity of h", ("src/c.py",), 8, 400))
    )

    _, log = world.run(measurer=measurer, planner=planner, budget=Budget(attempts_per_task=2))

    assert summary_body(log.state).tasks == (
        outcome_of("t1", TaskOutcomeKind.INTEGRATED, 1, 0),
        outcome_of("t2", TaskOutcomeKind.BLOCKED, 2, 2),
        outcome_of("t3", TaskOutcomeKind.INTEGRATED, 1, 0),
    )


def test_a_task_never_started_is_cancelled_with_no_attempts(world: MemoryWorld) -> None:
    _, log = world.run(token_limit=0)

    assert summary_body(log.state).tasks == (outcome_of("t1", TaskOutcomeKind.CANCELLED, 0, 0),)


# -- small things -------------------------------------------------------------------------------


def test_a_context_that_is_not_told_otherwise_takes_the_network_to_be_up() -> None:
    defaults = {f.name: f.default for f in dataclasses.fields(RunContext)}

    assert asyncio.run(cast(Any, defaults["network_is_up"])()) is True


def test_a_stop_carries_its_reason_as_its_message() -> None:
    assert str(StopRun("the agent cannot continue")) == "the agent cannot continue"


def a_stop(result: Any) -> CheckRun:
    settings = CheckSettings(Mode.BLOCKING, InconclusivePolicy.BLOCK)
    return CheckRun("complexity", 8, settings, result, Effect.REJECT_ATTEMPT)


def test_a_stop_by_several_findings_names_them_all() -> None:
    result = Rejected(
        findings=(
            Finding(message="too long", target="a.py::f"),
            Finding(message="deep", target="b.py"),
        )
    )

    assert describe_stop(a_stop(result)) == "gate 8 (complexity): too long [a.py::f]; deep [b.py]"


def test_a_stop_by_a_check_that_could_not_apply_says_so() -> None:
    result = NotApplicable(missing_capability="lizard")

    assert describe_stop(a_stop(result)) == "gate 8 (complexity): not_applicable"


def test_what_the_agent_is_told_is_not_cut() -> None:
    result = Rejected(findings=(Finding(message="z" * 3000, target="a.py"),))

    assert describe_stop(a_stop(result)).count("z") == 3000


def test_the_detail_of_an_agent_error_is_cut_to_three_hundred_characters(
    world: MemoryWorld,
) -> None:
    class Wordy(ScriptedAgent):
        async def call(self, ctx: AttemptContext, operation_id: str, sink: Any) -> Any:
            await super().call(ctx, operation_id, sink)
            return AgentResult(error=ErrorActivity("x" * 400, ErrorKind.BLOCKING))

    _, log = world.run(agent=Wordy())

    discarded = log.state.transition_payload(Level.ATTEMPT, "t1-a1", AttemptState.DISCARDED)
    assert discarded is not None and discarded["detail"] == "x" * 300


def test_waiting_for_the_network_is_noted_under_that_name(world: MemoryWorld) -> None:
    world.network = [False, True]

    _, log = world.run(agent=ScriptedAgent(errors={0: ErrorKind.PERSISTENT}))

    kinds = [
        json.loads(raw).get("payload", {}).get("kind")
        for raw in log.path.read_bytes().split(b"\n")
        if raw
    ]
    assert "waiting_for_network" in kinds


# -- who did each role -----------------------------------------------------------------------------


def _with_executors(config: RunConfig, **changes: object) -> RunConfig:
    return config.model_copy(update=changes)


def test_the_summary_names_the_cli_and_the_model_of_each_role(world: MemoryWorld) -> None:
    _, log = world.run()
    author = ExecutorRef(name="gemini", path="p", version="1.0", model="gemini-x")
    config = _with_executors(world.config, test_author=author)

    text = render_summary(log.state, Facts(), config, "branch", Path("x"))

    assert "Quem escreve os testes: `gemini` 1.0, modelo `gemini-x`." in text
    assert "Quem implementa: " in text
    assert ", modelo `" not in text.split("Quem implementa: ")[1].splitlines()[0]


def test_the_summary_records_a_cli_that_does_not_hold_its_role_to_the_permissions(
    world: MemoryWorld,
) -> None:
    _, log = world.run()
    loose = world.config.implementer.model_copy(update={"enforces_permissions": False})
    held = world.config.implementer.model_copy(update={"enforces_permissions": True})

    said = render_summary(
        log.state, Facts(), _with_executors(world.config, implementer=loose), "b", Path("x")
    )
    silent = render_summary(
        log.state, Facts(), _with_executors(world.config, implementer=held), "b", Path("x")
    )
    unknown = render_summary(log.state, Facts(), world.config, "b", Path("x"))

    assert "Limitação: a CLI" in said
    assert "Limitação" not in silent
    assert "Limitação" not in unknown
