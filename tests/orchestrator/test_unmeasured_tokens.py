"""A CLI that does not say what it used: the use is unknown, not zero, and the summary says the
limit of tokens was not measured."""

from pathlib import Path

import pytest

from forja.core.events import EventLog
from forja.core.states import Level
from forja.orchestrator.budget import Consumption, read_consumption
from forja.orchestrator.config import RunConfig
from forja.orchestrator.summary import Facts, read_facts, render_summary
from tests.core.events.support import RUN, FakeClock, create_run


def usage(log: EventLog, **payload: object) -> None:
    log.append_progress(
        Level.EXECUTION,
        RUN,
        "agent_activity",
        payload={"type": "usage", **payload},  # type: ignore[dict-item]
    )


def opened(tmp_path: Path) -> EventLog:
    log = EventLog.open(tmp_path / "e.jsonl", RUN, clock=FakeClock())
    create_run(log)
    return log


def config() -> RunConfig:
    return RunConfig.model_validate(
        {
            "repo": "r",
            "base_commit": "c" * 40,
            "objective": "refactor",
            "implementer": {"name": "cursor", "path": "p", "version": "1"},
            "forja_version": "0",
            "policy_version": "0",
        }
    )


def test_a_log_that_is_not_there_has_nothing_unmeasured(tmp_path: Path) -> None:
    assert read_consumption(tmp_path / "none.jsonl") == Consumption(0.0, 0, 0)


def test_a_call_that_said_what_it_used_is_measured_even_when_it_used_nothing(
    tmp_path: Path,
) -> None:
    log = opened(tmp_path)
    usage(log, input=100, output=20)
    usage(log, input=0, output=0)

    found = read_consumption(log.path)

    assert (found.tokens, found.unmeasured) == (120, 0)


@pytest.mark.parametrize(
    ("payload", "tokens"),
    [
        ({"input": None, "output": None}, 0),
        ({"input": 7, "output": None}, 7),  # what it did say still counts
        ({"input": None, "output": 3}, 3),
        ({"input": 5}, 5),  # a side that is missing was not said either
        ({}, 0),
    ],
)
def test_a_call_that_left_a_side_unsaid_is_counted_as_unmeasured(
    tmp_path: Path, payload: dict[str, object], tokens: int
) -> None:
    log = opened(tmp_path)
    usage(log, **payload)

    found = read_consumption(log.path)

    assert (found.tokens, found.unmeasured) == (tokens, 1)


def test_every_unmeasured_call_is_counted_and_only_usage_of_the_agent(tmp_path: Path) -> None:
    log = opened(tmp_path)
    usage(log, input=None, output=None)
    usage(log, input=10, output=1)
    usage(log, input=None, output=None)
    log.append_progress(Level.EXECUTION, RUN, "agent_activity", payload={"type": "text"})
    log.append_progress(Level.EXECUTION, RUN, "other", payload={"type": "usage"})

    found = read_consumption(log.path)

    assert (found.tokens, found.unmeasured) == (11, 2)


def test_the_facts_of_the_summary_hold_the_calls_nobody_measured(tmp_path: Path) -> None:
    log = opened(tmp_path)
    usage(log, input=None, output=None)
    usage(log, input=None, output=None)
    usage(log, input=4, output=4)

    assert read_facts(log.path).unmeasured_calls == 2


def test_the_summary_says_the_limit_of_tokens_was_not_measured(tmp_path: Path) -> None:
    log = opened(tmp_path)

    text = render_summary(log.state, Facts(unmeasured_calls=3), config(), "b", Path("x"))

    said = (
        "- **O limite de tokens não foi medido: 3 chamada(s) ao agente não informaram os tokens "
        "usados, e o que elas gastaram não entrou na conta.**"
    )
    assert said in text.splitlines()
    assert text.index("## Ambiente e garantias") < text.index(said) < text.index("## Para publicar")


def test_a_run_whose_calls_were_all_measured_says_nothing_of_it(tmp_path: Path) -> None:
    log = opened(tmp_path)

    text = render_summary(log.state, Facts(), config(), "b", Path("x"))

    assert "não foi medido" not in text
