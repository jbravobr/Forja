"""A whole run of the orchestrator with the real measurer: only the agent and gitleaks are faked."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from forja.core.states import AttemptState, ExecutionState, Level, TaskState
from forja.languages.python import PythonAdapter
from forja.orchestrator.measurer import PipelineMeasurer
from forja.orchestrator.ports import PlannedTask
from tests.checks.support import (
    BLOCKING,
    FAKE_GITLEAKS,
    LIZARD,
    OBSERVATION,
    PREMISE,
    SAMPLE,
    registry_of,
)
from tests.orchestrator.fakes import ScriptedAgent, ScriptedPlanner
from tests.orchestrator.harness import IDENTITY, World, minimal_env

pytestmark = pytest.mark.slow

TASK = PlannedTask("add a subtraction", ("src/sample/mod.py",), 8, 400)
GOOD = SAMPLE["src/sample/mod.py"] + "\n\ndef sub(a: int, b: int) -> int:\n    return a - b\n"
UNFORMATTED = SAMPLE["src/sample/mod.py"] + "\n\ndef sub( a,b ):\n  return a-b\n"


@pytest.fixture(scope="module")
def python_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("python-template") / "repo"
    home = path.parent / "home"
    home.mkdir()
    for relative, text in SAMPLE.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    env = minimal_env(home)
    base = [
        "git",
        "-c",
        f"user.name={IDENTITY.committer_name}",
        "-c",
        f"user.email={IDENTITY.committer_email}",
    ]
    for args in (
        ["init", "-b", "main"],
        ["config", "core.autocrlf", "false"],
        ["add", "-A"],
        ["commit", "-m", "base"],
    ):
        subprocess.run([*base, *args], cwd=path, env=env, check=True, capture_output=True)
    return path


@pytest.fixture
def world(tmp_path: Path, python_template: Path) -> World:
    (tmp_path / "home").mkdir()
    shutil.copytree(python_template, tmp_path / "repo")
    return World.at(tmp_path)


def run(world: World, *edits: dict[str, str]):  # type: ignore[no-untyped-def]
    log = world.open()
    settings = {n: BLOCKING for n in ("scope", "secrets", "format", "lint", "build", "tests")}
    measurer = PipelineMeasurer(
        adapters=registry_of(PythonAdapter(cache_dir=world.root / "pycache")),
        git=world.repo.git,
        log=log,
        workdir=world.run_dir / "measure",
        host_env=world.repo.env,
        settings=settings
        | {
            "complexity": PREMISE,
            "mutation": OBSERVATION,
            "crap": OBSERVATION,
            "pass_through": OBSERVATION,
            "module_depth": OBSERVATION,
            "duplication": OBSERVATION,
            "types": OBSERVATION,
            "dependency_cycles": OBSERVATION,
            "layer_rules": OBSERVATION,
            "package_metrics": OBSERVATION,
            "sast": OBSERVATION,
            "dependency_audit": OBSERVATION,
            "new_dependency": OBSERVATION,
            "differential": OBSERVATION,
            "e2e": OBSERVATION,
            "test_data": OBSERVATION,
            "properties": OBSERVATION,
            "voice": OBSERVATION,
            "comments": OBSERVATION,
        },
        lizard=LIZARD,
        gitleaks=(sys.executable, str(FAKE_GITLEAKS)),
    )
    return world.run(
        log=log,
        measurer=measurer,
        agent=ScriptedAgent(edits=edits),
        planner=ScriptedPlanner((TASK,)),
    )


def test_a_good_change_goes_through_every_gate_and_is_delivered(world: World) -> None:
    outcome, log = run(world, {"src/sample/mod.py": GOOD})

    assert outcome.state is ExecutionState.DELIVERED
    assert log.state.state_of(Level.TASK, "t1") is TaskState.INTEGRATED
    tree = world.repo.sh("show", f"{world.workspace.run_branch}:src/sample/mod.py")
    assert "def sub" in tree
    branch = world.workspace.run_branch
    stage = world.repo.sh("log", "-1", "--format=%H", "--grep=Stage-Id: t1-s", branch)
    trailers = world.repo.sh("log", "-1", "--format=%(trailers:only,unfold)", stage)
    assert "Evidence-Id: scope" in trailers.splitlines()
    assert "Metric-functions: 2 -> 3" in trailers.splitlines()
    assert '"functions": {' in world.repo.sh("notes", "show", stage)


def test_work_the_formatter_would_change_is_formatted_and_committed_in_the_same_attempt(
    world: World,
) -> None:
    outcome, log = run(world, {"src/sample/mod.py": UNFORMATTED})

    assert outcome.state is ExecutionState.DELIVERED
    assert log.state.state_of(Level.ATTEMPT, "t1-a1") is AttemptState.COMMITTED
    tree = world.repo.sh("show", f"{world.workspace.run_branch}:src/sample/mod.py")
    assert "def sub(a, b):\n    return a - b" in tree
