"""A JavaScript project, one with no ``tsconfig.json``, for real: the build, the mutation gate and
the agent's commands, with Vitest and with Jest.

Needs Node and npm, and the network the first time; the tests are skipped without them.
"""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from forja.core.adapters import CommandPlan, Operation, OperationRequest, Parsed, ToolOutput
from forja.core.gates.models import Approved, Rejected
from forja.languages.typescript import TypeScriptAdapter
from tests.cli.test_facade import run_facade
from tests.core.idempotency.conftest import Repo
from tests.languages.typescript import test_jest_lines as jest
from tests.languages.typescript import test_mutation as mutation
from tests.languages.typescript.test_mutation import NPM, judge_files, link

packages = mutation.packages  # the packages installed once for the run
jest_packages = jest.jest_packages

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        shutil.which("node") is None or NPM is None, reason="Node and npm are not installed"
    ),
]


@dataclass(frozen=True)
class Runner:
    """The same small project as each runner wants it written."""

    name: str
    packages: str  # the fixture that holds its installed packages
    base: dict[str, str]
    with_sub: str  # ``calc.js`` with a second function, whose body is line 6
    test_add: str  # the test file once it also loads ``sub``


VITEST_TEST = (
    'import { expect, test } from "vitest";\nimport { add } from "./calc";\n\n'
    'test("add", () => {\n  expect(add(2, 3)).toBe(5);\n});\n'
)
VITEST = Runner(
    name="vitest",
    packages="packages",
    base={
        "package.json": mutation.BASE["package.json"],
        ".gitignore": "node_modules/\n.forja-coverage/\n",
        "calc.js": "export function add(a, b) {\n  return a + b;\n}\n",
        "calc.test.js": VITEST_TEST,
    },
    with_sub=(
        "export function add(a, b) {\n  return a + b;\n}\n"
        "\nexport function sub(a, b) {\n  return a - b;\n}\n"
    ),
    test_add=VITEST_TEST.replace("{ add }", "{ add, sub }"),
)
JEST_TEST = (
    'const { add } = require("./calc");\n\ntest("add", () => {\n  expect(add(2, 3)).toBe(5);\n});\n'
)
JEST = Runner(
    name="jest",
    packages="jest_packages",
    base={
        "package.json": json.dumps(
            {"name": "calc", "private": True, "devDependencies": {"jest": jest.PACKAGES["jest"]}}
        ),
        ".gitignore": "node_modules/\n.forja-coverage/\n",
        "calc.js": "function add(a, b) {\n  return a + b;\n}\n\nmodule.exports = { add };\n",
        "calc.test.js": JEST_TEST,
    },
    with_sub=(
        "function add(a, b) {\n  return a + b;\n}\n"
        "\nfunction sub(a, b) {\n  return a - b;\n}\n"
        "\nmodule.exports = { add, sub };\n"
    ),
    test_add=JEST_TEST.replace("{ add }", "{ add, sub }"),
)
CHECKS = '\ntest("sub", () => {\n  expect(sub(3, 1)).toBe(2);\n});\n'
ONLY_CALLS = '\ntest("sub", () => {\n  sub(3, 1);\n});\n'


@pytest.fixture(params=[VITEST, JEST], ids=lambda runner: runner.name)
def runner(request: pytest.FixtureRequest) -> Runner:
    found: Runner = request.param
    return found


@pytest.fixture
def installed(runner: Runner, request: pytest.FixtureRequest) -> Path:
    found: Path = request.getfixturevalue(runner.packages)
    return found


@pytest.fixture
def project(runner: Runner, installed: Path, tmp_path: Path) -> Path:
    """The project in a plain directory, with its packages, as the agent finds it."""
    root = tmp_path / "project"
    root.mkdir()
    for name, text in runner.base.items():
        (root / name).write_text(text, encoding="utf-8", newline="\n")
    link(installed, root)
    return root


def judge(repo: Repo, tmp_path: Path, runner: Runner, installed: Path, test_of_sub: str):  # type: ignore[no-untyped-def]
    changed = {"calc.js": runner.with_sub, "calc.test.js": runner.test_add + test_of_sub}
    return judge_files(
        repo,
        tmp_path,
        runner.base,
        changed,
        ("calc.js",),
        prepare=lambda root: link(installed, root),
    )


def test_a_new_javascript_function_the_tests_check_is_approved(
    repo: Repo, tmp_path: Path, runner: Runner, installed: Path
) -> None:
    assert isinstance(judge(repo, tmp_path, runner, installed, CHECKS), Approved)


def test_a_javascript_test_that_checks_nothing_is_rejected(
    repo: Repo, tmp_path: Path, runner: Runner, installed: Path
) -> None:
    result = judge(repo, tmp_path, runner, installed, ONLY_CALLS)

    assert isinstance(result, Rejected)
    assert result.findings[0].measured == 0.0
    assert any(f.target == "calc.js:6" for f in result.findings)


def built(root: Path, log: Path) -> list[tuple[str, str]]:
    """The build of the project at ``root``, run as the adapter plans it and read as it reads."""
    adapter = TypeScriptAdapter()
    plan = adapter.plan(Operation.BUILD, OperationRequest(root=root))
    assert isinstance(plan, CommandPlan)
    done = subprocess.run(
        plan.argv, cwd=plan.cwd, capture_output=True, text=True, check=False, timeout=120
    )
    log.write_text(done.stdout, encoding="utf-8")
    parsed = adapter.parse(Operation.BUILD, ToolOutput(done.returncode, log))
    assert isinstance(parsed, Parsed), parsed
    return [(draft.measurement, draft.target.id) for draft in parsed.drafts]


def test_a_javascript_file_node_cannot_parse_fails_the_build(project: Path, tmp_path: Path) -> None:
    (project / "lib").mkdir()
    (project / "lib" / "bad.mjs").write_text("export const = 1;\n", encoding="utf-8")
    (project / "dist").mkdir()
    (project / "dist" / "made.js").write_text("not ) ours\n", encoding="utf-8")

    assert built(project, tmp_path / "build.txt") == [("build.compile_error", "lib/bad.mjs")]

    (project / "lib" / "bad.mjs").write_text("export const one = 1;\n", encoding="utf-8")

    assert built(project, tmp_path / "build.txt") == []


def test_the_tests_of_a_javascript_project_pass_for_the_agent(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_facade(["testar"], project, dict(os.environ)) == 0
    assert "falhou:" not in capsys.readouterr().out


def test_a_javascript_test_that_fails_is_named_to_the_agent(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = (project / "calc.js").read_text(encoding="utf-8").replace("a + b", "a - b")
    (project / "calc.js").write_text(text, encoding="utf-8", newline="\n")

    assert run_facade(["testar"], project, dict(os.environ)) != 0
    shown = capsys.readouterr().out.splitlines()
    failed = [line for line in shown if line.startswith("falhou: ")]
    assert failed
    assert all(line.startswith("falhou: calc.test.js") and " > add" in line for line in failed)
