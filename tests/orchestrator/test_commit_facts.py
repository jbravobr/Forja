"""What the commit of an approved stage says besides the agent's reason: the checks that approved
it, the numbers that changed, what the agent had to guess; and the subject, held to Conventional
Commits."""

import json
from pathlib import Path

import pytest

from forja.core.artifacts.models import Assumption
from forja.core.gates import Approved, CheckRun
from forja.core.gates.effects import decide_effect
from forja.core.gates.pipeline import PipelineOutcome
from forja.orchestrator.config import Budget
from forja.orchestrator.workspace_git import (
    CommitMessage,
    NotConventional,
    StageFacts,
    conventional,
)
from tests.orchestrator.fakes import (
    BLOCKING,
    PASSED_WITH_A_GAP,
    UNTESTED,
    ScriptedAgent,
    ScriptedMeasurer,
)
from tests.orchestrator.harness import MemoryWorld

# -- the facts, as trailers and as a note ---------------------------------------------------------


def test_a_stage_with_no_facts_adds_no_trailer_and_no_note() -> None:
    assert StageFacts().trailers() == ()
    assert StageFacts().note() == ""


def test_each_check_that_approved_the_stage_is_an_evidence_id_in_the_order_it_ran() -> None:
    facts = StageFacts(evidence=("scope", "lint", "complexity"))

    assert facts.trailers() == (
        ("Evidence-Id", "scope"),
        ("Evidence-Id", "lint"),
        ("Evidence-Id", "complexity"),
    )


def test_only_a_number_that_changed_is_a_trailer() -> None:
    facts = StageFacts(
        metrics=(
            ("functions", 4.0, 6.0),
            ("lint_violations", 3.0, 3.0),
            ("max_ccn", 14.0, 9.0),
            ("max_distance", 0.5, 0.25),
        )
    )

    assert facts.trailers() == (
        ("Metric-functions", "4 -> 6"),
        ("Metric-max-ccn", "14 -> 9"),
        ("Metric-max-distance", "0.5 -> 0.25"),
    )


@pytest.mark.parametrize("numbers", [(None, 3.0), (3.0, None), (None, None)])
def test_a_number_one_side_does_not_have_is_not_a_trailer(
    numbers: tuple[float | None, float | None],
) -> None:
    assert StageFacts(metrics=(("tests", *numbers),)).trailers() == ()


def test_the_name_of_a_number_becomes_a_name_git_reads_as_a_trailer() -> None:
    facts = StageFacts(metrics=(("dependency_cycles[services/api]", 2.0, 0.0),))

    assert facts.trailers() == (("Metric-dependency-cycles-services-api", "2 -> 0"),)


def test_a_guess_is_one_line_however_the_agent_wrote_it() -> None:
    facts = StageFacts(assumptions=("  the parser is\n\tonly called   from the CLI \n", " \n "))

    assert facts.trailers() == (("Assumption", "the parser is only called from the CLI"),)


def test_the_facts_come_in_one_order_evidence_numbers_guesses() -> None:
    facts = StageFacts(("lint",), (("max_ccn", 9.0, 4.0),), ("nobody calls it",))

    assert [key for key, _ in facts.trailers()] == ["Evidence-Id", "Metric-max-ccn", "Assumption"]


def test_the_note_holds_every_number_changed_or_not_and_the_checks() -> None:
    facts = StageFacts(
        evidence=("scope", "lint"),
        metrics=(("lint_violations", 3.0, 3.0), ("max_ccn", 14.0, 9.0), ("tests", 7.0, None)),
        assumptions=("nobody calls it",),
    )

    assert json.loads(facts.note()) == {
        "gates_passed": ["scope", "lint"],
        "metrics": {
            "lint_violations": {"before": 3.0, "after": 3.0},
            "max_ccn": {"before": 14.0, "after": 9.0},
            "tests": {"before": 7.0, "after": None},
        },
    }
    assert facts.note().startswith('{\n  "gates_passed"')  # one item to a line, for a person


def test_checks_alone_make_no_note() -> None:
    assert StageFacts(evidence=("scope",), assumptions=("a guess",)).note() == ""


def test_the_trailers_of_a_message_end_with_its_facts() -> None:
    message = CommitMessage("fix: x", "", "cli 1", "p1", StageFacts(evidence=("scope",)))

    assert message.trailers("t1", "t1-a1", ("Stage-Id", "t1-s")) == (
        ("Task-Id", "t1"),
        ("Stage-Id", "t1-s"),
        ("Attempt-Id", "t1-a1"),
        ("Agent", "cli 1"),
        ("Policy-Version", "p1"),
        ("Evidence-Id", "scope"),
    )
    assert message.trailers("t1", "t1-a1")[1] == ("Attempt-Id", "t1-a1")


