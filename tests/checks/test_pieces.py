"""The smaller parts of the checks: policy values, tool output, tool runs and scanner reports."""

import asyncio
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue

from forja.checks.base import MAX_FINDINGS, overrides_from, rejected
from forja.checks.changes import AddedLine, Change, ChangeKind, Changes
from forja.checks.complexity import DEFAULT_MAX_CCN, max_ccn, parse_functions
from forja.checks.secrets import SecretsCheck
from forja.checks.specs import default_registry
from forja.checks.tooling import DEFAULT_TIMEOUT, Tools
from forja.core.adapters import CommandPlan
from forja.core.events import EventLog
from forja.core.gates import Approved, Finding, GateInput, Inconclusive, InconclusiveReason, Mode
from forja.core.gates.models import Rejected
from forja.core.process import Outcome
from forja.core.states import ExecutionState, Level
from forja.languages.python import PythonAdapter
from tests.checks.support import RUN, sinks
from tests.core.events.support import FakeClock


def data(values: dict | None = None) -> GateInput:  # type: ignore[type-arg]
    return GateInput(Path("."), "c" * 40, "t1", "t1-s", "t1-a1", values=values or {})


# -- policy values ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({}, DEFAULT_MAX_CCN),
        ({"thresholds": {"python": {"max_ccn": 7}}}, 7),
        ({"thresholds": {"python": {"max_ccn": 7.9}}}, 7),
        ({"thresholds": {"go": {"max_ccn": 7}}}, DEFAULT_MAX_CCN),
        ({"thresholds": {"python": {"max_ccn": 0}}}, DEFAULT_MAX_CCN),
        ({"thresholds": {"python": {"max_ccn": -3}}}, DEFAULT_MAX_CCN),
        ({"thresholds": {"python": {"max_ccn": "7"}}}, DEFAULT_MAX_CCN),
        ({"thresholds": {"python": {}}}, DEFAULT_MAX_CCN),
        ({"thresholds": {"python": "x"}}, DEFAULT_MAX_CCN),
        ({"thresholds": "x"}, DEFAULT_MAX_CCN),
    ],
)
def test_the_complexity_limit_comes_from_the_policy_for_the_language(
    values: dict[str, Any],
    expected: int,
) -> None:
    assert max_ccn(data(values), "python") == expected


def test_the_default_limit_is_the_one_lizard_itself_uses() -> None:
    assert DEFAULT_MAX_CCN == 15


def test_the_repositorys_own_commands_are_keyed_by_language_and_operation() -> None:
    adapter = PythonAdapter()
    values: dict[str, JsonValue] = {
        "commands": {"test": ["make", "check"], "build": [], "lint": "no", "format": ["x"]}
    }

    assert overrides_from(adapter, values) == {
        "python": {"test": ("make", "check"), "format": ("x",)}
    }
    assert overrides_from(adapter, {"commands": {"build": []}}) == {}
    assert overrides_from(adapter, {"commands": "x"}) == {}
    assert overrides_from(adapter, {}) == {}


def test_the_findings_handed_back_are_the_first_few() -> None:
    many = [Finding(message="m", target=f"t{n}") for n in range(MAX_FINDINGS + 5)]

    assert len(rejected(many).findings) == MAX_FINDINGS
    assert rejected(many).findings[0].target == "t0"


# -- lizard output ---------------------------------------------------------------------------------


def test_complexity_is_read_by_file_and_function() -> None:
    lines = [
        '6,3,19,1,6,"f@4-9@pkg\\m.py","pkg\\m.py","f","f( a )",4,9',
        '2,1,7,1,2,"g@12-13@pkg/m.py","pkg/m.py","g","g( x )",12,13',
        '2,9,7,1,2,"g@20-21@pkg/m.py","pkg/m.py","g","g( y )",20,21',
        "not,a,row",
        '2,x,7,1,2,"h@1-2@pkg/m.py","pkg/m.py","h","h()",1,2',
        '2,5,7,1,2,"h@1-2@pkg/m.py","pkg/m.py","h","h()",1,2,extra',
        "",
    ]

    assert parse_functions(lines) == {("pkg/m.py", "f"): 3, ("pkg/m.py", "g"): 9}


