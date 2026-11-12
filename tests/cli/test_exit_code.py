"""The exit code says what the run delivered, not only that it ended: blocked tasks show in it."""

from types import SimpleNamespace
from typing import Any

import pytest

from forja.cli.run import exit_code
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


def test_a_run_with_no_outcome_was_interrupted() -> None:
    session: Any = SimpleNamespace()
    ended: Any = SimpleNamespace(outcome=None)

    assert exit_code(session, ended) == ExitCode.INTERRUPTED
