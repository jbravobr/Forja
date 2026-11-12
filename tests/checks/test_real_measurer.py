"""The measurer on a real project: real ruff, pytest, lizard and git; only gitleaks is faked.

Slow on purpose: the same cases run in milliseconds with fake tools in test_measurer.py, so this
file only has to show that the real tools and the parsers agree.
"""

import asyncio
import json
from pathlib import Path

import pytest

from forja.core.events import OpenOperation
from forja.core.gates import CheckSettings, Effect, InconclusiveReason, Mode, Verdict
from forja.core.gates.models import Approved, Inconclusive, Rejected
from forja.core.gates.pipeline import PipelineOutcome
from forja.core.idempotency import Decision, Reconciliation
from forja.core.states import Level
from forja.orchestrator.ports import Baseline
from tests.checks.support import BLOCKING, SAMPLE, Scene, deep, sinks
from tests.core.idempotency.conftest import Repo

pytestmark = pytest.mark.slow


def ran(outcome: PipelineOutcome) -> list[str]:
    return [run.check_id for run in outcome.runs]


def findings(outcome: PipelineOutcome) -> list[str]:
    stopper = outcome.stopped_by
    assert stopper is not None and isinstance(stopper.result, Rejected)
    return [finding.describe() for finding in stopper.result.findings]


def judge(scene: Scene, **context: object) -> PipelineOutcome:
    return asyncio.run(scene.measurer.judge(scene.context(**context), sinks))  # type: ignore[arg-type]


def base_of(scene: Scene) -> Baseline:
    return asyncio.run(scene.measurer.baseline(scene.repo.path, sinks))


def integration(scene: Scene) -> PipelineOutcome:
    return asyncio.run(scene.measurer.integration(scene.repo.path, sinks))


def normalize(scene: Scene) -> None:
    asyncio.run(scene.measurer.format_worktree(scene.repo.path))


def reconcile(scene: Scene, operation: OpenOperation) -> Reconciliation:
    return asyncio.run(scene.measurer.reconcile(operation))


# -- a good attempt --------------------------------------------------------------------------------


def test_a_clean_change_inside_the_scope_passes_every_gate_in_order(scene: Scene) -> None:
    scene.write(
        "src/sample/mod.py",
        SAMPLE["src/sample/mod.py"] + "\n\ndef sub(a: int) -> int:\n    return -a\n",
    )

    outcome = judge(scene)

    assert outcome.verdict is Verdict.PASSED
    assert ran(outcome) == [
        "scope",
        "secrets",
        "format",
        "lint",
        "types",
        "build",
        "tests",
        "e2e",
        "test_data",
        "properties",
        "mutation",
        "complexity",
        "crap",
        "pass_through",
        "module_depth",
        "duplication",
        "dependency_cycles",
        "layer_rules",
        "package_metrics",
        "sast",
        "dependency_audit",
        "new_dependency",
        "differential",
        "voice",
        "comments",
    ]


# -- gate 1: scope ---------------------------------------------------------------------------------


def test_an_attempt_that_changed_nothing_is_rejected(scene: Scene) -> None:
    outcome = judge(scene)

    assert ran(outcome) == ["scope"]
    assert findings(outcome) == ["the attempt changed nothing [diff]"]


def test_a_file_outside_the_scope_stops_the_pipeline_at_once(scene: Scene) -> None:
    scene.write("src/sample/other.py", "def twice(a: int) -> int:\n    return a + a\n")

    outcome = judge(scene)

    assert ran(outcome) == ["scope"]
    assert findings(outcome) == ["outside the task scope [src/sample/other.py]"]


def test_tests_are_frozen_in_a_refactoring(scene: Scene) -> None:
    scene.write("tests/test_mod.py", "def test_nothing() -> None:\n    pass\n")

    outcome = judge(scene, scope=("src/sample/mod.py", "tests"))

    assert findings(outcome) == [
        "only production files may change in this stage (this is test) [tests/test_mod.py]"
    ]


def test_the_size_of_the_change_is_limited(scene: Scene) -> None:
    scene.write(
        "src/sample/mod.py",
        SAMPLE["src/sample/mod.py"] + "\n\ndef sub(a: int) -> int:\n    return -a\n",
    )

    outcome = judge(scene, max_lines=2)

    assert findings(outcome) == [
        "more lines changed than the task allows [diff] measured 4, limit 2"
    ]


