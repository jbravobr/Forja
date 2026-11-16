"""Exactly which tools the measurer starts, in what directory, with what, and who is told.

The other tests ask what the gates decide; these ask what was run to decide it.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from forja.core.gates import GateInput, PipelineOutcome
from forja.core.states import Level
from forja.orchestrator.measurer import PipelineMeasurer
from tests.checks.support import FAKE_GITLEAKS, FAKE_JSCPD, FAKE_TOOLS, RUN, SAMPLE, Run, Scene
from tests.checks.test_measurer import MOD, edit_mod, judge, write_base
from tests.core.idempotency.conftest import Repo

ATTEMPT = (Level.ATTEMPT, "t1-a1", "run_tool")
EXECUTION = (Level.EXECUTION, RUN, "run_tool")
FILES = ("src/sample/mod.py",)
LINT = ("check", "--ignore-noqa", "--output-format=concise", "--no-cache", "--force-exclude", "--")
PYTEST = ("-m", "pytest", "-p", "no:cacheprovider", "-q", "-rA", "--color=no")


def tail(run: Run) -> tuple[str, ...]:
    """The command after the fake interpreter and script (and the tool's name for the fakes)."""
    command = run.command
    if command[1] in (str(FAKE_GITLEAKS), str(FAKE_JSCPD)):
        return command[2:]
    assert command[:2] == (sys.executable, FAKE_TOOLS), command
    return command[3:]


def one(scene: Scene, check: str) -> Run:
    (found,) = [r for r in scene.recorder.runs if r.check == check]
    return found


# -- an attempt ------------------------------------------------------------------------------------


def test_a_clean_attempt_starts_exactly_these_tools_in_this_order(scene: Scene) -> None:
    edit_mod(scene, "X = 1\n")

    judge(scene)

    runs = scene.recorder.runs
    assert [r.operation_id for r in runs] == [
        "run_tool:t1-a1:normalize_lint:1",
        "run_tool:t1-a1:normalize_format:1",
        "run_tool:t1-a1:secrets:1",
        "run_tool:t1-a1:format:1",
        "run_tool:t1-a1:lint:1",
        "run_tool:t1-a1:lint_before:1",
        "run_tool:t1-a1:types:1",
        "run_tool:t1-a1:types_before:1",
        "run_tool:t1-a1:build:1",
        "run_tool:t1-a1:tests:1",
        "run_tool:t1-a1:coverage:1",
        "run_tool:t1-a1:complexity:1",
        "run_tool:t1-a1:complexity:1",
        "run_tool:t1-a1:complexity:1",
        "run_tool:t1-a1:complexity:1",
        "run_tool:t1-a1:duplication:1",
    ]
    assert {r.where for r in runs} == {ATTEMPT}
    assert {r.timeout for r in runs} == {300.0}


def test_the_files_the_attempt_changed_are_fixed_and_formatted_before_any_gate_reads_them(
    scene: Scene,
) -> None:
    edit_mod(scene, "X = 1\n")

    judge(scene)

    lint, fmt = one(scene, "normalize_lint"), one(scene, "normalize_format")
    assert tail(lint) == (
        "check", "--fix", "--exit-zero", "--output-format=concise", "--no-cache",
        "--force-exclude", "--", *FILES,
    )  # fmt: skip
    assert tail(fmt) == ("format", "--no-cache", "--force-exclude", "--", *FILES)
    assert lint.cwd == fmt.cwd == scene.repo.path
    assert {lint.where, fmt.where} == {ATTEMPT}


def test_secrets_gets_the_added_lines_on_stdin_and_a_report_path_outside_the_worktree(
    scene: Scene,
) -> None:
    edit_mod(scene, "X = 1\n")

    judge(scene)

    run = one(scene, "secrets")
    reports = scene.work / "run" / "secrets" / "t1-a1"
    assert tail(run) == (
        "stdin", "--no-banner", "--redact", "--exit-code", "2",
        "--report-format", "json", "--report-path", str(reports / "gitleaks.json"),
    )  # fmt: skip
    assert run.cwd == scene.repo.path
    assert run.stdin_file == str(reports / "added.txt")
    assert (reports / "added.txt").read_text(encoding="utf-8") == "\n\nX = 1"


def test_format_checks_and_lint_reads_the_files_the_attempt_changed(scene: Scene) -> None:
    edit_mod(scene, "X = 1\n")

    judge(scene)

    assert tail(one(scene, "format")) == (
        "format",
        "--check",
        "--no-cache",
        "--force-exclude",
        "--",
        *FILES,
    )
    assert tail(one(scene, "lint")) == (*LINT, *FILES)
    assert one(scene, "format").cwd == one(scene, "lint").cwd == scene.repo.path


def test_the_before_side_is_linted_in_a_private_directory_with_the_parents_files(
    scene: Scene,
) -> None:
    edit_mod(scene, "X = 1\n")

    judge(scene)

    run = one(scene, "lint_before")
    assert tail(run) == (*LINT, *FILES)
    assert run.cwd != scene.repo.path and run.cwd.name.startswith("forja-before-")
    assert not run.cwd.exists()  # and it is gone once the lint is read


def test_build_and_tests_look_at_the_whole_project(scene: Scene) -> None:
    edit_mod(scene, "X = 1\n")

    judge(scene)

    build, tests = one(scene, "build"), one(scene, "tests")
    assert tail(build)[:2] == ("-m", "compileall") and tail(build)[-2:] == ("--", ".")
    assert tail(tests) == PYTEST
    assert build.cwd == tests.cwd == scene.repo.path


def test_complexity_is_measured_for_the_changed_production_files_after_and_before(
    scene: Scene,
) -> None:
    edit_mod(scene, "X = 1\n")

    judge(scene)

    after, before, *_ = [r for r in scene.recorder.runs if r.check == "complexity"]
    assert tail(after) == ("--csv", "--", *FILES) and tail(before) == ("--csv", "--", *FILES)
    assert after.cwd == scene.repo.path
    assert before.cwd.name.startswith("forja-before-") and not before.cwd.exists()
    assert "NO_COLOR" in after.env_names


def test_test_files_and_non_python_files_are_left_out_of_the_complexity_run(scene: Scene) -> None:
    edit_mod(scene, "X = 1\n")
    scene.write("src/sample/notes.md", "text\n")
    scene.write("tests/test_mod.py", SAMPLE["tests/test_mod.py"] + "\n")

    judge(scene, scope=("src/sample/mod.py", "src/sample/notes.md", "tests"))

    # the scope gate stopped everything after the normalization: tests may not change
    assert {r.check for r in scene.recorder.runs} == {"normalize_lint", "normalize_format"}


def test_only_the_production_python_files_reach_lizard(scene: Scene) -> None:
    edit_mod(scene, "X = 1\n")
    scene.write("src/sample/other.py", MOD + "\n\nY = 2\n")

    judge(scene, scope=("src/sample",))

    after = [r for r in scene.recorder.runs if r.check == "complexity"][0]
    assert tail(after) == ("--csv", "--", "src/sample/mod.py", "src/sample/other.py")


def test_a_renamed_file_is_linted_before_by_its_old_name(scene: Scene) -> None:
    scene.repo.sh("mv", "src/sample/other.py", "src/sample/renamed.py")

    judge(scene, scope=("src/sample",))

    assert tail(one(scene, "lint")) == (*LINT, "src/sample/renamed.py")
    assert tail(one(scene, "lint_before")) == (*LINT, "src/sample/other.py")


def test_a_new_file_has_no_before_to_lint(scene: Scene) -> None:
    scene.write("src/sample/fresh.py", "X = 1\n")

    judge(scene, scope=("src/sample",))

    assert [r.check for r in scene.recorder.runs if r.check.startswith("lint")] == ["lint"]
    assert [r.check for r in scene.recorder.runs if r.check == "complexity"] == ["complexity"] * 2


def test_the_policy_commands_replace_the_adapters_for_that_operation(
    repo: Repo, tmp_path: Path
) -> None:
    command = [sys.executable, FAKE_TOOLS, "python", "-m", "pytest"]
    scene = Scene(repo, tmp_path / "scene", values={"tests": {"commands": {"test": command}}})
    edit_mod(scene, "X = 1\n")

    judge(scene)

    assert one(scene, "tests").command == (*command,)


def test_the_policy_commands_for_lint_also_run_on_the_parents_version(
    repo: Repo, tmp_path: Path
) -> None:
    command = [sys.executable, FAKE_TOOLS, "ruff", "check", "--the-repositorys-own"]
    scene = Scene(repo, tmp_path / "scene", values={"lint": {"commands": {"lint": command}}})
    edit_mod(scene, "X = 1\n")

    judge(scene)

    assert one(scene, "lint").command == (*command,)
    assert one(scene, "lint_before").command == (*command,)


# -- the base --------------------------------------------------------------------------------------


def test_the_base_is_measured_by_these_runs_in_the_execution(scene: Scene) -> None:
    asyncio.run(scene.measurer.baseline(scene.repo.path, scene.recorder.sinks))

    runs = scene.recorder.runs
    assert [r.operation_id for r in runs] == [
        f"run_tool:{RUN}:build:1",
        f"run_tool:{RUN}:tests:1",
        f"run_tool:{RUN}:tests:1",
        f"run_tool:{RUN}:lint:1",
        f"run_tool:{RUN}:complexity:1",
    ]
    assert {r.where for r in runs} == {EXECUTION}
    assert tail(runs[0])[:2] == ("-m", "compileall")
    assert tail(runs[1]) == tail(runs[2]) == PYTEST
    assert tail(runs[3]) == (
        "check",
        "--ignore-noqa",
        "--output-format=concise",
        "--no-cache",
        "--force-exclude",
        "--",
        ".",
    )  # noqa: E501
    assert tail(runs[4]) == (
        "--csv",
        "--",
        "src/sample/__init__.py",
        "src/sample/mod.py",
        "src/sample/other.py",
    )


def test_the_integration_runs_build_then_tests_in_the_run_worktree(scene: Scene) -> None:
    asyncio.run(scene.measurer.integration(scene.repo.path, scene.recorder.sinks))

    runs = scene.recorder.runs
    assert [r.operation_id for r in runs] == [f"run_tool:{RUN}:build:1", f"run_tool:{RUN}:tests:1"]
    assert {r.where for r in runs} == {EXECUTION} and {r.cwd for r in runs} == {scene.repo.path}


def test_normalizing_fixes_what_is_safe_to_fix_and_then_formats_the_whole_tree(
    scene: Scene,
) -> None:
    asyncio.run(scene.measurer.format_worktree(scene.repo.path))

    events = [json.loads(raw) for raw in scene.log.path.read_bytes().split(b"\n") if raw]
    intents = [e for e in events if e["type"] == "intent" and e["operation"] == "run_tool"]
    assert [e["operation_id"] for e in intents] == [
        f"run_tool:{RUN}:normalize_lint:1",
        f"run_tool:{RUN}:normalize_format:1",
    ]
    assert {(e["level"], e["target_id"]) for e in intents} == {("execution", RUN)}
    lint, fmt = (e["payload"]["command"][3:] for e in intents)
    assert lint == [
        "check",
        "--fix",
        "--exit-zero",
        "--output-format=concise",
        "--no-cache",
        "--force-exclude",
        "--",
        ".",
    ]  # noqa: E501
    assert fmt == ["format", "--no-cache", "--force-exclude", "--", "."]
    assert {e["payload"]["cwd"] for e in intents} == {str(scene.repo.path)}


# -- what the pipeline is told ---------------------------------------------------------------------


class Spy:
    """Stands in for the gate pipeline and keeps what it was asked."""

    def __init__(self) -> None:
        self.checks: list[Any] = []
        self.data: list[GateInput] = []
        self.capabilities: list[Any] = []

    def __call__(self, checks: list[Any]) -> "Spy":
        self.checks = checks
        return self

    async def run(self, data: GateInput, capabilities: Any) -> PipelineOutcome:
        self.data.append(data)
        self.capabilities.append(capabilities)
        return PipelineOutcome(())


def test_the_pipeline_gets_the_attempt_the_commit_and_what_the_adapter_can_do(
    scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = Spy()
    monkeypatch.setattr(PipelineMeasurer, "_pipeline", lambda self, checks: spy(checks))
    edit_mod(scene, "X = 1\n")

    judge(scene)

    (data,) = spy.data
    assert (data.worktree, data.task_id, data.stage_id, data.attempt_id) == (
        scene.repo.path, "t1", "t1-s", "t1-a1",
    )  # fmt: skip
    assert data.commit == scene.repo.sh("rev-parse", "HEAD")
    assert spy.capabilities == [
        {"test_filter_by_file", "test_filter_by_test", "coverage", "structural_meters"}
    ]
    assert [c.id for c in spy.checks] == [
        "scope", "secrets", "format", "lint", "types", "build", "tests", "mutation", "complexity",
        "crap", "pass_through", "module_depth", "duplication", "dependency_cycles", "layer_rules",
    "package_metrics", "sast", "dependency_audit", "new_dependency", "differential",
        "e2e", "test_data", "properties", "voice", "comments",
    ]  # fmt: skip


def test_the_integration_is_judged_as_such(scene: Scene, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy()
    monkeypatch.setattr(PipelineMeasurer, "_pipeline", lambda self, checks: spy(checks))

    asyncio.run(scene.measurer.integration(scene.repo.path, scene.recorder.sinks))

    (data,) = spy.data
    assert (data.worktree, data.task_id, data.stage_id, data.attempt_id) == (
        scene.repo.path, "integration", "integration", "integration",
    )  # fmt: skip
    assert data.commit == scene.repo.sh("rev-parse", "HEAD")
    assert [c.id for c in spy.checks] == ["build", "tests"]


def test_the_checks_are_built_with_the_files_the_attempt_changed(
    scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = Spy()
    monkeypatch.setattr(PipelineMeasurer, "_pipeline", lambda self, checks: spy(checks))
    edit_mod(scene, "X = 1\n")
    scene.write("src/sample/other.py", "Y = 2\n")
    scene.write("tests/test_new.py", "def test_new() -> None:\n    pass\n")
    write_base(scene, ["a::b"], ["a::c"])

    judge(scene, scope=("src",))

    secrets, complexity, tests = (
        next(c for c in spy.checks if c.id == i) for i in ("secrets", "complexity", "tests")
    )
    assert secrets.reports == scene.work / "run" / "secrets" / "t1-a1"
    assert secrets.gitleaks == (sys.executable, str(FAKE_GITLEAKS))
    (part,) = complexity.parts  # one language: one part
    complexity = part.check
    assert complexity.files == ("src/sample/mod.py", "src/sample/other.py")
    assert complexity.lizard == scene.lizard
    assert set(complexity.before) == {"src/sample/mod.py", "src/sample/other.py"}
    assert tests.quarantine == {"a::b"} and tests.passing == {"a::c"}
    lint = next(c for c in spy.checks if c.id == "lint")
    assert set(lint.before) == {"src/sample/mod.py", "src/sample/other.py"}