# -- the subject ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subject",
    [
        "refactor: split the parser",
        "fix: x",
        "feat: add the export",
        "test: before the change: split the parser",
        "docs: the record of t1",
        "docs(readme): fix the build line",
        "style: normalize formatting",
        "fix(api-2): x",
    ],
)
def test_a_conventional_subject_is_returned_as_it_is(subject: str) -> None:
    assert conventional(subject) == subject


@pytest.mark.parametrize(
    "subject",
    [
        "split the parser",  # no type
        "refactor split the parser",  # no colon
        "refactor:split the parser",  # no space after the colon
        "refactor: ",  # no text
        "refactor:",
        "Refactor: split the parser",  # the type is lower case
        "chore: tidy",  # not a type a stage has
        "refactoring: split",  # a type is a whole word
        " refactor: split",  # the type starts the line
        "docs(): x",  # an empty scope
        "docs(Readme): x",
        "docs(-a): x",
        "docs(readme: x",
        "docs(readme)x: y",
        "",
    ],
)
def test_a_subject_that_is_not_conventional_is_refused(subject: str) -> None:
    with pytest.raises(NotConventional, match="Conventional Commits"):
        conventional(subject)


def test_a_message_is_made_only_with_a_conventional_subject() -> None:
    with pytest.raises(NotConventional):
        CommitMessage.of("wip", "x", "", agent="c", policy_version="p")
    with pytest.raises(NotConventional):
        CommitMessage.of("refactor", " \n ", "", agent="c", policy_version="p")


# -- a run ----------------------------------------------------------------------------------------


@pytest.fixture
def world(tmp_path: Path) -> MemoryWorld:
    return MemoryWorld(tmp_path)


def passed(*checks: str) -> PipelineOutcome:
    return PipelineOutcome(
        tuple(
            CheckRun(check, n, BLOCKING, Approved(), decide_effect(BLOCKING, Approved()))
            for n, check in enumerate(checks, 1)
        )
    )


def stage_commit(world: MemoryWorld, stage: str = "t1-s"):  # type: ignore[no-untyped-def]
    memory = world.memory
    return next(c for c in memory.commits.values() if c.trailer("Stage-Id") == [stage])


def test_the_commit_of_an_approved_stage_carries_what_forja_measured_and_the_agent_guessed(
    world: MemoryWorld,
) -> None:
    measurer = ScriptedMeasurer(
        verdicts=(passed("scope", "lint", "complexity"),),
        stage_numbers=(("lint_violations", 1.0, 1.0), ("max_ccn", 12.0, 7.0)),
    )
    guess = Assumption(
        statement="f is only called from g", reason="no other call found", missing_evidence="a map"
    )

    world.run(measurer=measurer, agent=ScriptedAgent(assumptions=(guess,)))

    commit = stage_commit(world)
    assert commit.trailers[-5:] == (
        ("Evidence-Id", "scope"),
        ("Evidence-Id", "lint"),
        ("Evidence-Id", "complexity"),
        ("Metric-max-ccn", "12 -> 7"),
        ("Assumption", "f is only called from g"),
    )
    assert json.loads(world.memory.notes[commit.sha]) == {
        "gates_passed": ["scope", "lint", "complexity"],
        "metrics": {
            "lint_violations": {"before": 1.0, "after": 1.0},
            "max_ccn": {"before": 12.0, "after": 7.0},
        },
    }


def test_a_check_that_could_not_decide_is_not_evidence(world: MemoryWorld) -> None:
    world.run(measurer=ScriptedMeasurer(verdicts=(PASSED_WITH_A_GAP,)))

    assert stage_commit(world).trailer("Evidence-Id") == []


def test_the_numbers_are_taken_from_the_worktree_of_the_attempt_that_is_committed(
    world: MemoryWorld,
) -> None:
    seen: list[tuple[str, Path]] = []

    class Watching(ScriptedMeasurer):
        async def stage_metrics(self, ctx, sinks):  # type: ignore[no-untyped-def]
            seen.append((ctx.attempt_id, ctx.worktree))
            return await super().stage_metrics(ctx, sinks)

    world.run(measurer=Watching())

    assert seen == [("t1-a1", world.workspace.attempt_worktree("t1-a1"))]


def test_work_kept_for_the_test_author_was_not_approved_and_says_nothing_of_evidence(
    world: MemoryWorld,
) -> None:
    measurer = ScriptedMeasurer(verdicts=(UNTESTED,), stage_numbers=(("max_ccn", 12.0, 7.0),))

    world.run(measurer=measurer, budget=Budget(attempts_per_task=1))

    kept = stage_commit(world)
    assert [key for key, _ in kept.trailers] == [
        "Task-Id",
        "Stage-Id",
        "Attempt-Id",
        "Agent",
        "Policy-Version",
    ]
    assert world.memory.notes == {}
