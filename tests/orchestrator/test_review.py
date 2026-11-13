"""The review collection says what of a run reached the main branch, and how each role fared."""

from pathlib import Path
from typing import cast

import pytest

from forja.core.artifacts.llm_output import Criterion, CriterionLevel
from forja.core.artifacts.models import Producer
from forja.core.artifacts.schemas import CriteriaBody
from forja.core.events import EventLog
from forja.core.events.state import RunState
from forja.core.states import Level
from forja.orchestrator.config import ExecutorRef, FixRequest, Objective, RunConfig, load_config
from forja.orchestrator.facts import Fact
from forja.orchestrator.ports import STAGE_LETTER, PlannedTask, role_of_stage
from forja.orchestrator.review import (
    NO_GATE,
    Landed,
    Stage,
    _by_gate,
    _by_role,
    cli_of,
    gate_of,
    render_review,
    stages_of,
)
from forja.orchestrator.summary import read_facts
from tests.orchestrator.fakes import (
    PASSED,
    PASSED_WITH_A_GAP,
    REJECTED,
    UNTESTED,
    ScriptedAgent,
    ScriptedMeasurer,
    ScriptedPlanner,
)
from tests.orchestrator.harness import RUN, MemoryWorld

TASK = PlannedTask(
    "reduce the complexity of f",
    ("src/a.py",),
    8,
    400,
    stages=(Producer.IMPLEMENTER, Producer.DOCUMENTER),
)
CODE = {"src/a.py": "def f():\n    return 1\n"}
README = {"README.md": "# a\n"}
TESTS = {"tests/test_a.py": "def test_a():\n    assert False\n"}
NOTHING: list[dict[str, str]] = [{}]
NOTHING_LANDED = Landed()
DESCRIPTION = "- page 1 shows the wrong items\n- the last page is empty"
CRITERIA = (
    Criterion(id=1, text="page 1 has the first items", level=CriterionLevel.UNIT, origin=("E1",)),
    Criterion(id=2, text="the last page has items", level=CriterionLevel.UNIT, origin=("E2",)),
)


@pytest.fixture
def world(tmp_path: Path) -> MemoryWorld:
    return MemoryWorld(tmp_path)


def refactor(world: MemoryWorld, *verdicts: object) -> EventLog:
    measurer = ScriptedMeasurer(verdicts=verdicts or (PASSED,))  # type: ignore[arg-type]
    _, log = world.run(
        measurer=measurer,
        agent=ScriptedAgent(edits=[CODE]),
        agents={Producer.DOCUMENTER: ScriptedAgent(edits=[README])},
        planner=ScriptedPlanner((TASK,)),
    )
    return log


def fix(world: MemoryWorld) -> EventLog:
    other = ExecutorRef(name="other-cli", path="/bin/other", version="2.0")
    config = world.config.model_copy(
        update={
            "objective": Objective.FIX,
            "fix": FixRequest(description=DESCRIPTION),
            "test_author": other,
        }
    )
    plan = PlannedTask(
        DESCRIPTION,
        ("src/a.py",),
        8,
        400,
        stages=(
            Producer.SPECIFIER,
            Producer.TEST_AUTHOR,
            Producer.IMPLEMENTER,
            Producer.DOCUMENTER,
        ),
        test_root="tests",
    )
    _, log = world.run(
        config=config,
        agent=ScriptedAgent(edits=[CODE]),
        agents={
            Producer.SPECIFIER: ScriptedAgent(edits=NOTHING, criteria=CRITERIA),
            Producer.TEST_AUTHOR: ScriptedAgent(edits=[TESTS]),
            Producer.DOCUMENTER: ScriptedAgent(edits=[README]),
        },
        planner=ScriptedPlanner((plan,)),
    )
    return log


def render(
    log: EventLog,
    landed: Landed = NOTHING_LANDED,
    facts: tuple[Fact, ...] = (),
) -> str:
    return render_review(
        log.state,
        load_config(log.state),
        read_facts(log.path).premises,
        landed,
        facts,
        run_branch=f"forja-run/{RUN}",
        main="origin/main",
        main_commit="abcdef0123456789",
        run_state="delivered",
    )


def test_each_stage_has_its_role_its_cli_and_what_the_attempts_did(world: MemoryWorld) -> None:
    log = refactor(world, REJECTED, PASSED)

    found = {s.id: s for s in stages_of(log.state, load_config(log.state))}

    assert list(found) == ["t1-s", "t1-d"]
    implementer = found["t1-s"]
    assert (implementer.role, implementer.cli, implementer.committed) == (
        Producer.IMPLEMENTER,
        "fake-cli",
        True,
    )
    assert (implementer.attempts, implementer.rejected) == (2, 1)
    assert implementer.stopped_by == (("8", "complexity"),)
    assert implementer.passed == ("complexity",)
    assert found["t1-d"].role is Producer.DOCUMENTER
    assert found["t1-d"].stopped_by == ()


