"""The collection of the review: what of a run reached the main branch and how each role fared.

Two things are read and nothing is written to the repository: the log of the run and the history
git shows of the main branch after the person fetched. A commit is matched by the trailers the
Forja put on it (``Stage-Id``, ``Task-Id``), so a squash or a rebase that rewrote the messages
hides it. The text is Portuguese, to a file in the data directory, like the summary.
"""

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from forja.checks.specs import default_registry
from forja.core.artifacts.models import Producer
from forja.core.events.state import RunState
from forja.core.idempotency.reconcilers import normalize_stage_id
from forja.core.states import AttemptState, Level, StageState, TaskState
from forja.orchestrator.config import Objective, RunConfig
from forja.orchestrator.facts import Fact
from forja.orchestrator.ports import NO_COMMIT, role_of_stage
from forja.orchestrator.summary import Premise

# how a rejected attempt says which check stopped it (``describe_stop`` in the task cycle)
_STOPPED_BY = re.compile(r"^gate (\d+) \(([^)]+)\):")
NO_GATE = "(fora dos gates)"
ROLE_TEXT = {
    Producer.ARCHAEOLOGIST: "arqueólogo",
    Producer.SPECIFIER: "especificador",
    Producer.TEST_AUTHOR: "autor de testes",
    Producer.IMPLEMENTER: "implementador",
    Producer.DOCUMENTER: "documentador",
}
OBJECTIVE_TEXT = {
    Objective.REFACTOR: "refatorar",
    Objective.FIX: "corrigir",
    Objective.INVESTIGATE: "investigar",
    Objective.CREATE: "criar",
}
INTENT_LIMIT = 80


@dataclass(frozen=True)
class Landed:
    """What the history of the main branch shows of the run's commits.

    ``stages`` are the ``Stage-Id`` trailers found; ``loose`` counts, per ``Task-Id``, the commits
    that carry that trailer and no ``Stage-Id`` (the merge of the task and the README commit).
    """

    stages: frozenset[str] = frozenset()
    loose: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Stage:
    """One stage of the run as the log tells it."""

    task: str
    id: str
    role: Producer
    cli: str
    committed: bool
    exhausted: bool
    attempts: int
    rejected: int
    passed: tuple[str, ...]  # the checks the approved attempt passed
    stopped_by: tuple[tuple[str, str], ...]  # (gate, check) of each rejected attempt


def cli_of(role: Producer, config: RunConfig) -> str:
    """The CLI that did the role: the test author's own if chosen, else the implementer's."""
    if role is Producer.TEST_AUTHOR and config.test_author is not None:
        return config.test_author.name
    return config.implementer.name


def stages_of(state: RunState, config: RunConfig) -> list[Stage]:
    found = []
    for task in state.children_of(Level.TASK, state.run_id):
        for stage in state.children_of(Level.STAGE, task):
            # by the id, not the place in the plan: an extra test author stage comes in between
            role = role_of_stage(task, stage)
            if role is not None:
                found.append(_stage(state, config, task, stage, role))
    return found


def _stage(state: RunState, config: RunConfig, task: str, stage: str, role: Producer) -> Stage:
    counted, rejected, stopped_by = 0, 0, []
    passed: tuple[str, ...] = ()
    for attempt in state.children_of(Level.ATTEMPT, stage):
        if state.state_of(Level.ATTEMPT, attempt) is AttemptState.DISCARDED:
            continue  # an infrastructure failure: not the agent's attempt
        counted += 1
        refusal = state.transition_payload(Level.ATTEMPT, attempt, AttemptState.REJECTED)
        if refusal is not None:
            rejected += 1
            stopped_by.append(gate_of(str(refusal.get("reason", ""))))
        approval = state.transition_payload(Level.ATTEMPT, attempt, AttemptState.APPROVED)
        gates = approval.get("gates_passed") if approval else None
        if isinstance(gates, list):
            passed = tuple(str(check) for check in gates)
    current = state.state_of(Level.STAGE, stage)
    return Stage(
        task,
        stage,
        role,
        cli_of(role, config),
        current is StageState.COMMITTED,
        current is StageState.EXHAUSTED,
        counted,
        rejected,
        passed,
        tuple(stopped_by),
    )


