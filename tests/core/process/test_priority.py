"""Low priority: the process and all it starts yield the processor to the rest of the machine."""

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from forja.core.process import Outcome, ProcessRunner, new_group
from tests.core.process.support import (
    LOWEST_PRIORITY,
    OWN_PRIORITY,
    RecordingSink,
    run_to_end,
    spec,
)

ASK_A_GRANDCHILD = (
    "import subprocess, sys\n"
    f"run = subprocess.run([sys.executable, '-c', {OWN_PRIORITY!r}], capture_output=True)\n"
    "print(run.stdout.decode().strip())\n"
)
LOWEST = LOWEST_PRIORITY


def own_priority() -> int:
    """The priority of this test process, read the way the children read theirs."""
    scope: dict[str, Any] = {}
    exec(OWN_PRIORITY.replace("print(", "found = ("), scope)  # noqa: S102
    return int(scope["found"])


class Watched:
    """A real group that notes what the runner asks of it, in order."""

    def __init__(self, refuse: OSError | None = None) -> None:
        self.inner = new_group()
        self.asked: list[str] = []
        self.refuse = refuse

    def spawn_kwargs(self) -> dict[str, Any]:
        self.asked.append("spawn")
        return self.inner.spawn_kwargs()

    def lower_priority(self) -> None:
        self.asked.append("lower_priority")
        if self.refuse is not None:
            raise self.refuse
        self.inner.lower_priority()

    def attach(self, pid: int) -> None:
        self.inner.attach(pid)

    def interrupt(self) -> None:
        self.inner.interrupt()

    def kill(self) -> None:
        self.inner.kill()

    def peak_memory(self) -> int | None:
        return self.inner.peak_memory()

    def close(self) -> None:
        self.inner.close()


def test_a_low_priority_process_runs_at_the_lowest_priority_of_the_system(tmp_path: Path) -> None:
    low = replace(spec(OWN_PRIORITY), low_priority=True)

    result, sink = run_to_end(low, tmp_path / "op.log")

    assert result.exit_code == 0
    assert sink.lines("out") == [str(LOWEST)]


def test_what_a_low_priority_process_starts_is_low_priority_too(tmp_path: Path) -> None:
    low = replace(spec(ASK_A_GRANDCHILD), low_priority=True)

    result, sink = run_to_end(low, tmp_path / "op.log")

    assert result.exit_code == 0
    assert sink.lines("out") == [str(LOWEST)]


def test_a_process_nobody_lowered_keeps_the_priority_of_who_started_it(tmp_path: Path) -> None:
    result, sink = run_to_end(spec(OWN_PRIORITY), tmp_path / "op.log")

    assert result.exit_code == 0
    assert sink.lines("out") == [str(own_priority())]


@pytest.mark.parametrize(
    ("low", "asked"), [(True, ["lower_priority", "spawn"]), (False, ["spawn"])]
)
def test_the_group_is_lowered_before_the_process_exists_and_only_when_asked(
    tmp_path: Path, low: bool, asked: list[str]
) -> None:
    group = Watched()
    wanted = replace(spec("pass"), low_priority=low)

    runner = ProcessRunner(RecordingSink(), group_factory=lambda: group)
    asyncio.run(runner.run("op-1", wanted, tmp_path / "op.log"))

    assert group.asked == asked


def test_a_system_that_refuses_the_low_priority_does_not_run_the_process(tmp_path: Path) -> None:
    group = Watched(refuse=PermissionError("no"))
    low = replace(spec("print('ran')"), low_priority=True)
    sink = RecordingSink()

    runner = ProcessRunner(sink, group_factory=lambda: group)
    result = asyncio.run(runner.run("op-1", low, tmp_path / "op.log"))

    assert result.outcome is Outcome.SPAWN_FAILED
    assert group.asked == ["lower_priority"]
    assert sink.lines("out") == []


def test_the_default_is_not_to_lower() -> None:
    assert spec("pass").low_priority is False
