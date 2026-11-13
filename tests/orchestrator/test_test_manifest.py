"""The test author's answer becomes the manifest of the tests: stored, judged and traced."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from forja.core.artifacts.llm_output import (
    Criterion,
    CriterionLevel,
    DataGenerator,
    DeclaredTest,
    LlmOutput,
)
from forja.core.artifacts.models import ArtifactType, Producer
from forja.core.artifacts.schemas import AuthoredTestsBody
from forja.core.artifacts.store import ArtifactStore
from forja.core.states import ExecutionState
from forja.orchestrator.agent_runner import _check_manifest
from forja.orchestrator.config import FixRequest, Objective
from forja.orchestrator.criteria import manifest_of
from forja.orchestrator.ports import Expectations, PlannedTask, RejectedWork
from forja.orchestrator.summary import _traceability, read_facts
from forja.orchestrator.task_cycle import TRACE_BYTES, _within_event_limit
from tests.orchestrator.fakes import ScriptedAgent, ScriptedMeasurer, ScriptedPlanner
from tests.orchestrator.harness import MemoryWorld

FIX = FixRequest.model_validate(
    {
        "description": "page 1 is wrong",
        "criteria": {
            "criteria": [
                {"id": 1, "text": "page 1 has the first items", "level": "unit", "origin": "E1"},
                {
                    "id": 2,
                    "text": "the last page has items",
                    "level": "unit",
                    "origin": ["E1", "E2"],
                },
            ]
        },
    }
)
TASK = PlannedTask(
    "page 1 is wrong",
    ("src/a.py",),
    8,
    400,
    stages=(Producer.TEST_AUTHOR, Producer.IMPLEMENTER, Producer.DOCUMENTER),
    test_root="tests",
)
TESTS = {"tests/test_a.py": "def test_a():\n    assert False\n"}
DECLARED = (
    DeclaredTest(criterion_id=1, test_id="tests/test_a.py::test_a", path="tests/test_a.py"),
    DeclaredTest(criterion_id=2, test_id="tests/test_a.py::test_b", path="tests/test_a.py"),
)
GENERATOR = DataGenerator(name="pages", seed=7, path="tests/data.py")


@pytest.fixture
def world(tmp_path: Path) -> MemoryWorld:
    found = MemoryWorld(tmp_path)
    found.config = found.config.model_copy(update={"objective": Objective.FIX, "fix": FIX})
    return found


def run(world: MemoryWorld, measurer: ScriptedMeasurer) -> tuple[object, object]:
    return world.run(
        measurer=measurer,
        agent=ScriptedAgent(edits=[{"src/a.py": "def f():\n    return 1\n"}]),
        agents={
            Producer.TEST_AUTHOR: ScriptedAgent(edits=[TESTS], tests=DECLARED),
            Producer.DOCUMENTER: ScriptedAgent(edits=[{"README.md": "# a\n"}]),
        },
        planner=ScriptedPlanner((TASK,)),
    )


def test_the_manifest_has_each_test_with_its_criterion_and_the_generators() -> None:
    manifest = manifest_of(LlmOutput(tests=DECLARED, generators=(GENERATOR,)))

    assert [(t.criterion_id, t.test_id) for t in manifest.tests] == [
        (1, "tests/test_a.py::test_a"),
        (2, "tests/test_a.py::test_b"),
    ]
    assert manifest.generators == (GENERATOR,)


def test_an_answer_with_no_test_or_a_test_twice_makes_no_manifest() -> None:
    with pytest.raises(ValidationError):
        manifest_of(LlmOutput())
    with pytest.raises(ValidationError, match="test ids must be unique"):
        manifest_of(LlmOutput(tests=(DECLARED[0], DECLARED[0])))
    with pytest.raises(ValidationError, match="generator names must be unique"):
        manifest_of(LlmOutput(tests=DECLARED, generators=(GENERATOR, GENERATOR)))


def test_such_an_answer_is_refused_with_the_reason() -> None:
    with pytest.raises(RejectedWork, match="the declared tests are not valid"):
        _check_manifest(LlmOutput())
    _check_manifest(LlmOutput(tests=DECLARED))


def test_the_manifest_is_stored_as_an_artifact_of_the_run(world: MemoryWorld) -> None:
    outcome, _ = run(world, ScriptedMeasurer())

    stored = ArtifactStore(world.run_dir / "store").get("test-manifest-t1")

    assert outcome.state is ExecutionState.DELIVERED  # type: ignore[attr-defined]
    assert stored.header.type is ArtifactType.TEST_MANIFEST
    assert stored.header.producer is Producer.TEST_AUTHOR
    assert isinstance(stored.body, AuthoredTestsBody)
    assert [t.test_id for t in stored.body.tests] == [t.test_id for t in DECLARED]


def test_what_the_gate_is_told_the_author_declared_is_what_the_manifest_holds(
    world: MemoryWorld,
) -> None:
    measurer = ScriptedMeasurer()

    run(world, measurer)

    expected = measurer.expected[0]
    assert isinstance(expected, Expectations)
    assert expected.criteria == frozenset({1, 2})
    assert expected.declared == DECLARED


def test_the_summary_traces_each_criterion_from_the_input_to_its_tests(
    world: MemoryWorld,
) -> None:
    _, log = run(world, ScriptedMeasurer())

    lines = _traceability(log.state, read_facts(log.path))  # type: ignore[attr-defined]

    assert "- `t1`:" in lines
    assert (
        "  - critério 1 (origem E1): page 1 has the first items → `tests/test_a.py::test_a`"
        in lines
    )
    assert (
        "  - critério 2 (origem E1, E2): the last page has items → `tests/test_a.py::test_b`"
        in lines
    )
    assert any(line.startswith("Não conferido:") or "Não conferido" in line for line in lines)


def test_many_long_criteria_are_traced_in_the_summary_without_breaking_the_event_limit(
    world: MemoryWorld,
) -> None:
    count = 30
    criteria = tuple(
        Criterion(
            id=n, text=f"criterion {n} " + "word " * 30, level=CriterionLevel.UNIT, origin=("E1",)
        )
        for n in range(1, count + 1)
    )
    declared = tuple(
        DeclaredTest(criterion_id=n, test_id=f"tests/test_a.py::test_{n}", path="tests/test_a.py")
        for n in range(1, count + 1)
    )
    world.config = world.config.model_copy(
        update={"fix": FixRequest.model_validate({"description": "many"})}
    )
    task = PlannedTask(
        "many", ("src/a.py",), 8, 400, stages=(Producer.SPECIFIER, *TASK.stages), test_root="tests"
    )

    _, log = world.run(
        measurer=ScriptedMeasurer(),
        agent=ScriptedAgent(edits=[{"src/a.py": "def f():\n    return 1\n"}]),
        agents={
            Producer.SPECIFIER: ScriptedAgent(edits=[{}], criteria=criteria),
            Producer.TEST_AUTHOR: ScriptedAgent(edits=[TESTS], tests=declared),
            Producer.DOCUMENTER: ScriptedAgent(edits=[{"README.md": "# a\n"}]),
        },
        planner=ScriptedPlanner((task,)),
    )

    lines = _traceability(log.state, read_facts(log.path))
    for n in range(1, count + 1):
        assert any(f"critério {n} (origem E1): criterion {n} " in line for line in lines)


def test_rows_that_fill_an_event_to_the_last_byte_go_together_and_one_byte_more_splits_them() -> (
    None
):
    half = TRACE_BYTES // 2 - len('"",')  # a row is its JSON and the comma after it
    row = "r" * half

    assert _within_event_limit([row, row]) == [[row, row]]
    assert _within_event_limit([row, row + "r"]) == [[row], [row + "r"]]
    assert _within_event_limit([row, row, row]) == [[row, row], [row]]
    assert _within_event_limit([]) == []


def test_what_an_implementer_declares_never_replaces_the_manifest_of_the_author(
    world: MemoryWorld,
) -> None:
    other = DeclaredTest(criterion_id=1, test_id="tests/test_z.py::test_z", path="tests/test_z.py")

    outcome, _ = world.run(
        measurer=ScriptedMeasurer(),
        agent=ScriptedAgent(edits=[{"src/a.py": "def f():\n    return 1\n"}], tests=(other,)),
        agents={
            Producer.TEST_AUTHOR: ScriptedAgent(edits=[TESTS], tests=DECLARED),
            Producer.DOCUMENTER: ScriptedAgent(edits=[{"README.md": "# a\n"}]),
        },
        planner=ScriptedPlanner((TASK,)),
    )

    stored = ArtifactStore(world.run_dir / "store").get("test-manifest-t1")
    assert outcome.state is ExecutionState.DELIVERED
    assert isinstance(stored.body, AuthoredTestsBody)
    assert [t.test_id for t in stored.body.tests] == [t.test_id for t in DECLARED]


def test_a_run_with_no_test_author_has_nothing_to_trace(world: MemoryWorld) -> None:
    world.config = world.config.model_copy(update={"objective": Objective.REFACTOR, "fix": None})
    _, log = world.run(measurer=ScriptedMeasurer(), planner=ScriptedPlanner())

    assert _traceability(log.state, read_facts(log.path)) == [
        "Nenhuma tarefa integrada com critérios e testes."
    ]
