"""Gate 11: the new version judged against the base, with real small programs for both."""

import asyncio
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import JsonValue

from forja.checks.differential import DifferentialCheck
from forja.checks.differential_base import DifferentialBase, measure_base
from forja.checks.tooling import Tools
from forja.core.events import EventLog
from forja.core.gates import Approved, GateInput, GateResult, Inconclusive, NotApplicable, Rejected
from forja.core.policy.differential import (
    EXIT_CODE,
    JSON,
    STDOUT,
    DifferentialCase,
    DifferentialOutput,
)
from forja.core.states import ExecutionState, Level
from tests.checks.support import RUN, sinks
from tests.core.events.support import FakeClock

SHOW = "import sys; sys.stdout.write(open('data.txt').read())"
SLEEP = "import time; time.sleep(float(open('data.txt').read()))"
HOLD = "x = b'x' * int(open('data.txt').read()); print('done')"
EXIT = "import sys; sys.exit(int(open('data.txt').read()))"
APPEND = "import sys; open(sys.argv[1], 'a').write('x')"
COUNTING = (
    "import pathlib, sys; p = pathlib.Path(sys.argv[1]); "
    "n = int(p.read_text()) + 1 if p.exists() else 1; p.write_text(str(n)); print('run', n)"
)
LOOSE: dict[str, JsonValue] = {
    "thresholds": {"python": {"max_time_ratio": 100, "max_memory_ratio": 100}}
}


@pytest.fixture
def log(tmp_path: Path) -> EventLog:
    opened = EventLog.open(tmp_path / "events.jsonl", RUN, clock=FakeClock())
    opened.append_transition(Level.EXECUTION, RUN, None, ExecutionState.PREPARING)
    return opened


def case(name: str, program: str, *outputs: DifferentialOutput, cwd: str = "") -> DifferentialCase:
    return DifferentialCase(
        name,
        (sys.executable, "-c", program),
        outputs or (DifferentialOutput(STDOUT),),
        cwd,
    )


def judge(
    log: EventLog,
    tmp_path: Path,
    cases: tuple[DifferentialCase, ...],
    *,
    was: str = "same",
    now: str = "same",
    values: Mapping[str, JsonValue] | None = None,
    languages: tuple[str, ...] = ("python",),
    alter: Callable[[DifferentialBase], DifferentialBase] = lambda base: base,
) -> GateResult:
    old, new = tmp_path / "old", tmp_path / "new"
    for folder, text in ((old, was), (new, now)):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "data.txt").write_text(text, encoding="utf-8")
    (old / "sub").mkdir(exist_ok=True)  # only the base has the directory a case may run in
    tools = Tools(
        sinks=sinks,
        log=log,
        level=Level.EXECUTION,
        target=RUN,
        logs=tmp_path / "logs",
        host_env=dict(os.environ),
    )
    base = alter(asyncio.run(measure_base(tools, cases, old, tmp_path / "data", {})))
    check = DifferentialCheck(tools, cases, base, new, {}, languages)
    data = GateInput(new, "head", "t1", "t1-s", "t1-a1", values=values or {})
    return asyncio.run(check.evaluate(data))


def findings(result: GateResult) -> list[tuple[str, str]]:
    assert isinstance(result, Rejected)
    return [(f.message, f.target) for f in result.findings]


def test_without_cases_the_gate_does_not_run(log: EventLog, tmp_path: Path) -> None:
    assert judge(log, tmp_path, ()) == NotApplicable(missing_capability="differential_cases")


def test_the_same_outputs_are_approved(log: EventLog, tmp_path: Path) -> None:
    assert isinstance(judge(log, tmp_path, (case("s", SHOW),), values=LOOSE), Approved)


def test_a_different_output_rejects_without_saying_what_it_held(
    log: EventLog, tmp_path: Path
) -> None:
    result = judge(log, tmp_path, (case("s", SHOW),), was="alpha", now="bravo", values=LOOSE)

    [(message, target)] = findings(result)
    assert target == "s: stdout"
    assert message.startswith("the output differs from the base: differs at byte 0 (line 1)")
    assert "alpha" not in message and "bravo" not in message