# -- the specs -------------------------------------------------------------------------------------


def test_the_checks_of_this_increment_are_registered_in_gate_order_with_their_defaults() -> None:
    specs = {spec.id: spec for spec in default_registry()}

    assert {
        i: (s.gate, s.default_mode, cast(Any, s.default_on_inconclusive).value)
        for i, s in specs.items()
    } == {
        "scope": (1, Mode.BLOCKING, "block"),
        "freeze": (1, Mode.BLOCKING, "block"),
        "secrets": (2, Mode.BLOCKING, "block"),
        "dependency_install": (2, Mode.BLOCKING, "block"),
        "format": (3, Mode.BLOCKING, "block"),
        "lint": (3, Mode.BLOCKING, "block"),
        "types": (3, Mode.OBSERVATION, "premise"),
        "build": (4, Mode.BLOCKING, "block"),
        "tests": (5, Mode.BLOCKING, "block"),
        "red_green": (5, Mode.BLOCKING, "block"),
        "e2e": (6, Mode.OBSERVATION, "block"),
        "test_data": (6, Mode.OBSERVATION, "premise"),
        "properties": (6, Mode.OBSERVATION, "premise"),
        "mutation": (7, Mode.OBSERVATION, "premise"),
        "complexity": (8, Mode.BLOCKING, "premise"),
        "crap": (8, Mode.OBSERVATION, "premise"),
        "pass_through": (8, Mode.OBSERVATION, "premise"),
        "module_depth": (8, Mode.OBSERVATION, "premise"),
        "duplication": (8, Mode.OBSERVATION, "premise"),
        "dependency_cycles": (9, Mode.OBSERVATION, "block"),
        "layer_rules": (9, Mode.OBSERVATION, "block"),
        "package_metrics": (9, Mode.OBSERVATION, "premise"),
        "sast": (10, Mode.OBSERVATION, "block"),
        "dependency_audit": (10, Mode.OBSERVATION, "block"),
        "new_dependency": (10, Mode.OBSERVATION, "premise"),
        "differential": (11, Mode.OBSERVATION, "premise"),
        "voice": (12, Mode.OBSERVATION, "premise"),
        "comments": (12, Mode.OBSERVATION, "premise"),
        "citations": (13, Mode.OBSERVATION, "block"),
        "traceability": (13, Mode.OBSERVATION, "block"),
        "change_record": (14, Mode.OBSERVATION, "block"),
        "readme": (14, Mode.OBSERVATION, "premise"),
        "doc_drift": (14, Mode.OBSERVATION, "premise"),
        "adr": (14, Mode.OBSERVATION, "premise"),
    }
    assert specs["scope"].limits == {"max_files", "max_lines"}
    assert specs["mutation"].thresholds == {"min_score"}
    assert specs["mutation"].limits == {"workers", "line_tests"}
    assert specs["complexity"].thresholds == {"max_ccn"}
    assert specs["crap"].thresholds == {"max_crap"}
    assert specs["module_depth"].thresholds == {"max_interface_ratio", "min_statements"}
    assert specs["duplication"].limits == {"min_tokens", "min_lines"}
    assert specs["red_green"].commands == {"test"}
    assert specs["tests"].commands == {"test"} and specs["build"].commands == {"build"}
    assert specs["format"].commands == {"format"} and specs["lint"].commands == {"lint"}


# -- running a tool --------------------------------------------------------------------------------


@pytest.fixture
def log(tmp_path: Path) -> EventLog:
    opened = EventLog.open(tmp_path / "events.jsonl", RUN, clock=FakeClock())
    opened.append_transition(Level.EXECUTION, RUN, None, ExecutionState.PREPARING)
    return opened


