"""The probe in real runs: Maven and Gradle, with the JDK, the tools and the network they need.

For each tool: the tests of each line from the listener's data, the round trip of those ids into
a filtered run, the stop at the first failing test with its id read back, and the verdicts of the
mutation check with and without the tests of each line.
"""

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from forja.checks.changes import read_changes
from forja.checks.mutation import MutationCheck, failed_test
from forja.checks.tooling import Tools
from forja.core.adapters import CommandPlan, NotSupported, Operation, OperationRequest, ToolOutput
from forja.core.events import EventLog
from forja.core.gates import GateInput
from forja.core.gates.models import Approved, Rejected
from forja.core.reports import ReportFormat
from forja.core.reports.readers import read_test_lines
from forja.core.states import ExecutionState, Level
from forja.languages.java import JavaAdapter, selection
from forja.languages.java.adapter import JAVA_ENVIRONMENT
from tests.checks.support import RUN, sinks
from tests.core.events.support import FakeClock
from tests.core.idempotency.conftest import Repo

pytestmark = pytest.mark.slow

MAVEN_POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.acme</groupId>
  <artifactId>calc</artifactId>
  <version>1.0</version>
  <properties>
    <maven.compiler.release>17</maven.compiler.release>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter</artifactId>
      <version>5.10.2</version>
      <scope>test</scope>
    </dependency>
  </dependencies>
</project>
"""
GRADLE_BUILD = """plugins { id 'java' }
java { sourceCompatibility = JavaVersion.VERSION_17 }
repositories { mavenCentral() }
dependencies {
  testImplementation 'org.junit.jupiter:junit-jupiter:5.10.2'
  testRuntimeOnly 'org.junit.platform:junit-platform-launcher'
}
test { useJUnitPlatform() }
"""
CALC = """package com.acme;

public final class Calc {
  public static int add(int a, int b) {
    return a + b;
  }

  public static int sub(int a, int b) {
    return a - b;
  }
}
"""
CALC_TEST = """package com.acme;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

class CalcTest {
  @Test
  void adds() {
    assertEquals(5, Calc.add(2, 3));
  }

  @Nested
  class Subtracao {
    @Test
    void subtracts() {
      assertEquals(2, Calc.sub(3, 1));
    }
  }
}
"""
SUM_TEST = """package com.acme;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.junit.jupiter.api.Test;

class SumTest {
  @Test
  void soma() {
    assertEquals(7, Calc.add(3, 4));
  }