def gate_of(reason: str) -> tuple[str, str]:
    """``(gate, check)`` a rejected attempt names in its reason, ``NO_GATE`` twice if none."""
    found = _STOPPED_BY.match(reason)
    return (found.group(1), found.group(2)) if found else (NO_GATE, NO_GATE)


def _gate_number(check: str) -> str:
    registry = default_registry()
    return str(registry.get(check).gate) if check in registry else "?"


# -- what the main branch shows ---------------------------------------------------------------


def _arrived(stage: Stage, landed: Landed) -> bool:
    return stage.id in landed.stages


def _with_commit(stages: Sequence[Stage]) -> list[Stage]:
    return [s for s in stages if s.committed and s.role not in NO_COMMIT]


def _task_lines(state: RunState, stages: Sequence[Stage], landed: Landed, run_id: str) -> list[str]:
    lines = []
    for task in state.children_of(Level.TASK, state.run_id):
        payload = state.transition_payload(Level.TASK, task, TaskState.PENDING) or {}
        intent = " ".join(str(payload.get("intent", "")).split())[:INTENT_LIMIT]
        own = _with_commit([s for s in stages if s.task == task])
        reached = [s for s in own if _arrived(s, landed)]
        if not own:
            verdict = "nenhuma etapa com commit; nada a chegar"
        elif len(reached) == len(own):
            verdict = "chegou inteira"
        elif reached:
            verdict = f"chegou em parte ({len(reached)} de {len(own)} etapas com commit)"
        else:
            verdict = "não chegou"
        lines.append(f"- `{task}` ({intent}): {verdict}.")
        for stage in own:
            where = "chegou" if _arrived(stage, landed) else "não chegou"
            lines.append(f"  - `Stage-Id: {stage.id}` ({ROLE_TEXT[stage.role]}): {where}.")
        extra = landed.loose.get(task, 0)
        if extra:
            lines.append(
                f"  - {extra} commit(s) com `Task-Id: {task}` e sem `Stage-Id` na principal "
                "(o merge da tarefa ou o README)."
            )
    style = normalize_stage_id(run_id)
    lines.append(
        f"- Estilo (`Stage-Id: {style}`): "
        + (
            "chegou."
            if style in landed.stages
            else "não está na principal (só existe se o formatador mudou algo)."
        )
    )
    return lines


# -- aggregates from the log ------------------------------------------------------------------


