"""The agent's few commands: fixed, narrow, and unable to turn into anything else."""

import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from forja.cli import facade
from forja.cli.facade import (
    PYTHON_VARIABLE,
    Refused,
    asked_for,
    named,
    plan_for,
    projects_in,
    safe_paths,
    tail,
)
from forja.core.adapters import (
    CommandPlan,
    EvidenceDraft,
    NotSupported,
    Operation,
    OperationRequest,
    Parsed,
    ToolOutput,
    Unparseable,
)
from forja.core.adapters.common import ParseResult
from forja.core.evidence import Target, TargetKind


def run_facade(argv: Sequence[str], cwd: Path, environ: Mapping[str, str]) -> int:
    """The facade as ``forja agente`` runs it, to the end."""
    return asyncio.run(facade.run_facade(argv, cwd, environ))


def run_plan(
    plan: CommandPlan,
    environ: Mapping[str, str],
    project: object = None,
    operation: Operation | None = None,
) -> int:
    return asyncio.run(facade.run_plan(plan, environ, project, operation))  # type: ignore[arg-type]


def test_paths_inside_the_project_are_accepted(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert safe_paths(tmp_path, ["src", "src/a.py"]) == ["src", "src/a.py"]


@pytest.mark.parametrize(
    "bad", ["--collect-only", "-x", "../outside", "src/../../x", "", "/etc/passwd", "C:\\Windows"]
)
def test_options_and_ways_out_are_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(Refused):
        safe_paths(tmp_path, [bad])


def test_a_path_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    with pytest.raises(Refused, match="não encontrado"):
        safe_paths(tmp_path, ["nada.py"])


def test_a_link_pointing_out_of_the_project_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "fora"
    outside.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    try:
        (project / "ponte").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("no permission to create links here")
    with pytest.raises(Refused):
        safe_paths(project, ["ponte"])


def test_long_output_keeps_the_end_and_says_what_was_left_out() -> None:
    text = "\n".join(str(number) for number in range(200))
    shown = tail(text, 10).splitlines()
    assert shown[0] == "[... 190 linhas anteriores omitidas]"
    assert shown[-1] == "199"
    assert len(shown) == 11
    assert tail("curto", 10) == "curto"


def test_the_usage_is_shown_without_a_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_facade([], Path.cwd(), {}) == 2
    assert "uso:" in capsys.readouterr().out


@pytest.mark.parametrize("what", ["git", "bash", "rm", "testar; git status", "--help"])
def test_the_facade_does_not_run_git_or_anything_it_does_not_name(
    what: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_facade([what], tmp_path, {}) == 2
    assert "uso:" in capsys.readouterr().out


def test_a_path_that_is_not_allowed_runs_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_facade(["lint", "--fix"], tmp_path, {}) == 2
    assert "caminho não permitido" in capsys.readouterr().out


# -- which projects, and what each is asked ------------------------------------------------------


class Project:
    """A project of the copy, as the facade sees one: it notes what it is asked to plan."""

    def __init__(
        self,
        base: str = ".",
        language: str = "go",
        *,
        filters: bool = True,
        offers: bool = True,
        exit_code: int = 0,
        reads: ParseResult | None = None,
    ) -> None:
        self.base = base
        self.language = language
        self.asked: list[tuple[Operation, OperationRequest]] = []
        self._filters = filters
        self._offers = offers
        self._exit_code = exit_code
        self._reads = reads if reads is not None else Parsed(())

    def holds(self, path: str) -> bool:
        return self.base == "." or path.startswith(f"{self.base}/")

    def plan(self, operation: Operation, request: OperationRequest) -> CommandPlan | NotSupported:
        self.asked.append((operation, request))
        if not self._offers:
            return NotSupported(operation.value, "the project does not declare that tool")
        if (request.files or request.test_files) and not self._filters:
            return NotSupported(operation.value, "filtering the tests by file is not offered")
        code = f"print('ran {self.base}'); raise SystemExit({self._exit_code})"
        return CommandPlan((sys.executable, "-c", code), request.root)

    def parse(self, operation: Operation, output: ToolOutput) -> ParseResult:
        return self._reads


def host() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key.upper() == "SYSTEMROOT"}


def failed(test: str) -> EvidenceDraft:
    return EvidenceDraft("test.outcome", Target(kind=TargetKind.SYMBOL, id=test), "failed", "")


def test_the_tests_of_a_python_project_run_with_the_python_of_the_repository(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")

    (project,) = projects_in(tmp_path, {PYTHON_VARIABLE: "/p/python"})
    plan = plan_for(project, Operation.TEST, tmp_path, [])

    assert isinstance(plan, CommandPlan)
    assert plan.argv[:3] == ("/p/python", "-m", "pytest")
    assert "-x" in plan.argv  # stops at the first test that fails


def test_a_directory_with_no_manifest_has_no_project(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert projects_in(tmp_path, {}) == []


def test_no_path_calls_for_every_project_and_a_path_for_those_around_it() -> None:
    api, web, root = Project("services/api"), Project("services/web"), Project(".")

    assert all(asked_for(p, []) for p in (api, web, root))  # type: ignore[arg-type]
    assert asked_for(api, ["services/api/main.go"])  # type: ignore[arg-type]
    assert asked_for(api, ["services\\api\\main.go"])  # type: ignore[arg-type]
    assert asked_for(api, ["services/api"])  # type: ignore[arg-type]
    assert asked_for(api, ["services"])  # type: ignore[arg-type]
    assert asked_for(api, ["."])  # type: ignore[arg-type]
    assert not asked_for(web, ["services/api/main.go"])  # type: ignore[arg-type]
    assert not asked_for(api, ["services/ap"])  # type: ignore[arg-type]
    assert asked_for(root, ["anything.go"])  # type: ignore[arg-type]


def test_the_tests_are_asked_to_stop_at_the_first_failure_and_the_lint_is_not(
    tmp_path: Path,
) -> None:
    project = Project()

    plan_for(project, Operation.TEST, tmp_path, [])  # type: ignore[arg-type]
    plan_for(project, Operation.LINT, tmp_path, [])  # type: ignore[arg-type]

    assert project.asked == [
        (Operation.TEST, OperationRequest(root=tmp_path, rerun=True)),
        (Operation.LINT, OperationRequest(root=tmp_path)),
    ]


def test_the_paths_inside_the_project_narrow_what_it_runs(tmp_path: Path) -> None:
    project = Project("api")
    paths = ["api/a_test.go", "web/b.ts", "api\\c.go"]

    plan_for(project, Operation.TEST, tmp_path, paths)  # type: ignore[arg-type]
    plan_for(project, Operation.LINT, tmp_path, paths)  # type: ignore[arg-type]

    inside = ("api/a_test.go", "api\\c.go")
    assert project.asked == [
        (Operation.TEST, OperationRequest(root=tmp_path, rerun=True, test_files=inside)),
        (Operation.LINT, OperationRequest(root=tmp_path, files=inside)),
    ]


def test_a_path_above_the_project_asks_for_the_whole_of_it(tmp_path: Path) -> None:
    project = Project("services/api")

    plan_for(project, Operation.TEST, tmp_path, ["services"])  # type: ignore[arg-type]

    assert project.asked == [(Operation.TEST, OperationRequest(root=tmp_path, rerun=True))]


def test_a_tool_that_cannot_be_told_the_paths_runs_the_whole_project(tmp_path: Path) -> None:
    project = Project(filters=False)

    plan = plan_for(project, Operation.TEST, tmp_path, ["a_test.go"])  # type: ignore[arg-type]

    assert isinstance(plan, CommandPlan)
    assert [request.test_files for _, request in project.asked] == [("a_test.go",), ()]


# -- what the agent is shown ---------------------------------------------------------------------


def test_the_failed_tests_and_the_lint_violations_are_named_as_the_adapter_reads_them() -> None:
    output = ToolOutput(1, Path("log"))
    drafts = (
        failed("pkg/TestSub"),
        EvidenceDraft(
            "test.outcome", Target(kind=TargetKind.SYMBOL, id="pkg/TestAdd"), "passed", ""
        ),
        EvidenceDraft(
            "test.outcome", Target(kind=TargetKind.SYMBOL, id="pkg/TestMul"), "error", ""
        ),
        EvidenceDraft("test.collected", Target(kind=TargetKind.MODULE, id="pkg"), 3, "tests"),
        EvidenceDraft("lint.errcheck", Target(kind=TargetKind.FILE, id="a.go"), 2, "violations"),
    )
    project = Project(reads=Parsed(drafts))

    assert named(project, Operation.TEST, output) == [  # type: ignore[arg-type]
        "falhou: pkg/TestSub",
        "falhou: pkg/TestMul",
    ]
    assert named(project, Operation.LINT, output) == ["a.go: errcheck (2)"]  # type: ignore[arg-type]


def test_an_output_the_adapter_cannot_read_names_nothing() -> None:
    project = Project(reads=Unparseable("not what it expected"))

    assert named(project, Operation.TEST, ToolOutput(1, Path("log"))) == []  # type: ignore[arg-type]


def test_the_exit_code_is_the_one_of_the_tool_and_its_output_is_shown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = CommandPlan((sys.executable, "-c", "print('seen'); raise SystemExit(3)"), tmp_path)

    assert run_plan(plan, host()) == 3
    assert capsys.readouterr().out.splitlines() == ["seen"]


def test_a_tool_that_ends_well_with_violations_read_in_its_output_does_not_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The lint of Maven is told not to fail the build: what it found is in the report."""
    found = EvidenceDraft("lint.Rule", Target(kind=TargetKind.FILE, id="A.java"), 1, "violations")
    dirty, clean = Project(reads=Parsed((found,))), Project()
    plan = CommandPlan((sys.executable, "-c", "pass"), tmp_path)

    assert run_plan(plan, host(), dirty, Operation.LINT) == 1
    assert capsys.readouterr().out.splitlines() == ["A.java: Rule (1)"]
    assert run_plan(plan, host(), clean, Operation.LINT) == 0


def test_only_the_first_twenty_are_named_and_the_rest_are_counted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = Project(reads=Parsed(tuple(failed(f"t{number:02}") for number in range(23))))
    plan = CommandPlan((sys.executable, "-c", "raise SystemExit(1)"), tmp_path)

    assert run_plan(plan, host(), project, Operation.TEST) == 1

    shown = capsys.readouterr().out.splitlines()
    assert shown[0] == "falhou: t00"
    assert shown[-2:] == ["falhou: t19", "[... e mais 3]"]
    assert len(shown) == 21


def test_a_tool_that_passes_the_time_limit_is_ended(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(facade, "TIMEOUT_SECONDS", 1)
    plan = CommandPlan((sys.executable, "-c", "import time; time.sleep(60)"), tmp_path)

    assert run_plan(plan, host()) == 124
    assert "interrompido: passou de 1 s" in capsys.readouterr().out


def test_a_tool_that_cannot_start_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = CommandPlan((str(tmp_path / "no-such-program"),), tmp_path)

    assert run_plan(plan, host()) == 127
    assert "não consegui rodar: spawn_failed" in capsys.readouterr().out


def test_the_tool_does_not_see_the_secrets_of_the_caller(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = "import os; print(sorted(k for k in os.environ if 'KEY' in k or 'TOKEN' in k))"
    plan = CommandPlan((sys.executable, "-c", code), tmp_path)
    secret = {**host(), "ANTHROPIC_API_KEY": "sk-secret", "GITHUB_TOKEN": "t"}

    assert run_plan(plan, secret) == 0
    assert capsys.readouterr().out.splitlines() == ["[]"]


# -- every project of the copy -------------------------------------------------------------------


def through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, *projects: Project) -> int:
    monkeypatch.setattr(facade, "projects_in", lambda cwd, environ: list(projects))
    return asyncio.run(facade.run_projects(name, [], tmp_path, host()))


def test_a_directory_with_no_project_runs_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_facade(["testar"], tmp_path, host()) == 2
    assert "nenhum projeto de linguagem conhecida" in capsys.readouterr().out


def test_one_project_is_run_without_a_heading(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert through(monkeypatch, tmp_path, "testar", Project()) == 0
    assert capsys.readouterr().out.splitlines() == ["ran ."]


def test_the_tests_stop_at_the_first_project_that_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = (Project("a"), Project("b", "java", exit_code=1), Project("c"))

    assert through(monkeypatch, tmp_path, "testar", *projects) == 1
    assert capsys.readouterr().out.splitlines() == [
        "== go: a ==",
        "ran a",
        "== java: b ==",
        "ran b",
    ]


def test_the_lint_goes_through_every_project_and_keeps_the_first_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = (Project("a", exit_code=3), Project("b", exit_code=1), Project("c"))

    assert through(monkeypatch, tmp_path, "lint", *projects) == 3
    assert [line for line in capsys.readouterr().out.splitlines() if line.startswith("ran")] == [
        "ran a",
        "ran b",
        "ran c",
    ]


def test_a_project_whose_tool_is_not_offered_says_why_and_fails_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert through(monkeypatch, tmp_path, "lint", Project(language="java", offers=False)) == 0
    assert capsys.readouterr().out.splitlines() == [
        "lint não se aplica a este projeto (java): the project does not declare that tool"
    ]


def test_only_the_projects_the_paths_call_for_are_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "b").mkdir()
    monkeypatch.setattr(facade, "projects_in", lambda cwd, environ: [Project("a"), Project("b")])

    assert run_facade(["lint", "b"], tmp_path, host()) == 0
    assert capsys.readouterr().out.splitlines() == ["ran b"]


# -- the real tools of a Python project ----------------------------------------------------------


def python_project(root: Path) -> None:
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")


def test_lint_runs_and_returns_the_exit_code_of_the_tool(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    python_project(tmp_path)
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("import os\n", encoding="utf-8")

    assert run_facade(["lint", "ok.py"], tmp_path, host()) == 0
    assert run_facade(["lint", "bad.py"], tmp_path, host()) != 0
    shown = capsys.readouterr().out
    assert "F401" in shown
    assert "bad.py: F401 (1)" in shown.splitlines()


def test_the_tests_run_stop_at_the_first_failure_and_leave_no_compiled_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    python_project(tmp_path)
    (tmp_path / "test_a.py").write_text("def test_a():\n    assert False\n", encoding="utf-8")
    (tmp_path / "test_b.py").write_text("def test_b():\n    assert False\n", encoding="utf-8")
    environ = {**host(), "PATH": os.environ["PATH"]}

    assert run_facade(["testar"], tmp_path, environ) == 1
    shown = capsys.readouterr().out.splitlines()
    assert [line for line in shown if line.startswith("falhou:")] == ["falhou: test_a.py::test_a"]
    assert not list(tmp_path.rglob("*.pyc"))


def test_the_measure_of_complexity_runs_on_the_path_given(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.py").write_text("def f(x):\n    return 1 if x else 2\n", encoding="utf-8")

    assert run_facade(["medir", "a.py"], tmp_path, host()) == 0
    assert "f@1-2@" in capsys.readouterr().out
