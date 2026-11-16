"""Mutation of TypeScript code, for real: Vitest runs on copies changed by the mutator's rules.

Needs Node and npm, and the network the first time (the packages are installed once for the whole
run); the tests are skipped without them.
"""

import asyncio
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from forja.checks.changes import read_changes
from forja.checks.mutation import MutationCheck
from forja.checks.tooling import Tools
from forja.core.events import EventLog
from forja.core.gates import GateInput
from forja.core.gates.models import Approved, GateResult, Rejected
from forja.core.states import ExecutionState, Level
from forja.languages.typescript import TypeScriptAdapter
from forja.languages.typescript.adapter import NODE_ENVIRONMENT
from tests.checks.support import RUN, sinks
from tests.core.events.support import FakeClock
from tests.core.idempotency.conftest import Repo

NPM = shutil.which("npm")
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        shutil.which("node") is None or NPM is None, reason="Node and npm are not installed"
    ),
]

PACKAGES = {"vitest": "5.0.5", "@vitest/coverage-v8": "5.0.5"}
BASE = {
    "package.json": json.dumps({"name": "calc", "type": "module", "devDependencies": PACKAGES}),
    "tsconfig.json": '{"compilerOptions": {"strict": true, "module": "esnext"}}',
    ".gitignore": "node_modules/\n.forja-coverage/\n",
    "calc.ts": "export function add(a: number, b: number): number {\n  return a + b;\n}\n",
    "calc.test.ts": (
        'import { expect, test } from "vitest";\nimport { add } from "./calc";\n\n'
        'test("add", () => {\n  expect(add(2, 3)).toBe(5);\n});\n'
    ),
}
WITH_SUB = BASE["calc.ts"] + (
    "\nexport function sub(a: number, b: number): number {\n  return a - b;\n}\n"
)
TEST_ADD = BASE["calc.test.ts"].replace("{ add }", "{ add, sub }")
TEST_THAT_CHECKS = TEST_ADD + ('\ntest("sub", () => {\n  expect(sub(3, 1)).toBe(2);\n});\n')
TEST_THAT_ONLY_CALLS = TEST_ADD + '\ntest("sub", () => {\n  sub(3, 1);\n});\n'
TEST_THAT_IS_EMPTY = TEST_ADD + '\ntest("sub", () => {});\n'


@pytest.fixture(scope="session")
def packages(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The installed packages, once for every test of the run."""
    root = tmp_path_factory.mktemp("node-packages")
    (root / "package.json").write_text(BASE["package.json"], encoding="utf-8")
    assert NPM is not None
    done = subprocess.run(
        [NPM, "install", "--no-audit", "--no-fund", "--loglevel=error"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        pytest.skip(f"the packages could not be installed: {done.stderr[-300:]}")
    return root / "node_modules"


def link(packages: Path, project: Path) -> None:
    target = project / "node_modules"
    try:
        os.symlink(packages, target, target_is_directory=True)
    except OSError:  # Windows without the right to make links
        subprocess.run(["cmd", "/c", "mklink", "/J", str(target), str(packages)], check=True)


def judge(
    repo: Repo,
    tmp_path: Path,
    packages: Path,
    test_text: str,
    adapter: TypeScriptAdapter | None = None,
    history: Path | None = None,
) -> GateResult:
    """Commit the base project, then add ``sub`` with the given test file and judge the change."""
    changed = {"calc.ts": WITH_SUB, "calc.test.ts": test_text}
    return judge_files(
        repo,
        tmp_path,
        BASE,
        changed,
        ("calc.ts",),
        adapter=adapter,
        history=history,
        prepare=lambda root: link(packages, root),
    )


def judge_files(
    repo: Repo,
    tmp_path: Path,
    base: dict[str, str],
    changed: dict[str, str],
    production: tuple[str, ...],
    adapter: TypeScriptAdapter | None = None,
    history: Path | None = None,
    prepare: Callable[[Path], None] | None = None,
) -> GateResult:
    """Commit ``base``, prepare the project (``prepare``, once the base is in), write ``changed``
    and judge it as the gate does."""
    for name, text in base.items():
        (repo.path / name).parent.mkdir(parents=True, exist_ok=True)
        (repo.path / name).write_text(text, encoding="utf-8", newline="\n")
    repo.sh("add", "-A")
    repo.sh("commit", "-m", "project")
    if prepare is not None:
        prepare(repo.path)
    for name, text in changed.items():
        (repo.path / name).parent.mkdir(parents=True, exist_ok=True)
        (repo.path / name).write_text(text, encoding="utf-8", newline="\n")

    log = EventLog.open(tmp_path / "events.jsonl", RUN, clock=FakeClock())
    log.append_transition(Level.EXECUTION, RUN, None, ExecutionState.PREPARING)
    wanted = NODE_ENVIRONMENT | {"TEMP", "TMP", "USERPROFILE", "SYSTEMROOT", "PATHEXT"}
    host_env = {**repo.env, **{k: v for k, v in os.environ.items() if k.upper() in wanted}}
    tools = Tools(
        sinks=sinks,
        log=log,
        level=Level.ATTEMPT,
        target="a",
        logs=tmp_path / "tool-logs",
        host_env=host_env,
    )
    changes = asyncio.run(read_changes(repo.git.at(repo.path)))
    check = MutationCheck(
        tools,
        adapter or TypeScriptAdapter(),
        repo.path,
        changes,
        production,
        host_env,
        language="typescript",
        history=history,
    )
    data = GateInput(Path("."), "c" * 40, "t", "s", "a", values={})
    return asyncio.run(check.evaluate(data))


def test_a_new_function_the_tests_check_is_approved(
    repo: Repo, tmp_path: Path, packages: Path
) -> None:
    assert isinstance(judge(repo, tmp_path, packages, TEST_THAT_CHECKS), Approved)


@pytest.mark.parametrize("test_text", [TEST_THAT_ONLY_CALLS, TEST_THAT_IS_EMPTY])
def test_a_test_that_checks_nothing_is_rejected(
    repo: Repo, tmp_path: Path, packages: Path, test_text: str
) -> None:
    result = judge(repo, tmp_path, packages, test_text)

    assert isinstance(result, Rejected)
    if test_text is TEST_THAT_ONLY_CALLS:  # the line runs, and no change to it is noticed
        assert result.findings[0].measured == 0.0
        assert result.findings[0].limit == 0.80
        assert any(f.target == "calc.ts:6" for f in result.findings)
    else:  # no test runs the new line at all
        assert {f.target for f in result.findings} == {"calc.ts:6"}
        assert "no test runs this new line" in result.findings[0].message