# -- gate 2: secrets -------------------------------------------------------------------------------


def test_a_secret_in_the_added_lines_is_found_where_it_was_added(scene: Scene) -> None:
    scene.write("src/sample/mod.py", SAMPLE["src/sample/mod.py"] + '\n\nKEY = "FAKE-SECRET-1"\n')

    outcome = judge(scene)

    assert ran(outcome) == ["scope", "secrets"]
    assert findings(outcome) == [
        "a possible secret was added (rule fake-rule) [src/sample/mod.py at src/sample/mod.py:5]"
    ]
    assert "FAKE-SECRET" not in "".join(findings(outcome))


def test_a_secret_that_is_removed_does_not_reprove(repo: Repo, tmp_path: Path) -> None:
    scene = Scene(repo, tmp_path / "scene", real=True)
    scene.write("src/sample/mod.py", SAMPLE["src/sample/mod.py"] + '\nKEY = "FAKE-SECRET-1"\n')
    repo.sh("commit", "-am", "oops")

    scene.write("src/sample/mod.py", SAMPLE["src/sample/mod.py"])

    assert (judge(scene)).verdict is Verdict.PASSED


def test_a_missing_gitleaks_blocks_the_task_instead_of_passing(
    repo: Repo,
    tmp_path: Path,
) -> None:
    scene = Scene(repo, tmp_path / "scene", real=True, gitleaks=("no-such-gitleaks-anywhere",))
    scene.write("src/sample/mod.py", SAMPLE["src/sample/mod.py"] + "\n\nX = 1\n")

    outcome = judge(scene)

    stopper = outcome.stopped_by
    assert stopper is not None and stopper.effect is Effect.BLOCK_TASK
    assert isinstance(stopper.result, Inconclusive)
    assert stopper.result.reason is InconclusiveReason.TOOL_FAILED
    assert "no-such-gitleaks-anywhere" in stopper.result.detail


# -- gate 3: format and lint -----------------------------------------------------------------------


def test_code_not_in_the_canonical_format_is_formatted_before_the_gates_read_it(
    scene: Scene,
) -> None:
    scene.write("src/sample/mod.py", "def add( a,b ):\n  return a+b\n")

    outcome = judge(scene)

    formatted = (scene.repo.path / "src/sample/mod.py").read_text(encoding="utf-8")
    assert formatted == "def add(a, b):\n    return a + b\n"
    assert [run.result for run in outcome.runs if run.check_id == "format"] == [Approved()]


def test_code_that_does_not_parse_is_said_so(scene: Scene) -> None:
    scene.write("src/sample/mod.py", "def (:\n")

    assert findings(judge(scene)) == ["the file does not parse [src/sample/mod.py]"]


def test_a_new_lint_violation_is_rejected_with_its_count(scene: Scene) -> None:
    # a name nobody defined: the fixer that runs before the gates cannot repair it
    scene.write(
        "src/sample/mod.py",
        SAMPLE["src/sample/mod.py"] + "\n\ndef lost() -> int:\n    return missing\n",
    )

    outcome = judge(scene)

    assert ran(outcome) == ["scope", "secrets", "format", "lint"]
    assert findings(outcome) == ["1 new F821 violation(s) [src/sample/mod.py] measured 1, limit 0"]


def test_an_old_violation_does_not_reprove_a_change_that_leaves_it_alone(
    repo: Repo,
    tmp_path: Path,
) -> None:
    scene = Scene(repo, tmp_path / "scene", real=True)
    scene.write("src/sample/mod.py", "import os\n\n\n" + SAMPLE["src/sample/mod.py"])
    repo.sh("commit", "-am", "an old violation")

    scene.write(
        "src/sample/mod.py", "import os\n\n\n" + SAMPLE["src/sample/mod.py"] + "\n\nX = 1\n"
    )

    assert (judge(scene)).verdict is Verdict.PASSED


def test_a_renamed_file_keeps_the_violations_it_already_had(
    repo: Repo,
    tmp_path: Path,
) -> None:
    scene = Scene(repo, tmp_path / "scene", real=True)
    scene.write("src/sample/other.py", "import os\n\n\n" + SAMPLE["src/sample/other.py"])
    repo.sh("commit", "-am", "an old violation")

    repo.sh("mv", "src/sample/other.py", "src/sample/renamed.py")
    outcome = judge(scene, scope=("src/sample/other.py", "src/sample/renamed.py"))

    assert outcome.verdict is Verdict.PASSED


