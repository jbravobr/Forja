"""The tests of each line, the filter that runs them back, and the stop at the first failure, for
real with Jest 30 and ts-jest. Needs Node and npm, and the network once (the packages); the tests
are skipped without them."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from forja.checks.mutation import failed_test
from forja.core.adapters import Operation, Parsed
from forja.languages.typescript import TypeScriptAdapter
from tests.core.idempotency.conftest import Repo
from tests.languages.typescript.test_mutation import NPM, link
from tests.languages.typescript.test_test_lines import lines_of, plan_of, run

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        shutil.which("node") is None or NPM is None, reason="Node and npm are not installed"
    ),
]

PACKAGES = {
    "typescript": "5.9.3",
    "jest": "30.5.2",
    "@jest/globals": "30.5.2",
    "ts-jest": "29.4.15",
}
FILES = {
    "package.json": json.dumps({"name": "calc", "private": True, "devDependencies": PACKAGES}),
    "tsconfig.json": (
        '{"compilerOptions": {"strict": true, "module": "commonjs", "target": "es2020", '
        '"esModuleInterop": true}}'
    ),
    "jest.config.js": 'module.exports = { preset: "ts-jest", testEnvironment: "node" };\n',
    ".gitignore": "node_modules/\n.forja-coverage/\n.forja-test-lines/\n",
    "calc.ts": "export function add(a: number, b: number): number {\n  return a + b;\n}\n",
    "calc.test.ts": (
        'import { expect, test } from "@jest/globals";\nimport { add } from "./calc";\n\n'
        'test("add", () => {\n  expect(add(2, 3)).toBe(5);\n});\n'
    ),
    "calc.more.test.ts": (
        'import { expect, test } from "@jest/globals";\nimport { add } from "./calc";\n\n'
        'test("add more", () => {\n  expect(add(1, 1)).toBe(2);\n});\n'
    ),
}


@pytest.fixture(scope="session")
def jest_packages(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("jest-packages")
    (root / "package.json").write_text(FILES["package.json"], encoding="utf-8")
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


@pytest.fixture
def jest_project(repo: Repo, jest_packages: Path) -> Repo:
    for name, text in FILES.items():
        (repo.path / name).write_text(text, encoding="utf-8", newline="\n")
    repo.sh("add", "-A")
    repo.sh("commit", "-m", "project")
    link(jest_packages, repo.path)
    return repo


def test_a_line_names_the_tests_of_both_files_under_jest(
    jest_project: Repo, tmp_path: Path
) -> None:
    code, _ = run(plan_of(Operation.TEST_LINES, jest_project.path), jest_project, tmp_path)

    assert code == 0
    assert lines_of(jest_project.path)[2] == {"calc.test.ts > add", "calc.more.test.ts > add more"}


def test_the_ids_of_a_line_run_exactly_those_tests_under_jest(
    jest_project: Repo, tmp_path: Path
) -> None:
    run(plan_of(Operation.TEST_LINES, jest_project.path), jest_project, tmp_path)
    ids = tuple(sorted(lines_of(jest_project.path)[2]))

    code, output = run(
        plan_of(Operation.TEST, jest_project.path, tests=ids, rerun=True), jest_project, tmp_path
    )

    assert code == 0
    parsed = TypeScriptAdapter().parse(Operation.TEST, output)
    assert isinstance(parsed, Parsed)
    assert {d.target.id for d in parsed.drafts if d.value == "passed"} == set(ids)


def test_the_run_stops_at_the_first_failure_under_jest(jest_project: Repo, tmp_path: Path) -> None:
    run(plan_of(Operation.TEST_LINES, jest_project.path), jest_project, tmp_path)
    ids = tuple(sorted(lines_of(jest_project.path)[2]))  # the file order: calc.more.test.ts first
    source = jest_project.path / "calc.ts"
    broken = source.read_text(encoding="utf-8").replace("a + b", "a + b + 1")
    source.write_text(broken, encoding="utf-8", newline="\n")

    code, output = run(
        plan_of(Operation.TEST, jest_project.path, tests=ids, rerun=True), jest_project, tmp_path
    )

    assert code != 0
    assert failed_test(TypeScriptAdapter(), output, ids) == ids[0]
    lines = output.log_path.read_text(encoding="utf-8").splitlines()
    ran = [line for line in lines if line.startswith(("ok ", "not ok ")) and " > " in line]
    assert [line for line in ran if "# SKIP" not in line] == [ran[0]]