def test_the_test_author_is_credited_to_its_own_cli_and_every_other_role_to_the_implementers(
    world: MemoryWorld,
) -> None:
    log = fix(world)

    found = {s.id: (s.role, s.cli) for s in stages_of(log.state, load_config(log.state))}

    assert found == {
        "t1-c": (Producer.SPECIFIER, "fake-cli"),
        "t1-t": (Producer.TEST_AUTHOR, "other-cli"),
        "t1-s": (Producer.IMPLEMENTER, "fake-cli"),
        "t1-d": (Producer.DOCUMENTER, "fake-cli"),
    }


def test_without_a_test_author_of_its_own_the_implementers_cli_writes_the_tests() -> None:
    config = RunConfig(
        repo="r",
        base_commit="a" * 40,
        objective=Objective.REFACTOR,
        implementer=ExecutorRef(name="one", path="/bin/one", version="1"),
        forja_version="0",
        policy_version="0",
    )

    assert cli_of(Producer.TEST_AUTHOR, config) == "one"


def test_a_rejection_names_the_gate_and_the_check_that_stopped_it() -> None:
    assert gate_of("gate 13 (traceability): element E2 has no criterion") == (
        "13",
        "traceability",
    )
    assert gate_of("git changed while the agent ran: HEAD moved") == (NO_GATE, NO_GATE)


@pytest.mark.parametrize(
    ("landed", "verdict"),
    [
        (Landed(frozenset({"t1-s", "t1-d"})), "chegou inteira"),
        (Landed(frozenset({"t1-s"})), "chegou em parte (1 de 2 etapas com commit)"),
        (Landed(), "não chegou"),
    ],
)
def test_a_task_arrived_whole_in_part_or_not_at_all(
    world: MemoryWorld, landed: Landed, verdict: str
) -> None:
    text = render(refactor(world), landed)

    assert f"- `t1` (reduce the complexity of f): {verdict}." in text


def test_each_stage_commit_is_said_to_have_arrived_or_not(world: MemoryWorld) -> None:
    text = render(refactor(world), Landed(frozenset({"t1-s"})))

    assert "  - `Stage-Id: t1-s` (implementador): chegou." in text
    assert "  - `Stage-Id: t1-d` (documentador): não chegou." in text


def test_the_specifier_commits_nothing_so_it_is_neither_arrived_nor_missing(
    world: MemoryWorld,
) -> None:
    text = render(fix(world), Landed(frozenset({"t1-t", "t1-s", "t1-d"})))

    assert "chegou inteira." in text
    assert "t1-c" not in text


def test_commits_of_a_task_without_a_stage_id_are_counted_not_guessed_at(
    world: MemoryWorld,
) -> None:
    text = render(refactor(world), Landed(frozenset(), {"t1": 2}))

    assert (
        "  - 2 commit(s) com `Task-Id: t1` e sem `Stage-Id` na principal "
        "(o merge da tarefa ou o README)." in text
    )


def test_the_style_commit_is_found_by_the_name_of_the_run(world: MemoryWorld) -> None:
    log = refactor(world)

    here = render(log, Landed(frozenset({f"normalize-{RUN}"})))
    absent = render(log)

    assert f"- Estilo (`Stage-Id: normalize-{RUN}`): chegou." in here
    assert f"- Estilo (`Stage-Id: normalize-{RUN}`): não está na principal" in absent


def test_the_table_by_role_counts_stages_arrivals_and_attempts(world: MemoryWorld) -> None:
    text = render(refactor(world, REJECTED, PASSED), Landed(frozenset({"t1-s"})))

    assert "| implementador | fake-cli | refatorar | 1 | 1 | 0 | 0 | 2 | 1 |" in text
    assert "| documentador | fake-cli | refatorar | 1 | 0 | 1 | 0 | 1 | 0 |" in text


def test_the_table_by_role_marks_the_kind_of_the_task(world: MemoryWorld) -> None:
    text = render(fix(world))

    assert "| autor de testes | other-cli | corrigir | 1 | 0 | 1 | 0 | 1 | 0 |" in text
    assert "| especificador | fake-cli | corrigir | 1 | 0 | 0 | 0 | 1 | 0 |" in text


def test_the_table_by_gate_counts_what_passed_and_what_stopped_per_role(
    world: MemoryWorld,
) -> None:
    text = render(refactor(world, REJECTED, PASSED))

    assert "| 8 | complexity | implementador | fake-cli | 1 | 1 |" in text
    assert "| 8 | complexity | documentador | fake-cli | 1 | 0 |" in text