# -- gate 4 and 5: build and tests -----------------------------------------------------------------


def test_a_file_that_does_not_compile_fails_the_build_when_nothing_before_it_does(
    repo: Repo,
    tmp_path: Path,
) -> None:
    observing = {
        name: BLOCKING for name in ("scope", "secrets", "build", "tests", "complexity")
    } | {
        name: CheckSettings(Mode.OBSERVATION)
        for name in (
            "format",
            "lint",
            "types",
            "mutation",
            "crap",
            "pass_through",
            "module_depth",
            "duplication",
            "dependency_cycles",
            "layer_rules",
            "package_metrics",
            "sast",
            "dependency_audit",
            "new_dependency",
            "differential",
            "e2e",
            "test_data",
            "properties",
            "voice",
            "comments",
        )
    }
    scene = Scene(repo, tmp_path / "scene", real=True, settings=observing)
    scene.write("src/sample/mod.py", "def (:\n")

    outcome = judge(scene)

    assert ran(outcome) == ["scope", "secrets", "format", "lint", "types", "build"]
    assert findings(outcome) == ["the file does not compile [src/sample/mod.py]"]


def test_a_failing_test_is_rejected_by_name(scene: Scene) -> None:
    scene.write("src/sample/mod.py", "def add(a: int, b: int) -> int:\n    return a - b\n")

    outcome = judge(scene)

    assert ran(outcome) == ["scope", "secrets", "format", "lint", "types", "build", "tests"]
    assert findings(outcome) == ["test failed [tests/test_mod.py::test_add]"]


def test_a_test_the_base_showed_to_be_unstable_does_not_reprove(scene: Scene) -> None:
    scene.write("src/sample/mod.py", "def add(a: int, b: int) -> int:\n    return a - b\n")
    (scene.work / "run").mkdir(parents=True)
    (scene.work / "run" / "base.json").write_text(
        json.dumps(
            {
                "unstable": ["tests/test_mod.py::test_add"],
                "passing": ["tests/test_mod.py::test_add"],
            }
        ),
        encoding="utf-8",
    )

    assert (judge(scene)).verdict is Verdict.PASSED


def test_a_tool_that_overruns_its_time_leaves_the_gate_inconclusive(
    repo: Repo,
    tmp_path: Path,
) -> None:
    scene = Scene(repo, tmp_path / "scene", real=True, timeouts={"tests": 0.01})
    scene.write("src/sample/mod.py", SAMPLE["src/sample/mod.py"] + "\n\nX = 1\n")

    outcome = judge(scene)

    stopper = outcome.stopped_by
    assert stopper is not None and stopper.check_id == "tests"
    assert isinstance(stopper.result, Inconclusive)
    assert stopper.result.reason is InconclusiveReason.TIMED_OUT
    assert stopper.effect is Effect.BLOCK_TASK


# -- gate 8: complexity ----------------------------------------------------------------------------


def test_a_function_above_the_limit_is_rejected_with_the_numbers(scene: Scene) -> None:
    scene.write("src/sample/mod.py", SAMPLE["src/sample/mod.py"] + "\n\n" + deep("branchy", 20))

    outcome = judge(scene)

    assert ran(outcome) == [
        "scope",
        "secrets",
        "format",
        "lint",
        "types",
        "build",
        "tests",
        "e2e",
        "test_data",
        "properties",
        "mutation",
        "complexity",
    ]
    assert findings(outcome) == [
        "function branchy is too complex [src/sample/mod.py::branchy] measured 21, limit 15"
    ]


def test_a_limit_from_the_policy_replaces_the_default(
    repo: Repo,
    tmp_path: Path,
) -> None:
    values = {"complexity": {"thresholds": {"python": {"max_ccn": 3}}}}
    scene = Scene(repo, tmp_path / "scene", real=True, values=values)
    scene.write("src/sample/mod.py", SAMPLE["src/sample/mod.py"] + "\n\n" + deep("branchy", 4))

    assert findings(judge(scene)) == [
        "function branchy is too complex [src/sample/mod.py::branchy] measured 5, limit 3"
    ]


