"""``forja agente testar|lint`` on a TypeScript project, for real, with Vitest and with Jest.

Needs Node and npm, and the network the first time; the tests are skipped without them.
"""

import os
import shutil
from pathlib import Path

import pytest

from tests.cli.test_facade import run_facade
from tests.languages.typescript import test_jest_lines as jest
from tests.languages.typescript import test_mutation as mutation
from tests.languages.typescript.test_mutation import NPM, link

packages = mutation.packages  # the packages installed once for the run
jest_packages = jest.jest_packages

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        shutil.which("node") is None or NPM is None, reason="Node and npm are not installed"
    ),
]


@pytest.fixture(params=["vitest", "jest"])
def project(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    """The small project of each runner, with its packages."""
    vitest = request.param == "vitest"
    files = mutation.BASE if vitest else jest.FILES
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8", newline="\n")
    link(request.getfixturevalue("packages" if vitest else "jest_packages"), tmp_path)
    return tmp_path


def test_the_tests_of_a_typescript_project_pass_for_the_agent(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_facade(["testar"], project, dict(os.environ)) == 0
    assert "falhou:" not in capsys.readouterr().out


def test_a_typescript_test_that_fails_is_named_to_the_agent(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = (project / "calc.ts").read_text(encoding="utf-8").replace("a + b", "a - b")
    (project / "calc.ts").write_text(text, encoding="utf-8", newline="\n")

    assert run_facade(["testar"], project, dict(os.environ)) != 0
    shown = capsys.readouterr().out.splitlines()
    failed = [line for line in shown if line.startswith("falhou: ")]
    assert failed
    assert all(line.startswith("falhou: calc.") and " > add" in line for line in failed)


def test_a_project_that_does_not_declare_eslint_has_no_lint_to_show(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_facade(["lint"], project, dict(os.environ)) == 0
    assert capsys.readouterr().out.splitlines() == [
        "lint não se aplica a este projeto (typescript): the project does not depend on that tool"
    ]