def test_a_run_with_no_gate_at_all_says_so(tmp_path: Path) -> None:
    world = MemoryWorld(tmp_path)
    _, log = world.run(planner=ScriptedPlanner(()), measurer=ScriptedMeasurer(metrics={}))

    assert "## Por gate\n\nNenhum gate registrado.\n" in render(log)


def test_without_a_comments_file_the_report_says_none_was_pointed(world: MemoryWorld) -> None:
    text = render(refactor(world))

    assert "Nenhum arquivo de comentários e confirmações foi apontado (`--fatos`)." in text


def test_a_comment_that_confirms_a_premise_of_the_run_is_tied_to_it(world: MemoryWorld) -> None:
    log = refactor(world, PASSED_WITH_A_GAP)
    premise = read_facts(log.path).premises[0]
    facts = (
        Fact(
            id="c1",
            author="Ana",
            date="2026-10-25",
            text="vale",
            relation="confirms",
            premise=premise.id,
        ),
        Fact(id="c2", author="Ana", date="2026-10-25", text="só um comentário"),
    )

    text = render(log, Landed(), facts)

    assert (
        f"- `c1` (Ana, 2026-10-25): vale Confirma a premissa `{premise.id}` (complexity)." in text
    )
    assert "- `c2` (Ana, 2026-10-25): só um comentário" in text
    assert "Premissas ainda sem resposta" not in text
    assert "aponte este mesmo arquivo com `--fatos`" in text


def test_a_comment_that_corrects_says_corrects_and_one_about_another_run_says_so(
    world: MemoryWorld,
) -> None:
    log = refactor(world, PASSED_WITH_A_GAP)
    premise = read_facts(log.path).premises[0]
    facts = (
        Fact(
            id="c1",
            author="Ana",
            date="2026-10-25",
            text="errado",
            relation="corrects",
            premise=premise.id,
        ),
        Fact(
            id="c2",
            author="Bia",
            date="2026-10-25",
            text="x",
            relation="confirms",
            premise="pdeadbeef",
        ),
    )

    text = render(log, Landed(), facts)

    assert f"Corrige a premissa `{premise.id}`" in text
    assert "Trata da premissa `pdeadbeef`, que esta execução não teve." in text


def test_a_premise_no_comment_answers_is_listed(world: MemoryWorld) -> None:
    log = refactor(world, PASSED_WITH_A_GAP)
    premise = read_facts(log.path).premises[0]
    facts = (Fact(id="c2", author="Ana", date="2026-10-25", text="só um comentário"),)

    text = render(log, Landed(), facts)

    assert f"Premissas ainda sem resposta: `{premise.id}`." in text


def test_the_report_says_where_it_read_and_what_it_cannot_see(world: MemoryWorld) -> None:
    text = render(refactor(world))

    assert text.startswith(f"# Coleta da revisão da execução {RUN}\n")
    assert "Principal lida: `origin/main` em `abcdef012345`." in text
    assert f"branch da entrega `forja-run/{RUN}`" in text
    assert "Squash ou rebase que reescreve as mensagens apaga" in text
    assert "O especificador e o arqueólogo não commitam" in text


# -- details of the aggregates ------------------------------------------------------------------


def stage_of(role: Producer, cli: str, stopped_by: tuple[tuple[str, str], ...] = ()) -> Stage:
    return Stage("t1", f"t1-{cli}", role, cli, True, False, 1, 0, (), stopped_by)


def body(table: list[str]) -> list[str]:
    return [line[: line.index(" |", 12)] for line in table[2:]]


def test_the_table_by_role_is_ordered_by_role_and_then_by_cli() -> None:
    stages = [
        stage_of(Producer.DOCUMENTER, "z"),
        stage_of(Producer.IMPLEMENTER, "b"),
        stage_of(Producer.DOCUMENTER, "a"),
        stage_of(Producer.IMPLEMENTER, "a"),
    ]

    rows = _by_role(stages, NOTHING_LANDED, Objective.REFACTOR)

    assert [(r.split(" | ")[0], r.split(" | ")[1]) for r in rows[2:]] == [
        ("| documentador", "a"),
        ("| documentador", "z"),
        ("| implementador", "a"),
        ("| implementador", "b"),
    ]


