"""A repair run end to end in each language, with the real tools: only the agents are scripted.

The test author adds a test that fails on the defect, the implementer fixes the production code,
and the pipeline in between is the real one (format, lint, build, tests, freeze, mutation). A
language whose tool chain is not installed is skipped.
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from forja.core.adapters import LanguageAdapter
from forja.core.artifacts.llm_output import DeclaredTest
from forja.core.artifacts.models import Producer
from forja.core.events import EventLog
from forja.core.states import AttemptState, ExecutionState, Level, StageState, TaskState
from forja.languages.csharp import CSharpAdapter
from forja.languages.csharp.adapter import DOTNET_ENVIRONMENT
from forja.languages.go import GoAdapter
from forja.languages.go.adapter import GO_ENVIRONMENT, find_golangci_lint
from forja.languages.java import JavaAdapter
from forja.languages.java.adapter import JAVA_ENVIRONMENT
from forja.languages.typescript import TypeScriptAdapter
from forja.languages.typescript.adapter import NODE_ENVIRONMENT
from forja.orchestrator.config import ExecutorRef, FixRequest, Objective
from forja.orchestrator.measurer import PipelineMeasurer
from forja.orchestrator.ports import PlannedTask
from tests.checks.support import (
    BLOCKING,
    FAKE_GITLEAKS,
    LIZARD,
    OBSERVATION,
    PREMISE,
    registry_of,
)
from tests.languages.csharp.test_mutation import LIBRARY, SOLUTION
from tests.languages.csharp.test_mutation import TESTS as CS_TESTS_PROJECT
from tests.languages.java.test_mutation import POM
from tests.languages.typescript.test_mutation import link
from tests.orchestrator.fakes import ScriptedAgent, ScriptedPlanner
from tests.orchestrator.harness import IDENTITY, World, minimal_env

pytestmark = pytest.mark.slow

FIX = FixRequest.model_validate(
    {
        "description": "add returns the difference",
        "criteria": {
            "criteria": [{"id": 1, "text": "add sums its operands", "level": "unit", "origin": "x"}]
        },
    }
)


@dataclass(frozen=True)
class Project:
    """A small project with one defect, the test that shows it and the change that fixes it."""

    language: str
    tools: tuple[str, ...]
    base: Mapping[str, str]
    tests: Mapping[str, str]  # what the test author writes
    declared: DeclaredTest
    fix: Mapping[str, str]  # what the implementer writes
    scope: tuple[str, ...]
    env: frozenset[str]
    adapter: Callable[[Path], LanguageAdapter]
    where: str = ""  # the directory of the project in the repository; empty for the root
    also: tuple[Callable[[Path], LanguageAdapter], ...] = ()  # the other projects of the repository
    packages_at: str | None = None  # where the installed TypeScript packages are linked
    label: str = ""


GO = Project(
    language="go",
    tools=("go",),
    base={
        "go.mod": "module example.com/calc\n\ngo 1.22\n",
        "calc.go": "package calc\n\nfunc Add(a, b int) int {\n\treturn a - b\n}\n",
        "calc_test.go": (
            'package calc\n\nimport "testing"\n\n'
            'func TestZero(t *testing.T) {\n\tif Add(0, 0) != 0 {\n\t\tt.Fatal("zero")\n\t}\n}\n'
        ),
    },
    tests={
        "calc_add_test.go": (
            'package calc\n\nimport "testing"\n\n'
            'func TestAdd(t *testing.T) {\n\tif Add(2, 3) != 5 {\n\t\tt.Fatal("add")\n\t}\n}\n'
        )
    },
    declared=DeclaredTest(
        criterion_id=1, test_id="example.com/calc/TestAdd", path="calc_add_test.go"
    ),
    fix={"calc.go": "package calc\n\nfunc Add(a, b int) int {\n\treturn a + b\n}\n"},
    scope=("calc.go",),
    env=frozenset(GO_ENVIRONMENT | {"TEMP", "TMP", "USERPROFILE"}),
    adapter=lambda root: GoAdapter(golangci_lint=find_golangci_lint()),
)

CALC_CS = (
    "namespace Calc;\n\npublic static class Calculator\n{\n"
    "    public static int Add(int a, int b)\n    {\n        return a - b;\n    }\n}\n"
)
CS_TEST = (
    "using Calc;\n\nnamespace Calc.Tests;\n\npublic class {name}\n{{\n"
    "    [Fact]\n    public void {method}()\n    {{\n"
    "        Assert.Equal({want}, Calculator.Add({a}, {b}));\n    }}\n}}\n"
)
CSHARP = Project(
    language="csharp",
    tools=("dotnet",),
    base={
        "Calc.sln": SOLUTION,
        ".gitignore": "bin/\nobj/\n.forja-coverage/\n",
        "src/Calc/Calc.csproj": LIBRARY,
        "src/Calc/Calc.cs": CALC_CS,
        "tests/Calc.Tests/Calc.Tests.csproj": CS_TESTS_PROJECT,
        "tests/Calc.Tests/ZeroTests.cs": CS_TEST.format(
            name="ZeroTests", method="Zero", want=0, a=0, b=0
        ),
    },
    tests={
        "tests/Calc.Tests/AddTests.cs": CS_TEST.format(
            name="AddTests", method="Adds", want=5, a=2, b=3
        )
    },
    declared=DeclaredTest(
        criterion_id=1, test_id="Calc.Tests.AddTests.Adds", path="tests/Calc.Tests/AddTests.cs"
    ),
    fix={"src/Calc/Calc.cs": CALC_CS.replace("a - b", "a + b")},
    scope=("src/Calc/Calc.cs",),
    env=frozenset(
        {n.upper() for n in DOTNET_ENVIRONMENT} | {"TEMP", "TMP", "SYSTEMROOT", "PATHEXT"}
    ),
    adapter=lambda root: CSharpAdapter(),
)
JAVA_CALC = (
    "package com.acme;\n\npublic final class Calc {\n  private Calc() {}\n\n"
    "  public static int add(int a, int b) {\n    return a - b;\n  }\n}\n"
)
JAVA_TEST = (
    "package com.acme;\n\nimport static org.junit.jupiter.api.Assertions.assertEquals;\n\n"
    "import org.junit.jupiter.api.Test;\n\nclass {name} {{\n"
    "  @Test\n  void {method}() {{\n    assertEquals({want}, Calc.add({a}, {b}));\n  }}\n}}\n"
)
JAVA = Project(
    language="java",
    tools=("mvn", "java"),
    base={
        "pom.xml": POM,
        ".gitignore": "target/\n",
        "src/main/java/com/acme/Calc.java": JAVA_CALC,
        "src/test/java/com/acme/ZeroTest.java": JAVA_TEST.format(
            name="ZeroTest", method="zero", want=0, a=0, b=0
        ),
    },
    tests={
        "src/test/java/com/acme/AddTest.java": JAVA_TEST.format(
            name="AddTest", method="adds", want=5, a=2, b=3
        )
    },
    declared=DeclaredTest(
        criterion_id=1, test_id="com.acme.AddTest.adds", path="src/test/java/com/acme/AddTest.java"
    ),
    fix={"src/main/java/com/acme/Calc.java": JAVA_CALC.replace("a - b", "a + b")},
    scope=("src/main/java/com/acme/Calc.java",),
    env=frozenset({n.upper() for n in JAVA_ENVIRONMENT} | {"TEMP", "TMP", "SYSTEMROOT", "PATHEXT"}),
    adapter=lambda root: JavaAdapter(),
)
BUILD_GRADLE = """plugins {
    id 'java'
}