def tools(log: EventLog, tmp_path: Path, **timeouts: float) -> Tools:
    return Tools(
        sinks=sinks,
        log=log,
        level=Level.EXECUTION,
        target=RUN,
        logs=tmp_path / "logs",
        host_env={},
        timeouts=timeouts,
    )


def python(code: str, cwd: Path) -> CommandPlan:
    return CommandPlan((sys.executable, "-c", code), cwd)


def test_a_run_has_a_log_an_id_and_a_way_to_read_what_it_printed(
    log: EventLog, tmp_path: Path
) -> None:
    execution = asyncio.run(tools(log, tmp_path).run("lint", python("print('hello')", tmp_path)))

    assert execution.finished and execution.result.exit_code == 0
    assert execution.operation_id == f"run_tool:{RUN}:lint:1"
    assert execution.lines == ["hello"]
    assert execution.output.exit_code == 0
    assert execution.result.log_path == tmp_path / "logs" / f"run_tool-{RUN}-lint-1.log"
    assert execution.check == "lint"


def test_a_repeated_run_never_reuses_an_id_even_after_a_cut(log: EventLog, tmp_path: Path) -> None:
    runner = tools(log, tmp_path)
    log.append_intent(Level.EXECUTION, RUN, "run_tool", f"run_tool:{RUN}:tests:1", payload={})

    first = asyncio.run(runner.run("tests", python("pass", tmp_path)))

    assert first.operation_id == f"run_tool:{RUN}:tests:2"


def test_ids_taken_by_a_finished_run_are_skipped_too(log: EventLog, tmp_path: Path) -> None:
    log.append_intent(Level.EXECUTION, RUN, "run_tool", f"run_tool:{RUN}:tests:1", payload={})
    log.append_effect(Level.EXECUTION, RUN, "run_tool", f"run_tool:{RUN}:tests:1")
    log.append_intent(Level.EXECUTION, RUN, "run_tool", f"run_tool:{RUN}:tests:2", payload={})

    execution = asyncio.run(tools(log, tmp_path).run("tests", python("pass", tmp_path)))

    assert execution.operation_id == f"run_tool:{RUN}:tests:3"


def test_a_tool_that_overruns_its_own_time_is_stopped_and_inconclusive(
    log: EventLog, tmp_path: Path
) -> None:
    execution = asyncio.run(
        tools(log, tmp_path, slow=0.3).run("slow", python("import time; time.sleep(30)", tmp_path))
    )

    assert not execution.finished and execution.result.outcome is Outcome.TIMED_OUT
    result = execution.inconclusive("slow thing")
    assert result.reason is InconclusiveReason.TIMED_OUT
    assert result.detail == f"slow thing: {Path(sys.executable).name} timed out"
    assert result.tool_run_ids == (execution.operation_id,)


def test_a_tool_that_cannot_start_is_inconclusive_by_name(log: EventLog, tmp_path: Path) -> None:
    execution = asyncio.run(
        tools(log, tmp_path).run("secrets", CommandPlan(("no-such-tool-anywhere",), tmp_path))
    )

    result = execution.inconclusive("secrets")
    assert not execution.finished and execution.result.outcome is Outcome.SPAWN_FAILED
    assert result.reason is InconclusiveReason.TOOL_FAILED
    assert result.detail == "secrets: no-such-tool-anywhere could not start"


def test_the_time_given_to_a_tool_is_its_own_or_the_default(log: EventLog, tmp_path: Path) -> None:
    assert DEFAULT_TIMEOUT == 300.0
    execution = asyncio.run(tools(log, tmp_path, quick=5.0).run("other", python("pass", tmp_path)))

    assert execution.finished


# -- the secrets report ----------------------------------------------------------------------------


