"""The tests of each line, the filter that runs them back, and the gate's verdicts with and without
them, for real: Vitest runs on small projects. Needs Node and npm, and the network once (the
packages); the tests are skipped without them."""

import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

import tests.languages.typescript.test_mutation as mutation
from forja.checks.mutation import failed_test, load_killers
from forja.core.adapters import (
    CommandPlan,
    LanguageCapability,
    NotSupported,
    Operation,
    OperationRequest,
    Parsed,
    ToolOutput,
)
from forja.core.gates.models import Approved, Rejected
from forja.core.reports import ReportFormat, ReportSpec
from forja.core.reports.readers import read_test_lines
from forja.languages.typescript import TypeScriptAdapter
from forja.languages.typescript.adapter import NODE_ENVIRONMENT
from forja.languages.typescript.template import template
from tests.core.idempotency.conftest import Repo
from tests.languages.typescript.test_mutation import (
    BASE,
    NPM,
    TEST_THAT_CHECKS,
    TEST_THAT_ONLY_CALLS,
    judge,
    judge_files,
    link,
)

packages = mutation.packages  # the packages installed once for the run

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        shutil.which("node") is None or NPM is None, reason="Node and npm are not installed"
    ),
]

MORE = (
    'import { expect, test } from "vitest";\nimport { add } from "./calc";\n\n'
    'test("add more", () => {\n  expect(add(1, 1)).toBe(2);\n});\n'
)


class WithoutLines(TypeScriptAdapter):
    """The same adapter without the tests of each line: the gate judges by the whole command."""

    capabilities = TypeScriptAdapter.capabilities - {LanguageCapability.TEST_LINES}

    def report(self, operation: Operation) -> ReportSpec | NotSupported:
        if operation is Operation.TEST_LINES:
            return NotSupported("test_lines", "not offered in this test")
        return super().report(operation)


def plan_of(operation: Operation, root: Path, **options: object) -> CommandPlan:
    answer = TypeScriptAdapter().plan(operation, OperationRequest(root=root, **options))  # type: ignore[arg-type]
    assert isinstance(answer, CommandPlan), answer
    return answer


def run(plan: CommandPlan, repo: Repo, tmp_path: Path) -> tuple[int, ToolOutput]:
    wanted = NODE_ENVIRONMENT | {"TEMP", "TMP", "USERPROFILE", "SYSTEMROOT", "PATHEXT"}
    host = {**repo.env, **{k: v for k, v in os.environ.items() if k.upper() in wanted}}
    done = subprocess.run(
        list(plan.argv),
        cwd=plan.cwd,
        env={**host, **plan.env_set},
        capture_output=True,
        text=True,
        check=False,
    )
    log = tmp_path / "output.txt"
    log.write_text(done.stdout + done.stderr, encoding="utf-8")
    return done.returncode, ToolOutput(done.returncode, log)


def lines_of(root: Path) -> Mapping[int, frozenset[str]]:
    text = (root / ".forja-test-lines" / "linhas.json").read_text(encoding="utf-8")
    found = read_test_lines(ReportFormat.TEST_LINES_JSON, {"linhas.json": text}, root)
    return found["calc.ts"]


@pytest.fixture
def two_files(repo: Repo, packages: Path) -> Repo:
    base = {**BASE, "calc.more.test.ts": MORE}
    for name, text in base.items():
        (repo.path / name).write_text(text, encoding="utf-8", newline="\n")
    repo.sh("add", "-A")
    repo.sh("commit", "-m", "project")
    link(packages, repo.path)
    return repo


def test_a_line_names_the_tests_of_both_files_and_a_line_without_one_names_none(
    two_files: Repo, tmp_path: Path
) -> None:
    code, _ = run(plan_of(Operation.TEST_LINES, two_files.path), two_files, tmp_path)

    assert code == 0
    lines = lines_of(two_files.path)
    assert lines[2] == {"calc.test.ts > add", "calc.more.test.ts > add more"}
    assert 1 not in lines  # the line that declares add: no test runs it