def test_old_complexity_that_did_not_grow_is_left_alone(
    repo: Repo,
    tmp_path: Path,
) -> None:
    scene = Scene(repo, tmp_path / "scene", real=True)
    scene.write("src/sample/mod.py", SAMPLE["src/sample/mod.py"] + "\n\n" + deep("branchy", 20))
    repo.sh("commit", "-am", "complex already")

    scene.write(
        "src/sample/mod.py",
        SAMPLE["src/sample/mod.py"] + "\n\n" + deep("branchy", 20) + "\n\nX = 1\n",
    )
    unchanged = judge(scene)
    scene.write("src/sample/mod.py", SAMPLE["src/sample/mod.py"] + "\n\n" + deep("branchy", 22))
    worse = judge(scene)

    assert unchanged.verdict is Verdict.PASSED
    assert findings(worse) == [
        "function branchy is too complex [src/sample/mod.py::branchy] measured 23, limit 15"
    ]


def test_missing_lizard_is_declared_as_a_gap_not_a_failure(
    repo: Repo,
    tmp_path: Path,
) -> None:
    scene = Scene(repo, tmp_path / "scene", real=True)
    scene.measurer = scene.make_measurer()
    scene.measurer._lizard = ("no-such-lizard-anywhere",)  # noqa: SLF001
    scene.write("src/sample/mod.py", SAMPLE["src/sample/mod.py"] + "\n\nX = 1\n")

    outcome = judge(scene)

    assert outcome.verdict is Verdict.PASSED
    assert [r.check_id for r in outcome.assumptions] == ["complexity"]


# -- the base --------------------------------------------------------------------------------------


def test_a_green_base_is_measured_for_the_numbers_the_summary_will_show(scene: Scene) -> None:
    measured = base_of(scene)

    assert measured.ok and measured.problems == ()
    assert measured.metrics == {
        "tests": 1.0,
        "unstable_tests": 0.0,
        "lint_violations": 0.0,
        "max_ccn": 1.0,
        "functions": 2.0,
        "dependency_cycles": 0.0,
        "dependency_edges": 0.0,
        "max_distance": 1.0,
    }


def test_a_base_whose_tests_fail_is_not_green(scene: Scene) -> None:
    scene.write("src/sample/mod.py", "def add(a: int, b: int) -> int:\n    return a - b\n")

    measured = base_of(scene)

    assert not measured.ok
    assert measured.problems == ("tests: tests/test_mod.py::test_add fails on the base",)


def test_a_base_that_does_not_compile_is_not_green(scene: Scene) -> None:
    scene.write("src/sample/mod.py", "def (:\n")

    measured = base_of(scene)

    assert not measured.ok
    assert measured.problems[0] == "build: src/sample/mod.py does not compile"


def test_a_test_that_changes_its_mind_between_two_runs_goes_to_quarantine(
    scene: Scene, tmp_path: Path
) -> None:
    flag = tmp_path / "flag"
    scene.write(
        "tests/test_flaky.py",
        "from pathlib import Path\n\n\ndef test_flaky() -> None:\n"
        f"    flag = Path({str(flag)!r})\n"
        "    first = not flag.exists()\n    flag.write_text('seen')\n    assert not first\n",
    )

    measured = base_of(scene)

    assert measured.ok
    assert measured.metrics["unstable_tests"] == 1.0
    saved = json.loads((scene.work / "run" / "base.json").read_text(encoding="utf-8"))
    assert saved == {
        "unstable": ["tests/test_flaky.py::test_flaky"],
        "passing": ["tests/test_mod.py::test_add"],
    }


def test_the_second_measurement_does_not_rewrite_what_the_first_found(scene: Scene) -> None:
    base_of(scene)
    first = (scene.work / "run" / "base.json").read_text(encoding="utf-8")
    scene.write("tests/test_more.py", "def test_more() -> None:\n    assert True\n")

    base_of(scene)

    assert (scene.work / "run" / "base.json").read_text(encoding="utf-8") == first


# -- integration and normalization -----------------------------------------------------------------


def test_integration_runs_only_build_and_tests(scene: Scene) -> None:
    outcome = integration(scene)

    assert ran(outcome) == ["build", "tests"] and outcome.verdict is Verdict.PASSED


def test_integration_rejects_work_that_breaks_the_tests(scene: Scene) -> None:
    scene.write("src/sample/mod.py", "def add(a: int, b: int) -> int:\n    return a - b\n")

    outcome = integration(scene)

    assert findings(outcome) == ["test failed [tests/test_mod.py::test_add]"]


