"""The measurer and every check in it, on a real git repository with fake tools (milliseconds).

The fake tools read markers from the files (see fake_tools.py) and print what the real ones print,
so these tests pin the logic of the gates. test_real_measurer.py runs a few of the same cases with
the real tools to show the parsers and the tools still agree.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from forja.core.events import OpenOperation
from forja.core.gates import CheckSettings, Effect, InconclusiveReason, Mode, Verdict
from forja.core.gates.models import Inconclusive, Rejected
from forja.core.gates.pipeline import PipelineOutcome
from forja.core.idempotency import Decision, Reconciliation
from forja.core.states import Level
from forja.orchestrator.ports import Baseline
from tests.checks.support import BLOCKING, RUN, SAMPLE, Scene, deep
from tests.core.idempotency.conftest import Repo

GRAPH = {"dependency_cycles": 0.0, "dependency_edges": 0.0, "max_distance": 1.0}
MOD = SAMPLE["src/sample/mod.py"]
OTHER = SAMPLE["src/sample/other.py"]
EVERY_GATE = [
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


def ran(outcome: PipelineOutcome) -> list[str]:
    return [run.check_id for run in outcome.runs]


def findings(outcome: PipelineOutcome) -> list[str]:
    stopper = outcome.stopped_by
    assert stopper is not None and isinstance(stopper.result, Rejected)
    return [finding.describe() for finding in stopper.result.findings]


def judge(scene: Scene, **context: object) -> PipelineOutcome:
    return asyncio.run(scene.measurer.judge(scene.context(**context), scene.recorder.sinks))  # type: ignore[arg-type]


def base_of(scene: Scene) -> Baseline:
    return asyncio.run(scene.measurer.baseline(scene.repo.path, scene.recorder.sinks))


def integration(scene: Scene) -> PipelineOutcome:
    return asyncio.run(scene.measurer.integration(scene.repo.path, scene.recorder.sinks))


def reconcile(scene: Scene, operation: OpenOperation) -> Reconciliation:
    return asyncio.run(scene.measurer.reconcile(operation))


def edit_mod(scene: Scene, extra: str) -> None:
    scene.write("src/sample/mod.py", MOD + "\n\n" + extra)


def commit_mod(scene: Scene, text: str) -> None:
    scene.write("src/sample/mod.py", text)
    scene.repo.sh("commit", "-am", "earlier work")


def write_base(scene: Scene, unstable: list[str], passing: list[str]) -> None:
    (scene.work / "run").mkdir(parents=True, exist_ok=True)
    document = {"unstable": unstable, "passing": passing}
    (scene.work / "run" / "base.json").write_text(json.dumps(document), encoding="utf-8")


# -- a good attempt --------------------------------------------------------------------------------


def test_a_clean_change_inside_the_scope_passes_every_gate_in_order(scene: Scene) -> None:
    edit_mod(scene, "def sub(a: int) -> int:\n    return -a\n")

    outcome = judge(scene)

    assert outcome.verdict is Verdict.PASSED
    assert ran(outcome) == EVERY_GATE


# -- gate 1: scope ---------------------------------------------------------------------------------


def test_an_attempt_that_changed_nothing_is_rejected(scene: Scene) -> None:
    outcome = judge(scene)

    assert ran(outcome) == ["scope"]
    assert findings(outcome) == ["the attempt changed nothing [diff]"]


def test_a_file_outside_the_scope_stops_the_pipeline_at_once(scene: Scene) -> None:
    scene.write("src/sample/other.py", OTHER + "\n\nX = 1\n")

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
    edit_mod(scene, "def sub(a: int) -> int:\n    return -a\n")

    outcome = judge(scene, max_lines=2)

    assert findings(outcome) == [
        "more lines changed than the task allows [diff] measured 4, limit 2"
    ]


@pytest.mark.parametrize("marker", ["x = 1  # noqa: E501", "# fmt: off", "# lizard forgives"])
def test_a_comment_that_tells_a_tool_to_look_away_is_rejected(scene: Scene, marker: str) -> None:
    edit_mod(scene, marker + "\n")

    outcome = judge(scene)

    assert ran(outcome) == ["scope"]
    assert findings(outcome) == ["a comment that silences a tool was added [src/sample/mod.py]"]


def test_a_file_the_ignore_rules_hide_is_rejected(scene: Scene) -> None:
    scene.write(".gitignore", "*.local\n")
    scene.repo.sh("add", ".gitignore")
    scene.repo.sh("commit", "-m", "ignore local files")
    scene.write("src/sample/hidden.local", "secret helper\n")
    edit_mod(scene, "X = 1\n")

    assert findings(judge(scene)) == ["a file git ignores was added [src/sample/hidden.local]"]


def test_what_tools_leave_behind_is_not_a_hidden_file(scene: Scene) -> None:
    scene.write(".gitignore", "__pycache__/\n*.pyc\n.ruff_cache/\n")
    scene.repo.sh("add", ".gitignore")
    scene.repo.sh("commit", "-m", "ignore caches")
    scene.write("src/sample/__pycache__/mod.cpython-312.pyc", "compiled")
    scene.write(".ruff_cache/note", "cache")
    edit_mod(scene, "X = 1\n")

    assert judge(scene).verdict is Verdict.PASSED


def test_a_compiled_file_outside_the_cache_directory_is_a_hidden_module(scene: Scene) -> None:
    scene.write(".gitignore", "*.pyc\n")
    scene.repo.sh("add", ".gitignore")
    scene.repo.sh("commit", "-m", "ignore compiled files")
    scene.write("src/sample/stray.pyc", "compiled")
    edit_mod(scene, "X = 1\n")

    assert findings(judge(scene)) == ["a file git ignores was added [src/sample/stray.pyc]"]


# -- gate 2: secrets -------------------------------------------------------------------------------


def test_a_secret_in_the_added_lines_is_found_where_it_was_added(scene: Scene) -> None:
    edit_mod(scene, 'KEY = "FAKE-SECRET-1"\n')

    outcome = judge(scene)

    assert ran(outcome) == ["scope", "secrets"]
    assert findings(outcome) == [
        "a possible secret was added (rule fake-rule) [src/sample/mod.py at src/sample/mod.py:5]"
    ]
    assert "FAKE-SECRET" not in "".join(findings(outcome))


def test_a_secret_that_is_removed_does_not_reprove(scene: Scene) -> None:
    commit_mod(scene, MOD + '\nKEY = "FAKE-SECRET-1"\n')

    scene.write("src/sample/mod.py", MOD)

    assert judge(scene).verdict is Verdict.PASSED


def test_a_missing_gitleaks_blocks_the_task_instead_of_passing(repo: Repo, tmp_path: Path) -> None:
    scene = Scene(repo, tmp_path / "scene", gitleaks=("no-such-gitleaks-anywhere",))
    edit_mod(scene, "X = 1\n")

    outcome = judge(scene)

    stopper = outcome.stopped_by
    assert stopper is not None and stopper.effect is Effect.BLOCK_TASK
    assert isinstance(stopper.result, Inconclusive)
    assert stopper.result.reason is InconclusiveReason.TOOL_FAILED
    assert stopper.result.detail == "secrets: no-such-gitleaks-anywhere could not start"


# -- gate 3: format and lint -----------------------------------------------------------------------


def test_code_not_in_the_canonical_format_is_rejected(scene: Scene) -> None:
    edit_mod(scene, "X = 1  # UNFORMATTED\n")

    outcome = judge(scene)

    assert ran(outcome) == ["scope", "secrets", "format"]
    assert findings(outcome) == ["the file is not in the canonical format [src/sample/mod.py]"]


def test_code_that_does_not_parse_is_said_so(scene: Scene) -> None:
    edit_mod(scene, "X = 1  # SYNTAX\n")

    assert findings(judge(scene)) == ["the file does not parse [src/sample/mod.py]"]


def test_formatting_is_only_asked_of_the_files_that_still_exist(scene: Scene) -> None:
    scene.repo.sh("rm", "src/sample/other.py")

    outcome = judge(scene, scope=("src/sample/other.py",))

    assert outcome.verdict is Verdict.PASSED
    assert ran(outcome) == EVERY_GATE


def test_a_new_lint_violation_is_rejected_with_its_count(scene: Scene) -> None:
    edit_mod(scene, "import os  # LINT:F401\n")

    outcome = judge(scene)

    assert ran(outcome) == ["scope", "secrets", "format", "lint"]
    assert findings(outcome) == ["1 new F401 violation(s) [src/sample/mod.py] measured 1, limit 0"]


def test_two_new_violations_of_one_rule_are_counted_together(scene: Scene) -> None:
    edit_mod(scene, "import os  # LINT:F401\nimport sys  # LINT:F401\n")

    assert findings(judge(scene)) == [
        "2 new F401 violation(s) [src/sample/mod.py] measured 2, limit 0"
    ]


def test_one_more_violation_than_the_base_had_is_rejected(scene: Scene) -> None:
    commit_mod(scene, MOD + "\n\nimport os  # LINT:F401\n")

    scene.write("src/sample/mod.py", MOD + "\n\nimport os  # LINT:F401\nimport sys  # LINT:F401\n")

    assert findings(judge(scene)) == [
        "1 new F401 violation(s) [src/sample/mod.py] measured 2, limit 1"
    ]


def test_an_old_violation_does_not_reprove_a_change_that_leaves_it_alone(scene: Scene) -> None:
    commit_mod(scene, MOD + "\n\nimport os  # LINT:F401\n")

    scene.write("src/sample/mod.py", MOD + "\n\nimport os  # LINT:F401\n\n\nX = 1\n")

    assert judge(scene).verdict is Verdict.PASSED


def test_a_renamed_file_keeps_the_violations_it_already_had(scene: Scene) -> None:
    scene.write("src/sample/other.py", OTHER + "\n\nimport os  # LINT:F401\n")
    scene.repo.sh("commit", "-am", "an old violation")

    scene.repo.sh("mv", "src/sample/other.py", "src/sample/renamed.py")
    outcome = judge(scene, scope=("src/sample/other.py", "src/sample/renamed.py"))

    assert outcome.verdict is Verdict.PASSED


def test_a_violation_in_a_new_file_is_new(scene: Scene) -> None:
    scene.write("src/sample/fresh.py", "import os  # LINT:F401\n")

    outcome = judge(scene, scope=("src/sample",))

    assert findings(outcome) == [
        "1 new F401 violation(s) [src/sample/fresh.py] measured 1, limit 0"
    ]


def test_each_rule_is_counted_on_its_own(scene: Scene) -> None:
    commit_mod(scene, MOD + "\n\nimport os  # LINT:F401\n")

    scene.write("src/sample/mod.py", MOD + "\n\nimport os  # LINT:F401\nX = 1  # LINT:E501\n")

    assert findings(judge(scene)) == [
        "1 new E501 violation(s) [src/sample/mod.py] measured 1, limit 0"
    ]


# -- gate 4 and 5: build and tests -----------------------------------------------------------------


def observing(*names: str) -> dict[str, CheckSettings]:
    watched = {
        name: CheckSettings(Mode.OBSERVATION)
        for name in (
            *names,
            "mutation",
            "crap",
            "pass_through",
            "module_depth",
            "duplication",
            "types",
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
    return {n: BLOCKING for n in ("scope", "secrets", "build", "tests", "complexity")} | watched


def blocking_on_types(repo: Repo, tmp_path: Path) -> Scene:
    settings = observing("format", "lint") | {"types": BLOCKING}
    return Scene(repo, tmp_path / "scene", settings=settings)


def test_a_new_type_error_is_rejected_when_the_repository_blocks_on_types(
    repo: Repo, tmp_path: Path
) -> None:
    scene = blocking_on_types(repo, tmp_path)
    edit_mod(scene, "X = 1  # TYPE:arg-type\n")

    outcome = judge(scene)

    assert ran(outcome) == ["scope", "secrets", "format", "lint", "types"]
    assert findings(outcome) == ["1 new arg-type error(s) [src/sample/mod.py] measured 1, limit 0"]


def test_a_type_error_the_base_already_had_does_not_reprove(repo: Repo, tmp_path: Path) -> None:
    scene = blocking_on_types(repo, tmp_path)
    commit_mod(scene, MOD + "\n\nX = 1  # TYPE:arg-type\n")

    scene.write("src/sample/mod.py", MOD + "\n\nX = 1  # TYPE:arg-type\n\n\nY = 2\n")

    assert judge(scene).verdict is Verdict.PASSED


def test_a_file_that_does_not_compile_fails_the_build(repo: Repo, tmp_path: Path) -> None:
    scene = Scene(repo, tmp_path / "scene", settings=observing("format", "lint"))
    edit_mod(scene, "X = 1  # NOCOMPILE\n")

    outcome = judge(scene)

    assert ran(outcome) == ["scope", "secrets", "format", "lint", "types", "build"]
    assert findings(outcome) == ["the file does not compile [src/sample/mod.py]"]


def test_a_failing_test_is_rejected_by_name(scene: Scene) -> None:
    edit_mod(scene, "X = 1  # BREAKS\n")

    outcome = judge(scene)

    assert ran(outcome) == ["scope", "secrets", "format", "lint", "types", "build", "tests"]
    assert findings(outcome) == ["test failed [tests/test_mod.py::test_add]"]


def test_a_test_the_base_showed_to_be_unstable_does_not_reprove(scene: Scene) -> None:
    edit_mod(scene, "X = 1  # BREAKS\n")
    write_base(scene, ["tests/test_mod.py::test_add"], ["tests/test_mod.py::test_add"])

    assert judge(scene).verdict is Verdict.PASSED


def test_a_test_that_passed_on_the_base_and_is_gone_now_is_a_failure(scene: Scene) -> None:
    edit_mod(scene, "X = 1\n")
    write_base(scene, [], ["tests/test_mod.py::test_add", "tests/test_mod.py::test_deleted"])

    assert findings(judge(scene)) == [
        "a test that passed on the base is missing or did not pass "
        "[tests/test_mod.py::test_deleted]"
    ]


def test_a_stable_test_that_now_fails_is_named_once(scene: Scene) -> None:
    edit_mod(scene, "X = 1  # BREAKS\n")
    write_base(scene, [], ["tests/test_mod.py::test_add"])

    assert findings(judge(scene)) == ["test failed [tests/test_mod.py::test_add]"]


def test_a_run_with_no_tests_passes_when_the_base_had_none(scene: Scene) -> None:
    scene.repo.sh("rm", "tests/test_mod.py")
    edit_mod(scene, "X = 1\n")

    assert judge(scene, scope=("src/sample/mod.py", "tests")).verdict is Verdict.REJECTED
    write_base(scene, [], [])
    scene.write("tests/test_mod.py", "")
    assert judge(scene, scope=("src/sample/mod.py", "tests")).verdict is Verdict.REJECTED


def test_a_tool_that_overruns_its_time_leaves_the_gate_inconclusive(
    repo: Repo, tmp_path: Path
) -> None:
    scene = Scene(repo, tmp_path / "scene", timeouts={"tests": 0.01})
    edit_mod(scene, "X = 1\n")

    outcome = judge(scene)

    stopper = outcome.stopped_by
    assert stopper is not None and stopper.check_id == "tests"
    assert isinstance(stopper.result, Inconclusive)
    assert stopper.result.reason is InconclusiveReason.TIMED_OUT
    assert stopper.effect is Effect.BLOCK_TASK


# -- gate 8: complexity ----------------------------------------------------------------------------


def test_a_function_above_the_limit_is_rejected_with_the_numbers(scene: Scene) -> None:
    edit_mod(scene, deep("branchy", 20))

    outcome = judge(scene)

    assert ran(outcome) == EVERY_GATE[:-13]  # the gates after the one that stopped it do not run
    assert findings(outcome) == [
        "function branchy is too complex [src/sample/mod.py::branchy] measured 21, limit 15"
    ]


def test_a_function_exactly_at_the_limit_is_fine(scene: Scene) -> None:
    edit_mod(scene, deep("branchy", 14))

    assert judge(scene).verdict is Verdict.PASSED


def test_a_limit_from_the_policy_replaces_the_default(repo: Repo, tmp_path: Path) -> None:
    values = {"complexity": {"thresholds": {"python": {"max_ccn": 3}}}}
    scene = Scene(repo, tmp_path / "scene", values=values)
    edit_mod(scene, deep("branchy", 4))

    assert findings(judge(scene)) == [
        "function branchy is too complex [src/sample/mod.py::branchy] measured 5, limit 3"
    ]


def test_old_complexity_that_did_not_grow_is_left_alone(scene: Scene) -> None:
    commit_mod(scene, MOD + "\n\n" + deep("branchy", 20))

    scene.write("src/sample/mod.py", MOD + "\n\n" + deep("branchy", 20) + "\n\nX = 1\n")
    unchanged = judge(scene)
    scene.write("src/sample/mod.py", MOD + "\n\n" + deep("branchy", 22))
    worse = judge(scene)

    assert unchanged.verdict is Verdict.PASSED
    assert findings(worse) == [
        "function branchy is too complex [src/sample/mod.py::branchy] measured 23, limit 15"
    ]


def test_complexity_that_fell_but_is_still_above_the_limit_is_left_alone(scene: Scene) -> None:
    commit_mod(scene, MOD + "\n\n" + deep("branchy", 30))

    scene.write("src/sample/mod.py", MOD + "\n\n" + deep("branchy", 20))

    assert judge(scene).verdict is Verdict.PASSED


def test_test_files_are_not_measured_for_complexity(scene: Scene) -> None:
    edit_mod(scene, "X = 1\n")
    scene.write("tests/test_mod.py", scene_text_with_a_deep_test())

    outcome = judge(scene, scope=("src/sample/mod.py", "tests"))

    assert ran(outcome) == ["scope"]


def scene_text_with_a_deep_test() -> str:
    return SAMPLE["tests/test_mod.py"] + "\n\n" + deep("test_deep", 30)


def test_missing_lizard_is_declared_as_a_gap_not_a_failure(scene: Scene) -> None:
    scene.measurer._lizard = ("no-such-lizard-anywhere",)  # noqa: SLF001
    edit_mod(scene, "X = 1\n")

    outcome = judge(scene)

    assert outcome.verdict is Verdict.PASSED
    assert [r.check_id for r in outcome.assumptions] == ["complexity"]


def test_lizard_that_exits_badly_is_a_gap_too(scene: Scene) -> None:
    scene.measurer._lizard = (  # noqa: SLF001
        *scene.measurer._lizard[:2],  # noqa: SLF001
        "nonsense",
    )
    edit_mod(scene, "X = 1\n")

    outcome = judge(scene)

    assert [r.check_id for r in outcome.assumptions] == ["complexity"]
    (gap,) = outcome.assumptions
    assert isinstance(gap.result, Inconclusive)
    assert gap.result.detail == "complexity: lizard ended with 1"


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
        **GRAPH,
    }


def test_the_numbers_count_what_is_there(scene: Scene) -> None:
    scene.write("src/sample/mod.py", MOD + "\n\nimport os  # LINT:F401\n\n" + deep("branchy", 6))
    scene.write("tests/test_more.py", "def test_more() -> None:\n    pass\n")

    assert base_of(scene).metrics == {
        "tests": 2.0,
        "unstable_tests": 0.0,
        "lint_violations": 1.0,
        "max_ccn": 7.0,
        "functions": 3.0,
        **GRAPH,
    }


def test_the_numbers_of_a_stage_are_the_base_of_the_run_beside_the_worktree_of_the_attempt(
    scene: Scene,
) -> None:
    base_of(scene)  # writes what the run started from
    scene.write("src/sample/mod.py", MOD + "\n\nimport os  # LINT:F401\n\n" + deep("branchy", 6))

    numbers = asyncio.run(scene.measurer.stage_metrics(scene.context(), scene.recorder.sinks))

    assert numbers == (
        ("dependency_cycles", 0.0, 0.0),
        ("dependency_edges", 0.0, 0.0),
        ("functions", 2.0, 3.0),
        ("lint_violations", 0.0, 1.0),
        ("max_ccn", 1.0, 7.0),
        ("max_distance", 1.0, 1.0),
        ("tests", 1.0, None),  # counting the tests would take running them again
        ("unstable_tests", 0.0, None),
    )


def test_a_run_whose_base_was_not_measured_still_says_what_the_stage_has(scene: Scene) -> None:
    numbers = asyncio.run(scene.measurer.stage_metrics(scene.context(), scene.recorder.sinks))

    assert dict((name, (before, after)) for name, before, after in numbers) == {
        "lint_violations": (None, 0.0),
        "max_ccn": (None, 1.0),
        "functions": (None, 2.0),
        **{name: (None, value) for name, value in GRAPH.items()},
    }


def test_a_base_whose_tests_fail_is_not_green(scene: Scene) -> None:
    scene.write("src/sample/mod.py", MOD + "\n# BREAKS\n")

    measured = base_of(scene)

    assert not measured.ok
    assert measured.problems == ("tests: tests/test_mod.py::test_add fails on the base",)


def test_a_base_that_does_not_compile_is_not_green(scene: Scene) -> None:
    scene.write("src/sample/mod.py", MOD + "\n# NOCOMPILE\n")

    measured = base_of(scene)

    assert not measured.ok
    assert measured.problems[0] == "build: src/sample/mod.py does not compile"


def test_a_tool_that_does_not_run_on_the_base_makes_it_not_green(
    repo: Repo, tmp_path: Path
) -> None:
    scene = Scene(repo, tmp_path / "scene", timeouts={"build": 0.01})

    measured = base_of(scene)

    assert not measured.ok
    assert measured.problems == (
        f"build: {Path(cast(Any, scene.adapter.plan).__self__._python[0]).name} timed out",
    )


def test_a_test_that_changes_its_mind_between_two_runs_goes_to_quarantine(scene: Scene) -> None:
    scene.write("tests/test_flaky.py", "def test_flaky() -> None:  # FLAKY\n    pass\n")

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
    scene.write("tests/test_more.py", "def test_more() -> None:\n    pass\n")

    base_of(scene)

    assert (scene.work / "run" / "base.json").read_text(encoding="utf-8") == first


def test_the_base_remembers_which_tests_passed_both_times(scene: Scene) -> None:
    base_of(scene)

    saved = json.loads((scene.work / "run" / "base.json").read_text(encoding="utf-8"))
    assert saved == {"unstable": [], "passing": ["tests/test_mod.py::test_add"]}


def test_a_base_file_that_is_damaged_is_the_same_as_none(scene: Scene) -> None:
    (scene.work / "run").mkdir(parents=True)
    (scene.work / "run" / "base.json").write_text("{not json", encoding="utf-8")
    edit_mod(scene, "X = 1  # BREAKS\n")

    assert findings(judge(scene)) == ["test failed [tests/test_mod.py::test_add]"]


# -- integration and normalization -----------------------------------------------------------------


def test_integration_runs_only_build_and_tests(scene: Scene) -> None:
    outcome = integration(scene)

    assert ran(outcome) == ["build", "tests"] and outcome.verdict is Verdict.PASSED


def test_integration_rejects_work_that_breaks_the_tests(scene: Scene) -> None:
    edit_mod(scene, "X = 1  # BREAKS\n")

    assert findings(integration(scene)) == ["test failed [tests/test_mod.py::test_add]"]


def test_integration_holds_a_test_that_vanished_against_the_base(scene: Scene) -> None:
    write_base(scene, [], ["tests/test_mod.py::test_add", "tests/test_gone.py::test_gone"])

    assert findings(integration(scene)) == [
        "a test that passed on the base is missing or did not pass [tests/test_gone.py::test_gone]"
    ]


def test_a_tool_run_is_reconciled_by_running_it_again(scene: Scene) -> None:
    operation = OpenOperation("run_tool", "run_tool:t1-a1:tests:1", Level.ATTEMPT, "t1-a1", 1)

    assert reconcile(scene, operation).decision is Decision.REDO


# -- more about the base and the numbers -----------------------------------------------------------


def test_the_base_file_is_written_with_its_keys_in_order_and_in_a_directory_made_for_it(
    scene: Scene,
) -> None:
    deep_dir = scene.work / "one" / "two" / "three"
    scene.measurer = scene.make_measurer(workdir=deep_dir)

    base_of(scene)

    text = (deep_dir / "base.json").read_text(encoding="utf-8")
    assert text == '{"passing": ["tests/test_mod.py::test_add"], "unstable": []}'


def test_a_base_that_fails_in_two_ways_lists_the_build_first_and_then_the_tests(
    scene: Scene,
) -> None:
    scene.write("src/sample/mod.py", MOD + "\n# NOCOMPILE\n# BREAKS\n")

    measured = base_of(scene)

    assert measured.problems == (
        "build: src/sample/mod.py does not compile",
        "tests: tests/test_mod.py::test_add fails on the base",
    )


def test_a_tool_that_does_not_run_makes_the_base_not_green_in_so_many_words(
    repo: Repo, tmp_path: Path
) -> None:
    scene = Scene(repo, tmp_path / "scene", timeouts={"build": 0.01})

    measured = base_of(scene)

    assert measured.ok is False
    assert measured.problems == (f"build: {Path(sys.executable).name} timed out",)
    assert measured.metrics == {}


def test_a_test_listed_in_only_one_of_the_two_runs_is_unstable(scene: Scene) -> None:
    scene.write("tests/test_once.py", "def test_once() -> None:  # VANISHES\n    pass\n")

    measured = base_of(scene)

    assert measured.metrics["unstable_tests"] == 1.0
    saved = json.loads((scene.work / "run" / "base.json").read_text(encoding="utf-8"))
    assert saved["unstable"] == ["tests/test_once.py::test_once"]


def test_a_project_without_functions_has_a_complexity_of_zero(scene: Scene) -> None:
    scene.write("src/sample/mod.py", "X = 1\n")
    scene.write("src/sample/other.py", "Y = 2\n")

    measured = base_of(scene)

    assert measured.metrics["max_ccn"] == 0.0 and measured.metrics["functions"] == 0.0


def test_a_project_without_production_files_has_no_complexity_to_report(scene: Scene) -> None:
    for path in ("src/sample/__init__.py", "src/sample/mod.py", "src/sample/other.py"):
        scene.repo.sh("rm", "-f", path)

    measured = base_of(scene)

    assert "max_ccn" not in measured.metrics and "functions" not in measured.metrics
    assert measured.metrics["tests"] == 1.0


def test_a_lizard_that_fails_names_its_run_in_the_gap(scene: Scene) -> None:
    scene.measurer._lizard = (*scene.measurer._lizard[:2], "nonsense")  # noqa: SLF001
    edit_mod(scene, "X = 1\n")

    (gap,) = judge(scene).assumptions

    assert isinstance(gap.result, Inconclusive)
    assert gap.result.tool_run_ids == ("run_tool:t1-a1:complexity:1",)
    assert gap.result.reason is InconclusiveReason.TOOL_FAILED


def test_a_renamed_file_keeps_the_complexity_it_already_had(scene: Scene) -> None:
    scene.write("src/sample/other.py", OTHER + "\n\n" + deep("branchy", 20))
    scene.repo.sh("commit", "-am", "complex already")

    scene.repo.sh("mv", "src/sample/other.py", "src/sample/renamed.py")
    outcome = judge(scene, scope=("src/sample/other.py", "src/sample/renamed.py"))

    assert outcome.verdict is Verdict.PASSED


def test_a_lizard_that_cannot_start_leaves_a_gap_that_says_so(scene: Scene) -> None:
    scene.measurer._lizard = ("no-such-lizard-anywhere",)  # noqa: SLF001
    edit_mod(scene, "X = 1\n")

    (gap,) = judge(scene).assumptions

    assert isinstance(gap.result, Inconclusive)
    assert gap.result.detail == "complexity: no-such-lizard-anywhere could not start"


def test_the_logs_of_the_tools_are_kept_under_tools_and_the_run(scene: Scene) -> None:
    base_of(scene)

    logs = sorted(p.name for p in (scene.work / "run" / "tools" / RUN).glob("*.log"))
    assert logs and all(name.startswith("run_tool-") for name in logs)


def test_the_base_file_makes_the_directories_above_it(scene: Scene) -> None:
    scene.measurer._workdir = scene.work / "a" / "b"  # noqa: SLF001

    scene.measurer._save_base([], [])  # noqa: SLF001

    assert (scene.work / "a" / "b" / "base.json").is_file()


def test_a_lizard_that_exits_badly_leaves_the_complexity_out_of_the_numbers(scene: Scene) -> None:
    scene.measurer._lizard = (*scene.measurer._lizard[:2], "nonsense")  # noqa: SLF001

    measured = base_of(scene)

    assert measured.ok
    assert "max_ccn" not in measured.metrics and "functions" not in measured.metrics


def test_the_complexity_of_every_function_is_available_to_the_planner(scene: Scene) -> None:
    scene.write("src/sample/mod.py", MOD + "\n\n" + deep("branchy", 6))

    found = asyncio.run(scene.measurer.functions(scene.repo.path, scene.recorder.sinks))

    assert found == {
        ("src/sample/mod.py", "add"): 1,
        ("src/sample/mod.py", "branchy"): 7,
        ("src/sample/other.py", "twice"): 1,
    }


def test_a_project_without_production_code_has_no_functions(scene: Scene) -> None:
    for path in ("src/sample/__init__.py", "src/sample/mod.py", "src/sample/other.py"):
        scene.repo.sh("rm", "-f", path)

    assert asyncio.run(scene.measurer.functions(scene.repo.path, scene.recorder.sinks)) == {}


def test_a_lizard_that_fails_stops_the_planning_with_a_reason(scene: Scene) -> None:
    scene.measurer._lizard = (*scene.measurer._lizard[:2], "nonsense")  # noqa: SLF001

    with pytest.raises(RuntimeError, match="the complexity could not be measured: complexity: "):
        asyncio.run(scene.measurer.functions(scene.repo.path, scene.recorder.sinks))


def test_the_repositorys_own_test_command_is_the_one_the_base_is_measured_with(
    repo: Repo, tmp_path: Path
) -> None:
    command = [sys.executable, "-c", "print('PASSED tests/test_x.py::only_one')"]
    values = {"tests": {"commands": {"test": command}}}
    scene = Scene(repo, tmp_path / "scene", values=values)

    measured = base_of(scene)

    assert measured.ok
    assert measured.metrics["tests"] == 1.0


def test_the_numbers_that_need_no_test_run_can_be_taken_on_their_own(scene: Scene) -> None:
    scene.write("src/sample/mod.py", MOD + "\n\nimport os  # LINT:F401\n\n" + deep("branchy", 6))

    taken = asyncio.run(scene.measurer.metrics(scene.repo.path, scene.recorder.sinks))

    assert taken == {"lint_violations": 1.0, "max_ccn": 7.0, "functions": 3.0, **GRAPH}