  @ParameterizedTest
  @ValueSource(ints = {1, 2})
  void dobro(int x) {
    assertEquals(2 * x, Calc.add(x, x));
  }
}
"""
PRODUCTION = "src/main/java/com/acme/Calc.java"
ADD_LINE = 5  # ``return a + b;``
SUB_LINE = 9
ADDS = "com.acme.CalcTest.adds"
SOMA = "com.acme.SumTest.soma"
DOBRO = "com.acme.SumTest.dobro"
SUBTRACTS = "com.acme.CalcTest$Subtracao.subtracts"


def write_project(root: Path, tool: str) -> None:
    files = {
        "pom.xml": MAVEN_POM,
        "build.gradle": GRADLE_BUILD,
        "settings.gradle": "rootProject.name = 'calc'\n",
        PRODUCTION: CALC,
        "src/test/java/com/acme/CalcTest.java": CALC_TEST,
        "src/test/java/com/acme/SumTest.java": SUM_TEST,
    }
    for name, text in files.items():
        if _is_other_tool(name, tool):
            continue
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def _is_other_tool(name: str, tool: str) -> bool:
    maven_file = name == "pom.xml"
    gradle_file = name.endswith(".gradle")
    return (maven_file and tool != "maven") or (gradle_file and tool != "gradle")


def adapter_for(tool: str, root: Path, cache: Path) -> JavaAdapter:
    adapter = (
        JavaAdapter(cache_dir=cache)
        if tool == "maven"
        else JavaAdapter(maven=("m",), gradle=("gradle",), cache_dir=cache)
    )
    if tool == "gradle":
        adapter.detect(root)
    return adapter


def run(plan: CommandPlan) -> subprocess.CompletedProcess[str]:
    """Runs the plan with its output in a file, not a pipe: a Gradle daemon the run starts keeps
    the handles it inherits, and a pipe would be waited on until that daemon ends."""
    env = {**os.environ, **plan.env_set}
    with tempfile.TemporaryFile() as output:
        done = subprocess.run(
            plan.argv,
            cwd=plan.cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=300,
            check=False,
        )
        output.seek(0)
        text = output.read().decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(done.args, done.returncode, text, "")


def tool_available(tool: str) -> bool:
    name = "mvn" if tool == "maven" else "gradle"
    return shutil.which(name) is not None and shutil.which("java") is not None


@pytest.fixture(params=["maven", "gradle"])
def tool(request: pytest.FixtureRequest) -> str:
    if not tool_available(request.param):
        pytest.skip(f"{request.param} and a JDK are not installed")
    return str(request.param)


@pytest.fixture
def project(tmp_path: Path, tool: str) -> Path:
    root = tmp_path / "calc"
    write_project(root, tool)
    return root


@pytest.fixture
def adapter(project: Path, tool: str, tmp_path: Path) -> JavaAdapter:
    return adapter_for(tool, project, tmp_path / "cache")


def line_tests(adapter: JavaAdapter, root: Path) -> dict[str, dict[int, frozenset[str]]]:
    plan = adapter.plan(Operation.TEST_LINES, OperationRequest(root=root))
    assert isinstance(plan, CommandPlan)
    result = run(plan)
    assert result.returncode == 0, result.stdout[-2000:]
    spec = adapter.report(Operation.TEST_LINES)
    assert not isinstance(spec, NotSupported)
    text = (root / spec.path).read_text(encoding="utf-8")
    found = read_test_lines(
        ReportFormat.TEST_LINES_JSON, {"linhas.json": text}, root, ("src/main/java",)
    )
    return {path: dict(lines) for path, lines in found.items()}


def test_each_line_names_the_tests_that_run_it_and_only_those(
    adapter: JavaAdapter, project: Path
) -> None:
    lines = line_tests(adapter, project)
    add = lines[PRODUCTION]
    assert add[ADD_LINE] == frozenset({ADDS, SOMA, DOBRO})
    assert add[SUB_LINE] == frozenset({SUBTRACTS})
    assert 1 not in lines[PRODUCTION]  # the class line runs no test


def test_the_ids_of_a_line_run_exactly_their_tests(adapter: JavaAdapter, project: Path) -> None:
    ids = tuple(sorted(line_tests(adapter, project)[PRODUCTION][ADD_LINE]))
    plan = adapter.plan(Operation.TEST, OperationRequest(root=project, tests=ids, rerun=True))
    assert isinstance(plan, CommandPlan)
    result = run(plan)
    assert result.returncode == 0, result.stdout[-2000:]
    ran = executed_classes(project)
    assert ran == {"com.acme.CalcTest", "com.acme.SumTest"}
    assert executed_count(project) == 4  # adds, soma and the two cases of dobro
    assert not any("subtracts" in name for name in executed_names(project))
    assert {selection.selector(i) for i in ids} == {ADDS, SOMA, DOBRO}


def test_a_failing_filtered_run_stops_and_names_the_test_that_failed(
    adapter: JavaAdapter, project: Path
) -> None:
    (project / PRODUCTION).write_text(CALC.replace("a + b", "a - b", 1), encoding="utf-8")
    asked = (ADDS, SUBTRACTS, SOMA, DOBRO)
    plan = adapter.plan(Operation.TEST, OperationRequest(root=project, tests=asked, rerun=True))
    assert isinstance(plan, CommandPlan)
    result = run(plan)
    assert result.returncode != 0
    output = project / "run.log"
    output.write_text(result.stdout + result.stderr, encoding="utf-8")
    assert failed_test(adapter, ToolOutput(result.returncode, output, project), asked) == ADDS
    assert len(failed_names(project)) == 1
    assert SUBTRACTS not in executed_names(project)


@pytest.mark.parametrize("hide_lines", [False, True])
@pytest.mark.parametrize("strong", [True, False])
def test_the_mutation_check_judges_the_same_with_and_without_the_lines(
    tool: str, tmp_path: Path, repo: Repo, strong: bool, hide_lines: bool
) -> None:
    verdict_type = verdict(tool, tmp_path, repo, strong=strong, hide_lines=hide_lines)
    assert verdict_type is (Approved if strong else Rejected)


TWICE = """  public static int twice(int a) {
    return a + a;
  }

  public static int sub"""
TWICE_TEST = """
  @Test
  void twice() {
    assertEquals(8, Calc.twice(4));
  }
}
"""


def verdict(tool: str, work: Path, repo: Repo, *, strong: bool, hide_lines: bool) -> type:
    """The verdict of the mutation check on a new method, checked by a test that compares its
    result (strong) or by one that only calls it (weak, the mutant survives)."""
    files = {
        "pom.xml": MAVEN_POM,
        "build.gradle": GRADLE_BUILD,
        "settings.gradle": "rootProject.name = 'calc'\n",
        ".gitignore": "target/\nbuild/\n.gradle/\n.forja-test-lines/\n",
        PRODUCTION: CALC,
        "src/test/java/com/acme/CalcTest.java": CALC_TEST,
    }
    for name, text in files.items():
        if _is_other_tool(name, tool):
            continue
        path = repo.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    repo.sh("add", "-A")
    repo.sh("commit", "-m", "project")
    body = TWICE_TEST if strong else TWICE_TEST.replace("assertEquals(8, Calc.twice(4));", "")
    (repo.path / PRODUCTION).write_text(
        CALC.replace("  public static int sub", TWICE), encoding="utf-8", newline="\n"
    )
    test_file = repo.path / "src/test/java/com/acme/CalcTest.java"
    test_file.write_text(CALC_TEST[: CALC_TEST.rindex("}")] + body, encoding="utf-8", newline="\n")
    log = EventLog.open(work / "events.jsonl", RUN, clock=FakeClock())
    log.append_transition(Level.EXECUTION, RUN, None, ExecutionState.PREPARING)
    wanted = {name.upper() for name in JAVA_ENVIRONMENT} | {"TEMP", "TMP", "SYSTEMROOT", "PATHEXT"}
    host_env = {**repo.env, **{k: v for k, v in os.environ.items() if k.upper() in wanted}}
    tools = Tools(
        sinks=sinks, log=log, level=Level.ATTEMPT, target="a", logs=work / "logs", host_env=host_env
    )
    changes = asyncio.run(read_changes(repo.git.at(repo.path)))
    adapter: JavaAdapter = adapter_for(tool, repo.path, work / "cache")
    if hide_lines:
        adapter = _Hidden(adapter)
    check = MutationCheck(
        tools,
        adapter,
        repo.path,
        changes,
        (PRODUCTION,),
        host_env,
        language="java",
        history=work / "history.json",
    )
    data = GateInput(Path("."), "c" * 40, "t", "s", "a", values={})
    result = asyncio.run(check.evaluate(data))
    assert isinstance(result, (Approved, Rejected)), result
    return type(result)


class _Hidden(JavaAdapter):
    """The adapter without its line report: the mutation check judges without the tests of lines."""

    def __init__(self, inner: JavaAdapter) -> None:
        super().__init__(maven=("m",))
        self.__dict__.update(inner.__dict__)

    def report(self, operation: Operation):  # type: ignore[no-untyped-def]
        if operation is Operation.TEST_LINES:
            return NotSupported(operation.value, "hidden for the comparison")
        return super().report(operation)


def executed_names(root: Path) -> list[str]:
    """The test cases a run executed: the ones it reported and did not skip."""
    import xml.etree.ElementTree as ElementTree

    names: list[str] = []
    for path in _reports(root):
        for case in ElementTree.parse(path).getroot().iter("testcase"):
            if case.find("skipped") is None:
                names.append(f"{case.get('classname', '')}.{case.get('name', '')}")
    return names


def executed_classes(root: Path) -> set[str]:
    return {name.rsplit(".", 1)[0].split("(")[0].split(".[")[0] for name in executed_names(root)}


def executed_count(root: Path) -> int:
    return len(executed_names(root))


def failed_names(root: Path) -> list[str]:
    import xml.etree.ElementTree as ElementTree

    found: list[str] = []
    for path in _reports(root):
        for case in ElementTree.parse(path).getroot().iter("testcase"):
            if case.find("failure") is not None:
                found.append(f"{case.get('classname', '')}.{case.get('name', '')}")
    return found


def _reports(root: Path) -> list[Path]:
    return list((root / "target" / "surefire-reports").glob("TEST-*.xml")) + list(
        (root / "build" / "test-results" / "test").glob("TEST-*.xml")
    )