def test_an_output_that_differs_in_every_run_is_reported_once(
    log: EventLog, tmp_path: Path
) -> None:
    result = judge(log, tmp_path, (case("s", SHOW),), was="a", now="b", values=LOOSE)

    assert len(findings(result)) == 1


def test_each_output_of_a_case_is_judged_by_its_own_rule(log: EventLog, tmp_path: Path) -> None:
    program = "import sys; sys.stdout.write(open('data.txt').read()); sys.exit(1)"
    outputs = (DifferentialOutput(STDOUT, JSON), DifferentialOutput(EXIT_CODE))

    same_document = judge(
        log,
        tmp_path / "a",
        (case("j", program, *outputs),),
        was='{"a":1,"b":2}',
        now='{ "b": 2, "a": 1 }',
        values=LOOSE,
    )
    other_document = judge(
        log,
        tmp_path / "b",
        (case("j", program, *outputs),),
        was='{"a":1}',
        now='{"a":2}',
        values=LOOSE,
    )

    assert isinstance(same_document, Approved)
    assert [target for _, target in findings(other_document)] == ["j: stdout"]


def test_a_different_exit_code_rejects(log: EventLog, tmp_path: Path) -> None:
    result = judge(
        log,
        tmp_path,
        (case("e", EXIT, DifferentialOutput(EXIT_CODE)),),
        was="0",
        now="3",
        values=LOOSE,
    )

    assert [target for _, target in findings(result)] == ["e: exit_code"]


def test_a_slower_version_rejects_with_the_ratio_and_the_limit(
    log: EventLog, tmp_path: Path
) -> None:
    result = judge(
        log,
        tmp_path,
        (case("t", SLEEP),),
        was="0.05",
        now="0.6",
        values={"thresholds": {"python": {"max_memory_ratio": 100}}},
        languages=("python",),
    )

    assert isinstance(result, Rejected)
    [found] = result.findings
    assert (found.message, found.target, found.limit) == ("slower than the base", "t", 1.25)
    assert found.measured is not None and found.measured > 2


def test_the_loosest_limit_of_the_languages_is_used(log: EventLog, tmp_path: Path) -> None:
    values: dict[str, JsonValue] = {
        "thresholds": {
            "go": {"max_time_ratio": 100, "max_memory_ratio": 100},
            "python": {"max_time_ratio": 1.01, "max_memory_ratio": 1.01},
        }
    }
    slow = (case("t", SLEEP),)

    both = judge(
        log, tmp_path / "a", slow, was="0.05", now="0.6", values=values, languages=("python", "go")
    )
    only = judge(
        log, tmp_path / "b", slow, was="0.05", now="0.6", values=values, languages=("python",)
    )

    assert isinstance(both, Approved)
    assert isinstance(only, Rejected)


@pytest.mark.skipif(sys.platform != "win32", reason="the peak memory is measured only on Windows")
def test_a_version_that_holds_more_memory_rejects(log: EventLog, tmp_path: Path) -> None:
    result = judge(
        log,
        tmp_path,
        (case("m", HOLD),),
        was="5000000",
        now="150000000",
        values={"thresholds": {"python": {"max_time_ratio": 100}}},
        languages=("python",),
    )

    assert findings(result) == [("uses more memory than the base", "m")]


@pytest.mark.skipif(sys.platform != "win32", reason="the peak memory is measured only on Windows")
def test_memory_is_not_judged_when_the_base_has_no_measure(log: EventLog, tmp_path: Path) -> None:
    def without_memory(base: DifferentialBase) -> DifferentialBase:
        return DifferentialBase(
            {name: replace(kept, memories=()) for name, kept in base.cases.items()}, base.inputs
        )

    result = judge(
        log,
        tmp_path,
        (case("m", HOLD),),
        was="5000000",
        now="150000000",
        values=LOOSE,
        alter=without_memory,
    )

    assert isinstance(result, Approved)


