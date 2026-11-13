"""The exit code says what the run delivered, not only that it ended: blocked tasks show in it."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from forja.cli.run import closing_lines, exit_code
from forja.orchestrator.outcome import ExitCode


def code(
    *,
    integrated: int,
    blocked: int,
    pending: int = 0,
    state: str = "delivered",
    delivered: bool = True,
) -> int:
    view = SimpleNamespace(
        tasks_integrated=integrated,
        tasks_blocked=blocked,
        tasks_total=integrated + blocked + pending,
        state=state,
    )
    session: Any = SimpleNamespace(view=SimpleNamespace(snapshot=lambda: view))
    outcome = SimpleNamespace(exit_code=ExitCode.DELIVERED if delivered else ExitCode.FAILED)
    ended: Any = SimpleNamespace(outcome=outcome)
    return exit_code(session, ended)


@pytest.mark.parametrize(
    ("integrated", "blocked", "expected"),
    [
        (3, 0, ExitCode.DELIVERED),
        (2, 1, ExitCode.SOME_BLOCKED),
        (0, 2, ExitCode.NOTHING_INTEGRATED),
        (0, 1, ExitCode.NOTHING_INTEGRATED),
        (0, 0, ExitCode.DELIVERED),
    ],
)
def test_a_delivery_with_blocked_tasks_does_not_exit_zero(
    integrated: int, blocked: int, expected: ExitCode
) -> None:
    assert code(integrated=integrated, blocked=blocked) == expected


@pytest.mark.parametrize(
    ("integrated", "blocked", "pending", "expected"),
    [
        (1, 0, 2, ExitCode.SOME_BLOCKED),
        (0, 0, 2, ExitCode.NOTHING_INTEGRATED),
        (1, 1, 1, ExitCode.SOME_BLOCKED),
        (0, 1, 1, ExitCode.NOTHING_INTEGRATED),
    ],
)
def test_a_delivery_with_tasks_the_tokens_did_not_reach_does_not_exit_zero(
    integrated: int, blocked: int, pending: int, expected: ExitCode
) -> None:
    assert code(integrated=integrated, blocked=blocked, pending=pending) == expected


def test_a_failed_run_keeps_its_own_code() -> None:
    assert code(integrated=1, blocked=1, delivered=False) == ExitCode.FAILED


def not_started_lines(*, integrated: int, blocked: int, pending: int) -> list[str]:
    view = SimpleNamespace(
        tasks_integrated=integrated,
        tasks_blocked=blocked,
        tasks_total=integrated + blocked + pending,
        state="delivered",
    )
    session: Any = SimpleNamespace(
        view=SimpleNamespace(snapshot=lambda: view),
        run_id="r1",
        directory=Path("runs") / "r1",
        context=SimpleNamespace(notices=()),
    )
    ended: Any = SimpleNamespace(interrupted=False, outcome=SimpleNamespace(message="Entregue."))
    return [line for line in closing_lines(session, ended) if "não começaram" in line]


@pytest.mark.parametrize(
    ("integrated", "blocked", "pending", "said"),
    [
        (2, 1, 3, ["3 tarefa(s) não começaram: o limite de tokens acabou antes."]),
        (1, 1, 1, ["1 tarefa(s) não começaram: o limite de tokens acabou antes."]),
        (0, 0, 1, ["1 tarefa(s) não começaram: o limite de tokens acabou antes."]),
        (2, 1, 0, []),
        (0, 0, 0, []),
    ],
)
def test_the_closing_lines_count_the_tasks_that_never_started(
    integrated: int, blocked: int, pending: int, said: list[str]
) -> None:
    assert not_started_lines(integrated=integrated, blocked=blocked, pending=pending) == said


def test_a_run_with_no_outcome_was_interrupted() -> None:
    session: Any = SimpleNamespace()
    ended: Any = SimpleNamespace(outcome=None)

    assert exit_code(session, ended) == ExitCode.INTERRUPTED