def test_the_table_by_gate_is_ordered_by_gate_number_check_role_and_cli() -> None:
    stages = [
        stage_of(Producer.DOCUMENTER, "z", (("5", "zeta"),)),
        stage_of(Producer.IMPLEMENTER, "b", (("5", "zeta"),)),
        stage_of(Producer.IMPLEMENTER, "x", (("10", "alpha"),)),
        stage_of(Producer.IMPLEMENTER, "a", (("5", "zeta"),)),
        stage_of(Producer.IMPLEMENTER, "q", (("5", "alpha"),)),
    ]

    rows = _by_gate(stages)

    assert [tuple(r.strip("| ").split(" | ")[:4]) for r in rows[2:]] == [
        ("5", "alpha", "implementador", "q"),
        ("5", "zeta", "documentador", "z"),
        ("5", "zeta", "implementador", "a"),
        ("5", "zeta", "implementador", "b"),
        ("10", "alpha", "implementador", "x"),
    ]


def test_a_task_with_no_loose_commit_has_no_line_about_them(world: MemoryWorld) -> None:
    assert "sem `Stage-Id`" not in render(refactor(world))


class WithoutStages:
    """The state of a log whose task left no list of stages in its first transition."""

    def __init__(self, inner: RunState) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)

    def transition_payload(self, level: Level, ident: str, state: object) -> object:
        if level is Level.TASK:
            return {}
        return self.inner.transition_payload(level, ident, state)  # type: ignore[arg-type]


def test_the_role_of_a_stage_does_not_need_the_list_of_roles_of_the_task(
    world: MemoryWorld,
) -> None:
    log = refactor(world)
    config = load_config(log.state)

    found = stages_of(cast(RunState, WithoutStages(log.state)), config)

    assert [(stage.id, stage.role) for stage in found] == [
        ("t1-s", Producer.IMPLEMENTER),
        ("t1-d", Producer.DOCUMENTER),
    ]


def test_an_extra_test_author_stage_does_not_shift_the_roles_after_it(
    world: MemoryWorld,
) -> None:
    other = ExecutorRef(name="other-cli", path="/bin/other", version="2.0")
    criteria = CriteriaBody(criteria=CRITERIA)
    config = world.config.model_copy(
        update={
            "objective": Objective.FIX,
            "fix": FixRequest(description=DESCRIPTION, criteria=criteria),
            "test_author": other,
        }
    )
    plan = PlannedTask(
        DESCRIPTION,
        ("src/a.py",),
        8,
        400,
        stages=(Producer.TEST_AUTHOR, Producer.IMPLEMENTER, Producer.DOCUMENTER),
        test_root="tests",
    )
    more = {"tests/test_b.py": "def test_b():\n    assert True\n"}
    _, log = world.run(
        config=config,
        measurer=ScriptedMeasurer(verdicts=(PASSED, UNTESTED, PASSED, PASSED)),
        agent=ScriptedAgent(edits=[CODE]),
        agents={
            Producer.TEST_AUTHOR: ScriptedAgent(edits=[TESTS, more]),
            Producer.DOCUMENTER: ScriptedAgent(edits=[README]),
        },
        planner=ScriptedPlanner((plan,)),
    )

    found = [(s.id, s.role, s.cli) for s in stages_of(log.state, load_config(log.state))]

    assert found == [
        ("t1-t", Producer.TEST_AUTHOR, "other-cli"),
        ("t1-s", Producer.IMPLEMENTER, "fake-cli"),
        ("t1-t2", Producer.TEST_AUTHOR, "other-cli"),
        ("t1-d", Producer.DOCUMENTER, "fake-cli"),
    ]
    assert "`Stage-Id: t1-d` (documentador)" in render(log)
    assert "`Stage-Id: t1-t2` (autor de testes)" in render(log)


@pytest.mark.parametrize(
    ("stage", "role"),
    [
        ("t1-s", Producer.IMPLEMENTER),
        ("t1-t", Producer.TEST_AUTHOR),
        ("t1-d", Producer.DOCUMENTER),
        ("t1-c", Producer.SPECIFIER),
        ("t1-f", Producer.ARCHAEOLOGIST),
        ("t1-t2", Producer.TEST_AUTHOR),
        ("t1-t13", Producer.TEST_AUTHOR),
        ("t1-s2", None),
        ("t1-d2", None),
        ("t1-tx", None),
        ("t1-t²", None),
        ("t1-x", None),
        ("t1-", None),
        ("t1", None),
        ("t1s", None),
        ("t2-s", None),
        ("t11-s", None),
    ],
)
def test_the_id_of_a_stage_says_which_role_did_it(stage: str, role: Producer | None) -> None:
    assert role_of_stage("t1", stage) is role


def test_every_role_that_has_a_stage_has_a_letter_of_its_own() -> None:
    assert set(STAGE_LETTER) == set(Producer) - {Producer.FORJA}
    assert len(set(STAGE_LETTER.values())) == len(STAGE_LETTER)
