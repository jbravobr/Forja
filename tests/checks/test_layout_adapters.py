"""Format and lint over more than one language: each file goes to the adapter that claims it."""

import asyncio
from pathlib import Path

import pytest

from forja.checks.changes import Change, ChangeKind, Changes
from forja.checks.layout import FormatCheck, LintCheck, TypesCheck, skip_git_and_links
from forja.checks.tooling import Tools
from forja.core.adapters import Operation
from forja.core.events import EventLog
from forja.core.gates import (
    Approved,
    GateInput,
    Inconclusive,
    InconclusiveReason,
    NotApplicable,
    Rejected,
)
from forja.core.states import ExecutionState, Level
from tests.checks.support import RUN, Stub, draft, projects_of, sinks
from tests.core.events.support import FakeClock


def changes_of(*paths: str) -> Changes:
    return Changes(tuple(Change(ChangeKind.MODIFIED, p) for p in paths), 1, ())


def data() -> GateInput:
    return GateInput(Path("."), "c" * 40, "t1", "t1-s", "t1-a1")


@pytest.fixture
def tools(tmp_path: Path) -> Tools:
    log = EventLog.open(tmp_path / "events.jsonl", RUN, clock=FakeClock())
    log.append_transition(Level.EXECUTION, RUN, None, ExecutionState.PREPARING)
    return Tools(
        sinks=sinks,
        log=log,
        level=Level.ATTEMPT,
        target="t1-a1",
        logs=tmp_path / "logs",
        host_env={},
    )


def test_every_adapter_formats_its_own_files_and_the_findings_add_up(
    tools: Tools, tmp_path: Path
) -> None:
    py = Stub("python", ".py", tmp_path, after=(draft("format.diff", "a.py"),))
    go = Stub("go", ".go", tmp_path, after=(draft("format.diff", "b.go"),))
    check = FormatCheck(tools, projects_of(py, go), tmp_path, changes_of("a.py", "b.go", "c.md"))

    result = asyncio.run(check.evaluate(data()))

    assert isinstance(result, Rejected)
    assert sorted(f.target for f in result.findings) == ["a.py", "b.go"]
    assert [r.files for r in py.requests] == [("a.py",)]
    assert [r.files for r in go.requests] == [("b.go",)]


def test_an_adapter_without_a_formatter_leaves_its_files_and_the_others_still_count(
    tools: Tools, tmp_path: Path
) -> None:
    py = Stub("python", ".py", tmp_path, after=(draft("format.diff", "a.py"),))
    go = Stub("go", ".go", tmp_path, offers=False)

    result = asyncio.run(
        FormatCheck(tools, projects_of(py, go), tmp_path, changes_of("a.py", "b.go")).evaluate(
            data()
        )
    )
    assert isinstance(result, Rejected)

    nobody = asyncio.run(
        FormatCheck(
            tools,
            projects_of(Stub("go", ".go", tmp_path, offers=False)),
            tmp_path,
            changes_of("b.go"),
        ).evaluate(data())
    )
    assert nobody == NotApplicable(missing_capability="format")


def test_a_lint_violation_is_new_only_against_the_same_adapters_old_text(
    tools: Tools, tmp_path: Path
) -> None:
    py = Stub(
        "python",
        ".py",
        tmp_path,
        after=(draft("lint.E1", "a.py", 3),),
        before=(draft("lint.E1", "a.py", 3),),
    )
    go = Stub(
        "go",
        ".go",
        tmp_path,
        after=(draft("lint.errcheck", "b.go", 3),),
        before=(draft("lint.errcheck", "b.go", 1),),
    )
    check = LintCheck(
        tools,
        projects_of(py, go),
        tmp_path,
        changes_of("a.py", "b.go"),
        {"a.py": "x\n", "b.go": "y\n"},
    )

    result = asyncio.run(check.evaluate(data()))

    assert isinstance(result, Rejected)
    assert [(f.target, f.measured, f.limit) for f in result.findings] == [("b.go", 3, 1)]
    assert [sorted(r.files) for r in go.requests if r.root != tmp_path] == [["b.go"]]


def test_the_old_text_is_linted_with_the_config_its_adapter_names(
    tools: Tools, tmp_path: Path
) -> None:
    (tmp_path / "linter.toml").write_text("x\n", encoding="utf-8")
    py = Stub("python", ".py", tmp_path, config=("linter.toml",))
    check = LintCheck(tools, projects_of(py), tmp_path, changes_of("a.py"), {"a.py": "x\n"})

    assert asyncio.run(check.evaluate(data())) == Approved()
    assert py.config_seen == [True]


def test_a_linter_that_cannot_run_on_the_old_text_is_inconclusive_not_a_pass(
    tools: Tools, tmp_path: Path
) -> None:
    java = Stub(
        "java", ".java", tmp_path, after=(draft("lint.pmd", "A.java"),), offers_before=False
    )
    check = LintCheck(tools, projects_of(java), tmp_path, changes_of("A.java"), {"A.java": "x\n"})

    result = asyncio.run(check.evaluate(data()))

    assert isinstance(result, Inconclusive)
    assert result.detail == "lint: the java linter cannot run on the old files"