def test_the_number_of_runs_comes_from_the_policy(log: EventLog, tmp_path: Path) -> None:
    counter = tmp_path / "counter"
    runs = (DifferentialCase("n", (sys.executable, "-c", APPEND, str(counter))),)

    def forget(base: DifferentialBase) -> DifferentialBase:
        counter.write_text("", encoding="utf-8")
        return base

    default: dict[str, JsonValue] = {
        "thresholds": {"python": {"max_time_ratio": 100, "max_memory_ratio": 100}}
    }
    judge(log, tmp_path / "a", runs, values=default, languages=("python",), alter=forget)
    after_default = counter.read_text(encoding="utf-8")
    two: dict[str, JsonValue] = {
        "thresholds": {"python": {"max_time_ratio": 100, "max_memory_ratio": 100, "runs": 2}}
    }
    judge(log, tmp_path / "b", runs, values=two, languages=("python",), alter=forget)

    assert (after_default, counter.read_text(encoding="utf-8")) == ("xxx", "xx")


def test_at_least_one_run_is_made_whatever_the_policy_says(log: EventLog, tmp_path: Path) -> None:
    counter = tmp_path / "counter"
    runs = (DifferentialCase("n", (sys.executable, "-c", APPEND, str(counter))),)
    values: dict[str, JsonValue] = {
        "thresholds": {
            "python": {"max_time_ratio": 100, "max_memory_ratio": 100, "runs": 0.5},
        }
    }

    def forget(base: DifferentialBase) -> DifferentialBase:
        counter.write_text("", encoding="utf-8")
        return base

    judge(log, tmp_path, runs, values=values, languages=("python",), alter=forget)

    assert counter.read_text(encoding="utf-8") == "x"


def test_a_case_the_base_found_unstable_is_left_out(log: EventLog, tmp_path: Path) -> None:
    unstable = DifferentialCase("u", (sys.executable, "-c", COUNTING, str(tmp_path / "count")))

    result = judge(log, tmp_path, (unstable,), values=LOOSE)

    assert result == NotApplicable(missing_capability="differential_stable_cases")


def test_a_stable_case_is_judged_beside_an_unstable_one(log: EventLog, tmp_path: Path) -> None:
    unstable = DifferentialCase("u", (sys.executable, "-c", COUNTING, str(tmp_path / "count")))

    result = judge(log, tmp_path, (unstable, case("s", SHOW)), was="a", now="b", values=LOOSE)

    assert [target for _, target in findings(result)] == ["s: stdout"]


def test_a_run_that_cannot_be_made_is_inconclusive(log: EventLog, tmp_path: Path) -> None:
    result = judge(log, tmp_path, (case("c", SHOW, cwd="sub"),), values=LOOSE)

    assert isinstance(result, Inconclusive)
    assert "case c" in result.detail


def test_a_rejection_wins_over_a_run_that_cannot_be_made(log: EventLog, tmp_path: Path) -> None:
    cases = (case("c", SHOW, cwd="sub"), case("s", SHOW))

    result = judge(log, tmp_path, cases, was="a", now="b", values=LOOSE)

    assert [target for _, target in findings(result)] == ["s: stdout"]


def test_a_case_the_base_never_measured_is_inconclusive(log: EventLog, tmp_path: Path) -> None:
    def empty(base: DifferentialBase) -> DifferentialBase:
        return DifferentialBase({}, base.inputs)

    result = judge(log, tmp_path, (case("s", SHOW),), values=LOOSE, alter=empty)

    assert isinstance(result, Inconclusive)
    assert "case s" in result.detail


def test_the_frozen_input_of_the_base_is_what_the_new_version_reads(
    log: EventLog, tmp_path: Path
) -> None:
    program = "import sys; sys.stdout.write(sys.stdin.read())"
    with_input = DifferentialCase("i", (sys.executable, "-c", program), stdin="data.txt")

    result = judge(log, tmp_path, (with_input,), was="from-base", now="from-new", values=LOOSE)

    assert isinstance(result, Approved)  # both read the copy the base froze, not their own file


def test_a_ratio_exactly_at_the_limit_passes_and_just_above_it_does_not() -> None:
    from forja.checks.differential import _ratio

    assert _ratio("c", "slower", [125.0], [100.0], 1.25) == []
    assert len(_ratio("c", "slower", [126.0], [100.0], 1.25)) == 1


def test_a_base_median_of_zero_is_not_judged() -> None:
    from forja.checks.differential import _ratio

    assert _ratio("c", "slower", [5.0], [0.0], 1.25) == []
