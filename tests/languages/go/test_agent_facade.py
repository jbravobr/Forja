"""``forja agente testar|lint`` on a Go project, for real: the agent sees what the gates see.

Needs the Go tool chain; the tests are skipped without it.
"""

import os
import shutil
from pathlib import Path

import pytest

from forja.languages.go.adapter import find_golangci_lint
from tests.cli.test_facade import run_facade
from tests.languages.go.test_mutation import BASE

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("go") is None, reason="the Go tool chain is not installed"),
]

BROKEN = BASE["calc.go"].replace("a + b", "a - b")
UNCHECKED = (
    'package calc\n\nimport "os"\n\nfunc Clean() {\n\tos.Remove("x")\n}\n'  # errcheck: not checked
)


def project(root: Path, **changed: str) -> Path:
    for name, text in {**BASE, **changed}.items():
        (root / name).write_text(text, encoding="utf-8", newline="\n")
    return root


def test_the_tests_of_a_go_project_pass_for_the_agent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_facade(["testar"], project(tmp_path), dict(os.environ)) == 0
    assert "falhou:" not in capsys.readouterr().out


def test_a_go_test_that_fails_is_named_to_the_agent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, **{"calc.go": BROKEN})

    assert run_facade(["testar"], root, dict(os.environ)) != 0
    shown = capsys.readouterr().out
    assert "falhou: example.com/calc/TestAdd" in shown.splitlines()
    assert "add" in shown  # what the test said when it failed is in the output of the tool


@pytest.mark.skipif(find_golangci_lint() is None, reason="golangci-lint is not installed")
def test_the_lint_of_a_go_project_names_the_file_and_the_linter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)

    assert run_facade(["lint"], root, dict(os.environ)) == 0
    capsys.readouterr()

    (root / "clean.go").write_text(UNCHECKED, encoding="utf-8", newline="\n")

    assert run_facade(["lint"], root, dict(os.environ)) != 0
    assert "clean.go: errcheck (1)" in capsys.readouterr().out.splitlines()