def test_the_ids_of_a_line_run_exactly_those_tests(two_files: Repo, tmp_path: Path) -> None:
    run(plan_of(Operation.TEST_LINES, two_files.path), two_files, tmp_path)
    ids = tuple(sorted(lines_of(two_files.path)[2]))

    code, output = run(
        plan_of(Operation.TEST, two_files.path, tests=ids, rerun=True), two_files, tmp_path
    )

    assert code == 0
    parsed = TypeScriptAdapter().parse(Operation.TEST, output)
    assert isinstance(parsed, Parsed)
    assert {d.target.id for d in parsed.drafts if d.value == "passed"} == set(ids)


def test_the_run_stops_at_the_first_failure_and_names_that_test(
    two_files: Repo, tmp_path: Path
) -> None:
    run(plan_of(Operation.TEST_LINES, two_files.path), two_files, tmp_path)
    ids = tuple(sorted(lines_of(two_files.path)[2]))  # the file order: calc.more.test.ts first
    source = two_files.path / "calc.ts"
    broken = source.read_text(encoding="utf-8").replace("a + b", "a + b + 1")
    source.write_text(broken, encoding="utf-8", newline="\n")

    code, output = run(
        plan_of(Operation.TEST, two_files.path, tests=ids, rerun=True), two_files, tmp_path
    )

    assert code != 0
    assert isinstance(TypeScriptAdapter().parse(Operation.TEST, output), Parsed)
    assert failed_test(TypeScriptAdapter(), output, ids) == ids[0]
    lines = output.log_path.read_text(encoding="utf-8").splitlines()
    # a test file the run did not reach is a "not ok" of its own name, and the other test of the
    # file is listed as skipped: only the test that failed ran
    ran = [line for line in lines if line.startswith(("ok ", "not ok ")) and " > " in line]
    assert [line for line in ran if "# SKIP" not in line] == [ran[0]]


@pytest.mark.parametrize("adapter", [TypeScriptAdapter(), WithoutLines()], ids=["lines", "none"])
@pytest.mark.parametrize("test_text", [TEST_THAT_CHECKS, TEST_THAT_ONLY_CALLS])
def test_the_verdict_is_the_same_with_and_without_the_tests_of_each_line(
    repo: Repo, tmp_path: Path, packages: Path, adapter: TypeScriptAdapter, test_text: str
) -> None:
    result = judge(repo, tmp_path, packages, test_text, adapter=adapter)

    if test_text is TEST_THAT_CHECKS:
        assert isinstance(result, Approved)
    else:
        assert isinstance(result, Rejected)
        assert any(f.target == "calc.ts:6" for f in result.findings)


def test_the_killer_of_a_mutant_is_kept_for_the_order(
    repo: Repo, tmp_path: Path, packages: Path
) -> None:
    history = tmp_path / "measure" / "killers.json"

    result = judge(repo, tmp_path, packages, TEST_THAT_CHECKS, history=history)

    assert isinstance(result, Approved)
    assert "calc.test.ts > sub" in set(load_killers(history).values())


def test_a_project_made_by_the_template_measures_its_coverage_and_is_judged(
    repo: Repo, tmp_path: Path
) -> None:
    project = template("calc-tool")
    base = dict(project.files)
    changed = {
        "src/index.ts": base["src/index.ts"]
        + "\nexport function twice(n: number): number {\n  return n * 2;\n}\n",
        "src/index.test.ts": base["src/index.test.ts"].replace(
            'import { version } from "./index";',
            'import { twice, version } from "./index";',
        )
        + '\ntest("twice doubles", () => {\n  expect(twice(4)).toBe(8);\n});\n',
    }

    def install(root: Path) -> None:
        for argv in project.setup:
            program = NPM if argv[0] == "npm" and NPM else argv[0]  # npm is a .cmd on Windows
            subprocess.run([program, *argv[1:]], cwd=root, check=True, capture_output=True)

    result = judge_files(
        repo,
        tmp_path,
        base,
        changed,
        ("src/index.ts",),
        prepare=install,
    )

    assert isinstance(result, Approved), result
