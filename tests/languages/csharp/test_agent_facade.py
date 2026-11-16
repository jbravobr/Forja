"""``forja agente testar|lint`` on a C# solution, for real: the agent sees what the gates see.

Needs the .NET SDK and its packages (xUnit) in the NuGet cache or reachable by the network; the
tests are skipped without the SDK.
"""

import os
import shutil
from pathlib import Path

import pytest

from tests.cli.test_facade import run_facade
from tests.languages.csharp.test_mutation import BASE, CALC, write

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("dotnet") is None, reason="the .NET SDK is not installed"),
]


def solution(root: Path, **changed: str) -> Path:
    for name, text in {**BASE, **changed}.items():
        write(root, name, text)
    return root


def test_the_tests_of_a_csharp_solution_pass_for_the_agent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_facade(["testar"], solution(tmp_path), dict(os.environ)) == 0
    shown = capsys.readouterr().out.splitlines()
    assert not [line for line in shown if line.startswith("falhou:")]
    # the solution and each of its projects are a project to the Forja, as they are to the gates
    assert [line for line in shown if line.startswith("== ")] == [
        "== csharp: . ==",
        "== csharp: src/Calc ==",
        "== csharp: tests/Calc.Tests ==",
    ]


def test_a_csharp_test_that_fails_is_named_to_the_agent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = solution(tmp_path, **{"src/Calc/Calc.cs": CALC.replace("a + b", "a - b")})

    assert run_facade(["testar"], root, dict(os.environ)) != 0
    shown = capsys.readouterr().out.splitlines()
    assert "falhou: Calc.Tests.CalculatorTests.Adds" in shown


def test_the_lint_of_a_csharp_solution_with_nothing_to_say_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_facade(["lint"], solution(tmp_path), dict(os.environ)) == 0
    assert "não se aplica" not in capsys.readouterr().out