def test_a_linter_nobody_offers_makes_the_lint_not_applicable(tools: Tools, tmp_path: Path) -> None:
    go = Stub("go", ".go", tmp_path, offers=False)
    check = LintCheck(tools, projects_of(go), tmp_path, changes_of("b.go"), {"b.go": "y\n"})

    assert asyncio.run(check.evaluate(data())) == NotApplicable(missing_capability="lint")


def test_an_added_file_missing_from_the_copy_does_not_stop_the_old_side_lint(
    tools: Tools, tmp_path: Path
) -> None:
    py = Stub("python", ".py", tmp_path, after=(draft("lint.E1", "new.py"),))
    added = Changes(
        (Change(ChangeKind.ADDED, "new.py"), Change(ChangeKind.MODIFIED, "a.py")), 1, ()
    )
    check = LintCheck(tools, projects_of(py), tmp_path, added, {"a.py": "x\n"})

    result = asyncio.run(check.evaluate(data()))

    assert isinstance(result, Rejected)


def test_a_type_error_is_new_only_against_the_same_adapters_old_text(
    tools: Tools, tmp_path: Path
) -> None:
    py = Stub(
        "python",
        ".py",
        tmp_path,
        after=(draft("types.arg-type", "a.py", 3), draft("types.return-value", "a.py", 1)),
        before=(draft("types.arg-type", "a.py", 1),),
    )
    check = TypesCheck(tools, projects_of(py), tmp_path, changes_of("a.py"), {"a.py": "x\n"})

    result = asyncio.run(check.evaluate(data()))

    assert isinstance(result, Rejected)
    assert [(f.message, f.target, f.measured, f.limit) for f in result.findings] == [
        ("2 new arg-type error(s)", "a.py", 3, 1),
        ("1 new return-value error(s)", "a.py", 1, 0),
    ]


def test_a_type_checker_that_cannot_run_on_the_old_text_is_inconclusive(
    tools: Tools, tmp_path: Path
) -> None:
    py = Stub("python", ".py", tmp_path, after=(draft("types.x", "a.py"),), offers_before=False)
    check = TypesCheck(tools, projects_of(py), tmp_path, changes_of("a.py"), {"a.py": "x\n"})

    result = asyncio.run(check.evaluate(data()))

    assert isinstance(result, Inconclusive)
    assert result.detail == "types: the python type checker cannot run on the old files"


def test_a_language_without_a_type_checker_makes_the_types_check_not_applicable(
    tools: Tools, tmp_path: Path
) -> None:
    go = Stub("go", ".go", tmp_path, offers=False)
    check = TypesCheck(tools, projects_of(go), tmp_path, changes_of("b.go"), {"b.go": "y\n"})

    assert asyncio.run(check.evaluate(data())) == NotApplicable(missing_capability="types")


def test_the_private_copy_leaves_out_git_and_each_link_on_its_own(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / ".git").mkdir()
    try:
        (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are not allowed here")

    assert skip_git_and_links(str(tmp_path), [".git"]) == {".git"}
    assert skip_git_and_links(str(tmp_path), ["link"]) == {"link"}
    assert skip_git_and_links(str(tmp_path), ["real"]) == set()


class Refusing(Stub):
    """An adapter that says it cannot measure the types of a project the way the project asks."""

    def unmeasurable(self, operation: Operation, root: Path) -> str | None:
        return f"plugins in {root.name}" if operation is Operation.TYPES else None


def test_types_an_adapter_cannot_measure_as_the_project_asks_are_inconclusive(
    tools: Tools, tmp_path: Path
) -> None:
    py = Refusing("python", ".py", tmp_path, after=(draft("types.x", "a.py"),))
    check = TypesCheck(tools, projects_of(py), tmp_path, changes_of("a.py"), {"a.py": "x\n"})

    result = asyncio.run(check.evaluate(data()))

    assert isinstance(result, Inconclusive)
    assert result.reason is InconclusiveReason.UNSUPPORTED_CONSTRUCT
    assert result.detail == f"types: plugins in {tmp_path.name}"
    assert py.requests == []  # nothing was run


def test_the_refusal_is_asked_about_types_only_and_about_the_projects_with_changed_files(
    tools: Tools, tmp_path: Path
) -> None:
    py = Refusing("python", ".py", tmp_path)
    go = Stub("go", ".go", tmp_path, after=(draft("types.x", "b.go"),))
    projects = projects_of(py, go)

    lint = LintCheck(tools, projects, tmp_path, changes_of("a.py"), {"a.py": "x\n"})
    assert asyncio.run(lint.evaluate(data())) == Approved()

    types = TypesCheck(tools, projects, tmp_path, changes_of("b.go"), {"b.go": "y\n"})
    assert isinstance(asyncio.run(types.evaluate(data())), Rejected)
