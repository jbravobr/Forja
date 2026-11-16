"""Mutation of C# code, for real: ``dotnet test`` runs on copies changed by the mutator's rules.

Needs the .NET SDK and its packages (xUnit, coverlet) in the NuGet cache or reachable by the
network; the tests are skipped without the SDK.
"""

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from forja.checks.changes import read_changes
from forja.checks.mutation import MutationCheck
from forja.checks.tooling import Tools
from forja.core.events import EventLog
from forja.core.gates import GateInput
from forja.core.gates.models import Approved, GateResult, Rejected
from forja.core.states import ExecutionState, Level
from forja.languages.csharp import CSharpAdapter
from forja.languages.csharp.adapter import DOTNET_ENVIRONMENT
from tests.checks.support import RUN, sinks
from tests.core.events.support import FakeClock
from tests.core.idempotency.conftest import Repo

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("dotnet") is None, reason="the .NET SDK is not installed"),
]

LIBRARY = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
    <Nullable>enable</Nullable>
  </PropertyGroup>
</Project>
"""
TESTS = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
    <Nullable>enable</Nullable>
    <IsPackable>false</IsPackable>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="coverlet.collector" Version="6.0.2" />
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.12.0" />
    <PackageReference Include="xunit" Version="2.9.2" />
    <PackageReference Include="xunit.runner.visualstudio" Version="2.8.2" />
  </ItemGroup>
  <ItemGroup>
    <Using Include="Xunit" />
    <ProjectReference Include="../../src/Calc/Calc.csproj" />
  </ItemGroup>
</Project>
"""
# ruff: noqa: E501 - the lines of a solution file are not ours to wrap
SOLUTION = """Microsoft Visual Studio Solution File, Format Version 12.00
Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Calc", "src/Calc/Calc.csproj", "{11111111-1111-1111-1111-111111111111}"
EndProject
Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Calc.Tests", "tests/Calc.Tests/Calc.Tests.csproj", "{22222222-2222-2222-2222-222222222222}"
EndProject
Global
\tGlobalSection(SolutionConfigurationPlatforms) = preSolution
\t\tDebug|Any CPU = Debug|Any CPU
\tEndGlobalSection
\tGlobalSection(ProjectConfigurationPlatforms) = postSolution
\t\t{11111111-1111-1111-1111-111111111111}.Debug|Any CPU.ActiveCfg = Debug|Any CPU
\t\t{11111111-1111-1111-1111-111111111111}.Debug|Any CPU.Build.0 = Debug|Any CPU
\t\t{22222222-2222-2222-2222-222222222222}.Debug|Any CPU.ActiveCfg = Debug|Any CPU
\t\t{22222222-2222-2222-2222-222222222222}.Debug|Any CPU.Build.0 = Debug|Any CPU
\tEndGlobalSection
EndGlobal
"""
CALC = (
    "namespace Calc;\n\npublic static class Calculator\n{\n"
    "    public static int Add(int a, int b)\n    {\n        return a + b;\n    }\n}\n"
)
WITH_SUB = CALC.replace(
    "    }\n}\n",
    "    }\n\n    public static int Sub(int a, int b)\n    {\n        return a - b;\n    }\n}\n",
)
TEST_ADD = (
    "using Calc;\n\nnamespace Calc.Tests;\n\npublic class CalculatorTests\n{\n"
    "    [Fact]\n    public void Adds()\n    {\n        Assert.Equal(5, Calculator.Add(2, 3));\n"
    "    }\n"
)
TEST_THAT_CHECKS = (
    TEST_ADD
    + "\n    [Fact]\n    public void Subtracts()\n    {\n"
    + "        Assert.Equal(2, Calculator.Sub(3, 1));\n    }\n}\n"
)
TEST_THAT_ONLY_CALLS = (
    TEST_ADD
    + "\n    [Fact]\n    public void Subtracts()\n    {\n        Calculator.Sub(3, 1);\n    }\n}\n"
)
TEST_THAT_IS_EMPTY = TEST_ADD + "\n    [Fact]\n    public void Subtracts()\n    {\n    }\n}\n"
BASE = {
    "Calc.sln": SOLUTION,
    ".gitignore": "bin/\nobj/\n.forja-coverage/\n",
    "src/Calc/Calc.csproj": LIBRARY,
    "src/Calc/Calc.cs": CALC,
    "tests/Calc.Tests/Calc.Tests.csproj": TESTS,
    "tests/Calc.Tests/CalculatorTests.cs": TEST_ADD + "}\n",
}


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def judge(
    repo: Repo,
    tmp_path: Path,
    test_text: str,
    adapter: CSharpAdapter | None = None,
    history: Path | None = None,
    line_tests: bool = False,
) -> GateResult:
    """Commit the base solution, then add ``Sub`` with the given test file and judge the change:
    by the whole command, or with ``line_tests`` by the tests of the lines first, as the policy
    may ask."""
    for name, text in BASE.items():
        write(repo.path, name, text)
    repo.sh("add", "-A")
    repo.sh("commit", "-m", "solution")
    write(repo.path, "src/Calc/Calc.cs", WITH_SUB)
    write(repo.path, "tests/Calc.Tests/CalculatorTests.cs", test_text)

    log = EventLog.open(tmp_path / "events.jsonl", RUN, clock=FakeClock())
    log.append_transition(Level.EXECUTION, RUN, None, ExecutionState.PREPARING)
    wanted = {name.upper() for name in DOTNET_ENVIRONMENT} | {
        "TEMP",
        "TMP",
        "SYSTEMROOT",
        "PATHEXT",
    }
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
        adapter or CSharpAdapter(),
        repo.path,
        changes,
        ("src/Calc/Calc.cs",),
        host_env,
        language="csharp",
        history=history,
    )
    values = {"limits": {"line_tests": 1}} if line_tests else {}
    data = GateInput(Path("."), "c" * 40, "t", "s", "a", values=values)  # type: ignore[arg-type]
    return asyncio.run(check.evaluate(data))


def test_a_new_method_the_tests_check_is_approved(repo: Repo, tmp_path: Path) -> None:
    assert isinstance(judge(repo, tmp_path, TEST_THAT_CHECKS), Approved)


@pytest.mark.parametrize("test_text", [TEST_THAT_ONLY_CALLS, TEST_THAT_IS_EMPTY])
def test_a_test_that_checks_nothing_is_rejected(repo: Repo, tmp_path: Path, test_text: str) -> None:
    result = judge(repo, tmp_path, test_text)

    assert isinstance(result, Rejected)
    if test_text is TEST_THAT_ONLY_CALLS:  # the line runs, and no change to it is noticed
        assert result.findings[0].measured == 0.0
        assert any(f.target == "src/Calc/Calc.cs:12" for f in result.findings)
    else:  # no test runs the new line at all
        assert "src/Calc/Calc.cs:12" in {f.target for f in result.findings}
        assert "no test runs this new line" in result.findings[0].message