def secrets(
    log: EventLog, tmp_path: Path, script: str, patch_text: str = '+KEY = "x"'
) -> SecretsCheck:
    patch = (
        "diff --git a/a.py b/a.py",
        "--- a/a.py",
        "+++ b/a.py",
        "@@ -0,0 +1 @@",
        patch_text,
    )
    changes = Changes((Change(ChangeKind.ADDED, "a.py"),), 1, patch)
    return SecretsCheck(
        tools(log, tmp_path),
        tmp_path,
        changes,
        tmp_path / "reports",
        (sys.executable, "-c", script, "--"),
    )


def judge(check: SecretsCheck) -> object:
    return asyncio.run(check.evaluate(data()))


def test_nothing_added_means_nothing_to_scan(log: EventLog, tmp_path: Path) -> None:
    check = SecretsCheck(
        tools(log, tmp_path), tmp_path, Changes((), 0, ()), tmp_path / "reports", ("no-such-tool",)
    )

    assert judge(check) == Approved()
    assert not (tmp_path / "reports").exists()


def test_a_clean_scan_approves(log: EventLog, tmp_path: Path) -> None:
    assert judge(secrets(log, tmp_path, "raise SystemExit(0)")) == Approved()


def test_a_scanner_that_fails_for_its_own_reasons_is_inconclusive(
    log: EventLog, tmp_path: Path
) -> None:
    result = judge(secrets(log, tmp_path, "raise SystemExit(1)"))

    assert isinstance(result, Inconclusive)
    assert result.reason is InconclusiveReason.TOOL_FAILED
    assert result.detail == "secrets: gitleaks ended with 1"
    assert len(result.tool_run_ids) == 1 and result.tool_run_ids[0].endswith(":secrets:1")


REPORT = (
    "import json, sys\n"
    "path = sys.argv[sys.argv.index('--report-path') + 1]\n"
    "json.dump(%s, open(path, 'w'))\n"
    "raise SystemExit(2)\n"
)


def test_a_leak_is_named_by_rule_and_place_and_never_by_value(
    log: EventLog, tmp_path: Path
) -> None:
    script = REPORT % '[{"RuleID": "aws-key", "StartLine": 1, "Secret": "AKIAXXXX"}]'

    result = judge(secrets(log, tmp_path, script))

    assert isinstance(result, Rejected)
    (finding,) = result.findings
    assert finding.describe() == "a possible secret was added (rule aws-key) [a.py at a.py:1]"
    assert "AKIA" not in finding.describe()


@pytest.mark.parametrize(
    "report",
    ["not json", "{}", "[]", '["x"]', '[{"RuleID": "r"}]', '[{"RuleID": "r", "StartLine": 99}]'],
)
def test_a_report_that_cannot_be_placed_still_rejects(
    log: EventLog, tmp_path: Path, report: str
) -> None:
    script = (
        "import sys\n"
        "path = sys.argv[sys.argv.index('--report-path') + 1]\n"
        f"open(path, 'w').write({report!r})\n"
        "raise SystemExit(2)\n"
    )

    result = judge(secrets(log, tmp_path, script))

    assert isinstance(result, Rejected)
    assert all("possible secret" in f.message for f in result.findings)


def test_a_leak_on_a_line_without_a_file_is_a_leak_of_the_diff() -> None:
    from forja.checks.secrets import _finding  # noqa: PLC0415

    assert _finding("r", None).describe() == "a possible secret was added (rule r) [diff]"
    assert _finding("r", AddedLine(None, 3, "x")).target == "diff"
    assert _finding("r", AddedLine("a.py", None, "x")).location is not None
    assert _finding("r", AddedLine("a.py", 0, "x")).target == "a.py"
    assert _finding("r", AddedLine("a.py", 0, "x")).location is None


def test_a_stale_report_from_an_earlier_scan_is_not_read(log: EventLog, tmp_path: Path) -> None:
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "gitleaks.json").write_text('[{"RuleID": "old", "StartLine": 1}]')

    result = judge(secrets(log, tmp_path, "raise SystemExit(2)"))

    assert isinstance(result, Rejected)
    assert [f.message for f in result.findings] == ["a possible secret was added"]