def _by_role(stages: Sequence[Stage], landed: Landed, objective: Objective) -> list[str]:
    rows: dict[tuple[Producer, str], Counter[str]] = {}
    for stage in stages:
        row = rows.setdefault((stage.role, stage.cli), Counter())
        row["stages"] += stage.committed
        row["landed"] += stage.committed and stage.role not in NO_COMMIT and _arrived(stage, landed)
        row["not_landed"] += (
            stage.committed and stage.role not in NO_COMMIT and not _arrived(stage, landed)
        )
        row["exhausted"] += stage.exhausted
        row["attempts"] += stage.attempts
        row["rejected"] += stage.rejected
    table = [
        "| Papel | CLI | Tipo de tarefa | Etapas aprovadas | Chegaram | Não chegaram "
        "| Esgotadas | Tentativas | Rejeitadas |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for (role, cli), n in sorted(rows.items(), key=lambda item: (item[0][0].value, item[0][1])):
        table.append(
            f"| {ROLE_TEXT[role]} | {cli} | {OBJECTIVE_TEXT[objective]} | {n['stages']} "
            f"| {n['landed']} | {n['not_landed']} | {n['exhausted']} | {n['attempts']} "
            f"| {n['rejected']} |"
        )
    return table


def _by_gate(stages: Sequence[Stage]) -> list[str]:
    rows: dict[tuple[str, str, Producer, str], Counter[str]] = {}
    for stage in stages:
        for check in stage.passed:
            rows.setdefault((_gate_number(check), check, stage.role, stage.cli), Counter())[
                "passed"
            ] += 1
        for gate, check in stage.stopped_by:
            rows.setdefault((gate, check, stage.role, stage.cli), Counter())["stopped"] += 1
    if not rows:
        return ["Nenhum gate registrado."]
    table = [
        "| Gate | Verificação | Papel | CLI | Passou | Reprovou |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for (gate, check, role, cli), n in sorted(
        rows.items(),
        key=lambda item: (item[0][0].rjust(3), item[0][1], item[0][2].value, item[0][3]),
    ):
        table.append(
            f"| {gate} | {check} | {ROLE_TEXT[role]} | {cli} | {n['passed']} | {n['stopped']} |"
        )
    return table


# -- what the person wrote down ---------------------------------------------------------------


def _comments(facts: Sequence[Fact], premises: Sequence[Premise]) -> list[str]:
    if not facts:
        return ["Nenhum arquivo de comentários e confirmações foi apontado (`--fatos`)."]
    known = {p.id: p for p in premises}
    lines = []
    for fact in facts:
        said = f"- `{fact.id}` ({fact.author}, {fact.date}): {fact.text}"
        if fact.premise in known:
            premise = known[fact.premise]
            verb = "Confirma" if fact.relation == "confirms" else "Corrige"
            said += f" {verb} a premissa `{premise.id}` ({premise.check or premise.statement})."
        elif fact.premise:
            said += f" Trata da premissa `{fact.premise}`, que esta execução não teve."
        lines.append(said)
    settled = {f.premise for f in facts}
    open_ = [p.id for p in premises if p.id not in settled]
    if open_:
        lines += ["", "Premissas ainda sem resposta: " + ", ".join(f"`{p}`" for p in open_) + "."]
    lines += [
        "",
        "Para que entrem como evidência nos pacotes das próximas execuções, aponte este mesmo "
        "arquivo com `--fatos` (ou `fatos` no `execucao.toml`).",
    ]
    return lines


LIMITS = (
    "Só vale o que o git ainda mostra. Squash ou rebase que reescreve as mensagens apaga "
    "`Stage-Id` e `Task-Id`, e a etapa aparece como não chegou.",
    "Os ids de etapa (`t1-s`) se repetem entre execuções; a leitura vai do commit de partida da "
    "execução até a principal e não separa execuções que partiram do mesmo commit.",
    "O especificador e o arqueólogo não commitam; por isso não entram em chegou ou não chegou.",
    "Comentários do revisor entram só pelo arquivo apontado; nenhuma plataforma é consultada.",
)


def render_review(
    state: RunState,
    config: RunConfig,
    premises: Sequence[Premise],
    landed: Landed,
    facts: Sequence[Fact],
    *,
    run_branch: str,
    main: str,
    main_commit: str,
    run_state: str,
) -> str:
    stages = stages_of(state, config)
    lines = [
        f"# Coleta da revisão da execução {state.run_id}",
        "",
        f"Execução {run_state}, criada a partir de `{config.base_commit[:12]}`; branch da entrega "
        f"`{run_branch}`. Principal lida: `{main}` em `{main_commit[:12]}`.",
        "",
        "## O que chegou à principal",
        "",
        *_task_lines(state, stages, landed, state.run_id),
        "",
        "## Por papel, CLI e tipo de tarefa",
        "",
        *_by_role(stages, landed, config.objective),
        "",
        "## Por gate",
        "",
        *_by_gate(stages),
        "",
        "## Comentários e confirmações",
        "",
        *_comments(facts, premises),
        "",
        "## Limites",
        "",
        *(f"- {limit}" for limit in LIMITS),
        "",
    ]
    return "\n".join(lines)