def test_normalization_applies_the_safe_fixes_and_the_formatter(scene: Scene) -> None:
    scene.write("src/sample/mod.py", "import os\n\n\ndef add( a,b ):\n  return a+b\n")

    normalize(scene)

    text = (scene.repo.path / "src/sample/mod.py").read_text(encoding="utf-8")
    assert text == "def add(a, b):\n    return a + b\n"


def test_a_tool_run_is_reconciled_by_running_it_again(scene: Scene) -> None:
    operation = OpenOperation("run_tool", "run_tool:t1-a1:tests:1", Level.ATTEMPT, "t1-a1", 1)

    decision = reconcile(scene, operation)

    assert decision.decision is Decision.REDO


# -- what an attempt may not use to get past a gate ------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    [
        # one that hides a violation: the fixer takes out a noqa that hides nothing
        "import os  # noqa: F401",
        "import os  # NOQA",
        "# ruff: noqa",
        "# fmt: off",
        "x = 1  # fmt: skip",
        'KEY = "x"  # gitleaks:allow',
        "def f():  # lizard forgives",
        "# lizard: ignore",
        "# GENERATED CODE",
        "# yapf: disable",
        "x = 1  # nosem",
        "x = 1  # nosemgrep",
    ],
)
def test_a_comment_that_tells_a_tool_to_look_away_is_rejected(scene: Scene, marker: str) -> None:
    scene.write("src/sample/mod.py", SAMPLE["src/sample/mod.py"] + f"\n\n{marker}\n")

    outcome = judge(scene)

    assert ran(outcome) == ["scope"]
    assert findings(outcome) == ["a comment that silences a tool was added [src/sample/mod.py]"]


def test_a_file_the_ignore_rules_hide_is_rejected_because_the_tests_would_still_see_it(
    scene: Scene,
) -> None:
    scene.write(".gitignore", "*.local\n")
    scene.repo.sh("add", ".gitignore")
    scene.repo.sh("commit", "-m", "ignore local files")
    scene.write("src/sample/hidden.local", "secret helper\n")
    scene.write("src/sample/mod.py", SAMPLE["src/sample/mod.py"] + "\n\nX = 1\n")

    outcome = judge(scene)

    assert findings(outcome) == ["a file git ignores was added [src/sample/hidden.local]"]


def test_what_tools_leave_behind_is_not_a_hidden_file(scene: Scene) -> None:
    scene.write(".gitignore", "__pycache__/\n*.pyc\n.ruff_cache/\n")
    scene.repo.sh("add", ".gitignore")
    scene.repo.sh("commit", "-m", "ignore caches")
    scene.write("src/sample/__pycache__/mod.cpython-312.pyc", "compiled")
    scene.write(".ruff_cache/note", "cache")
    scene.write("src/sample/mod.py", SAMPLE["src/sample/mod.py"] + "\n\nX = 1\n")

    assert judge(scene).verdict is Verdict.PASSED


def test_a_test_that_passed_on_the_base_and_is_gone_now_is_a_failure(scene: Scene) -> None:
    scene.write("src/sample/mod.py", SAMPLE["src/sample/mod.py"] + "\n\nX = 1\n")
    (scene.work / "run").mkdir(parents=True)
    (scene.work / "run" / "base.json").write_text(
        json.dumps(
            {
                "unstable": [],
                "passing": ["tests/test_mod.py::test_add", "tests/test_mod.py::test_deleted"],
            }
        ),
        encoding="utf-8",
    )

    outcome = judge(scene)

    assert findings(outcome) == [
        "a test that passed on the base is missing or did not pass "
        "[tests/test_mod.py::test_deleted]"
    ]


def test_a_stable_test_that_now_fails_is_named_once(scene: Scene) -> None:
    scene.write("src/sample/mod.py", "def add(a: int, b: int) -> int:\n    return a - b\n")
    (scene.work / "run").mkdir(parents=True)
    (scene.work / "run" / "base.json").write_text(
        json.dumps({"unstable": [], "passing": ["tests/test_mod.py::test_add"]}), encoding="utf-8"
    )

    assert findings(judge(scene)) == ["test failed [tests/test_mod.py::test_add]"]


def test_the_base_remembers_which_tests_passed_both_times(scene: Scene) -> None:
    base_of(scene)

    saved = json.loads((scene.work / "run" / "base.json").read_text(encoding="utf-8"))
    assert saved == {"unstable": [], "passing": ["tests/test_mod.py::test_add"]}