repositories {
    mavenCentral()
}

dependencies {
    testImplementation 'org.junit.jupiter:junit-jupiter:5.11.4'
    testRuntimeOnly 'org.junit.platform:junit-platform-launcher'
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

test {
    useJUnitPlatform()
}
"""
GRADLE = replace(
    JAVA,
    language="gradle",
    tools=("gradle", "java"),
    base={
        **{k: v for k, v in JAVA.base.items() if k not in ("pom.xml", ".gitignore")},
        "build.gradle": BUILD_GRADLE,
        "settings.gradle": "rootProject.name = 'calc'\n",
        ".gitignore": "build/\n.gradle/\n",
    },
)
TS_PACKAGES = {"vitest": "5.0.5", "@vitest/coverage-v8": "5.0.5", "typescript": "5.9.3"}
TS_CALC = "export function add(a: number, b: number): number {\n  return a - b;\n}\n"
TS_TEST = (
    'import {{ expect, test }} from "vitest";\nimport {{ add }} from "./calc";\n\n'
    'test("{name}", () => {{\n  expect(add({a}, {b})).toBe({want});\n}});\n'
)
TYPESCRIPT = Project(
    language="typescript",
    tools=("node", "npm"),
    base={
        "package.json": json.dumps(
            {"name": "calc", "type": "module", "devDependencies": TS_PACKAGES}
        ),
        "tsconfig.json": json.dumps(
            {
                "compilerOptions": {
                    "strict": True,
                    "target": "es2022",
                    "module": "esnext",
                    "moduleResolution": "bundler",
                    "skipLibCheck": True,
                }
            }
        ),
        ".gitignore": "node_modules\n.forja-coverage/\n",
        "calc.ts": TS_CALC,
        "zero.test.ts": TS_TEST.format(name="zero", a=0, b=0, want=0),
    },
    tests={"add.test.ts": TS_TEST.format(name="adds", a=2, b=3, want=5)},
    declared=DeclaredTest(criterion_id=1, test_id="add.test.ts > adds", path="add.test.ts"),
    fix={"calc.ts": TS_CALC.replace("a - b", "a + b")},
    scope=("calc.ts",),
    env=frozenset(NODE_ENVIRONMENT | {"TEMP", "TMP", "USERPROFILE", "SYSTEMROOT", "PATHEXT"}),
    adapter=lambda root: TypeScriptAdapter(),
)
PROJECTS = [GO, CSHARP, JAVA, GRADLE, TYPESCRIPT]


def under(project: Project, where: str) -> Project:
    """The same project in a directory of a repository that has nothing at its root."""

    def move(files: Mapping[str, str]) -> dict[str, str]:
        return {f"{where}/{name}": text for name, text in files.items()}

    declared = project.declared
    # a TypeScript test id starts with the path of its file; the others name a test without one
    test_id = (
        f"{where}/{declared.test_id}" if project.language == "typescript" else declared.test_id
    )
    return replace(
        project,
        base=move(project.base),
        tests=move(project.tests),
        fix=move(project.fix),
        scope=tuple(f"{where}/{path}" for path in project.scope),
        declared=DeclaredTest(
            criterion_id=declared.criterion_id, test_id=test_id, path=f"{where}/{declared.path}"
        ),
        where=where,
    )


IN_A_DIRECTORY = [under(p, "svc") for p in PROJECTS]
WEB = under(TYPESCRIPT, "web")
BOTH = {**GO.base, **WEB.base}  # Go at the root of the repository, TypeScript in ``web``
MIXED = [
    replace(
        WEB,
        base=BOTH,
        tools=(*GO.tools, *WEB.tools),
        env=GO.env | WEB.env,
        also=(GO.adapter,),
        packages_at="web",
        label="go-and-typescript-fixing-typescript",
    ),
    replace(
        GO,
        base=BOTH,
        tools=(*GO.tools, *WEB.tools),
        env=GO.env | WEB.env,
        also=(WEB.adapter,),
        packages_at="web",
        label="go-and-typescript-fixing-go",
    ),
]
EVERY_PROJECT = [*PROJECTS, *IN_A_DIRECTORY, *MIXED]


def name_of(project: Project) -> str:
    if project.label:
        return project.label
    return f"{project.language}-in-{project.where}" if project.where else project.language


@pytest.fixture(scope="session")
def ts_packages(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The TypeScript project's packages, installed once for the whole run."""
    npm = shutil.which("npm")
    if npm is None:
        pytest.skip("typescript: the tool chain is not installed")
    root = tmp_path_factory.mktemp("ts-packages")
    (root / "package.json").write_text(TYPESCRIPT.base["package.json"], encoding="utf-8")
    done = subprocess.run(
        [npm, "install", "--no-audit", "--no-fund", "--loglevel=error"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        pytest.skip(f"typescript: the packages could not be installed: {done.stderr[-300:]}")
    return root / "node_modules"


def host_env(project: Project, base: Mapping[str, str]) -> dict[str, str]:
    """What a real run inherits besides the minimal environment."""
    return {**base, **{k: v for k, v in os.environ.items() if k.upper() in project.env}}


def commit(path: Path, files: Mapping[str, str], env: Mapping[str, str]) -> None:
    for relative, text in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    git = [
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
        subprocess.run([*git, *args], cwd=path, env=dict(env), check=True, capture_output=True)


def world_of(project: Project, tmp_path: Path, packages: Path | None = None) -> World:
    (tmp_path / "home").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    commit(repo, project.base, minimal_env(tmp_path / "home"))
    if packages is not None:
        link(
            packages, repo / (project.where if project.packages_at is None else project.packages_at)
        )
    found = World.at(tmp_path)
    found.config = found.config.model_copy(
        update={
            "objective": Objective.FIX,
            "fix": FIX,
            "test_author": ExecutorRef(name="author-cli", path="/bin/author", version="2.0"),
        }
    )
    return found


def repair(  # type: ignore[no-untyped-def]
    project: Project, tmp_path: Path, packages: Path | None, *fixes: Mapping[str, str]
):
    world = world_of(project, tmp_path, packages)
    log = world.open()
    settings = {
        n: BLOCKING
        for n in ("scope", "secrets", "format", "lint", "build", "tests", "red_green", "freeze")
    }
    measurer = PipelineMeasurer(
        adapters=registry_of(project.adapter(tmp_path), *(a(tmp_path) for a in project.also)),
        git=world.repo.git,
        log=log,
        workdir=world.run_dir / "measure",
        host_env=host_env(project, world.repo.env),
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
    task = PlannedTask(
        "add returns the difference",
        project.scope,
        8,
        400,
        stages=(Producer.TEST_AUTHOR, Producer.IMPLEMENTER),
    )
    author = ScriptedAgent(edits=[project.tests], tests=(project.declared,))
    implementer = ScriptedAgent(edits=list(fixes) or [project.fix])
    outcome, log = world.run(
        log=log,
        measurer=measurer,
        agent=implementer,
        agents={Producer.TEST_AUTHOR: author},
        planner=ScriptedPlanner((task,)),
        extra={"test_files": measurer.test_files, "test_root": ""},
    )
    return world, outcome, log


def packages_of(project: Project, request: pytest.FixtureRequest) -> Path | None:
    if project.language != "typescript" and project.packages_at is None:
        return None
    found: Path = request.getfixturevalue("ts_packages")
    return found


def why(log: EventLog) -> str:
    """The reasons attempts were rejected, for a failure to explain itself."""
    return "; ".join(
        f"{attempt} {log.state.state_of(Level.ATTEMPT, attempt)}: "
        f"{log.state.transition_payload(Level.ATTEMPT, attempt, AttemptState.REJECTED)}"
        for stage in ("t1-t", "t1-s")
        for attempt in log.state.children_of(Level.ATTEMPT, stage)
    )


@pytest.mark.parametrize("project", EVERY_PROJECT, ids=name_of)
def test_a_repair_goes_through_every_gate_and_is_delivered(
    project: Project, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    if any(shutil.which(tool) is None for tool in project.tools):
        pytest.skip(f"{project.language}: the tool chain is not installed")

    world, outcome, log = repair(project, tmp_path, packages_of(project, request))

    assert outcome.state is ExecutionState.DELIVERED, log.state
    assert log.state.state_of(Level.TASK, "t1") is TaskState.INTEGRATED, why(log)
    assert log.state.state_of(Level.STAGE, "t1-t") is StageState.COMMITTED
    for relative in (*project.tests, *project.fix):
        assert world.repo.sh("show", f"{world.workspace.run_branch}:{relative}")


@pytest.mark.parametrize("project", EVERY_PROJECT, ids=name_of)
def test_an_implementer_that_touches_the_frozen_tests_is_sent_back(
    project: Project, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    if any(shutil.which(tool) is None for tool in project.tools):
        pytest.skip(f"{project.language}: the tool chain is not installed")
    path, text = next(iter(project.tests.items()))
    # a comment: the formatter that runs before the gates would take a blank line back out
    comment = "#" if project.language == "python" else "//"
    tampered = {**project.fix, path: f"{text}{comment} touched\n"}

    _, outcome, log = repair(
        project, tmp_path, packages_of(project, request), tampered, project.fix
    )

    assert outcome.state is ExecutionState.DELIVERED, log.state
    rejected = log.state.transition_payload(Level.ATTEMPT, "t1-a1", AttemptState.REJECTED)
    assert rejected is not None and "only production files may change" in str(rejected["reason"])
    assert log.state.state_of(Level.ATTEMPT, "t1-a2") is AttemptState.COMMITTED
