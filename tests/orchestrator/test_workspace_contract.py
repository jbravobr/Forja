"""The same scenarios against GitWorkspace and MemoryWorkspace: the double may not drift.

Whatever one does the other must do, because the fast tests of the orchestrator trust the double.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from forja.core.events import OpenOperation
from forja.core.idempotency import Decision, ReconciliationError, task_branch_name
from forja.core.states import Level
from forja.orchestrator.operations import Step
from forja.orchestrator.workspace_git import (
    NOTES_REF,
    CommitMessage,
    IntegrationConflict,
    Layout,
    NothingToCommit,
    NothingToRollBack,
    StageFacts,
    workcopy_path,
)
from forja.testing import MemoryRepo, MemoryWorkspace
from tests.core.idempotency.conftest import Repo
from tests.orchestrator.harness import RUN, make_workspace, minimal_env

MESSAGE = CommitMessage(
    "refactor: make it simpler", "Split the long function.", "fake-cli 1.0", "0.0.0+abc"
)


@dataclass
class Backend:
    """What a scenario needs from either side, expressed once."""

    name: str
    workspace: Any
    base: str
    read_tree: Callable[[str], dict[str, str]]
    subjects: Callable[[str], list[str]]
    tip_trailers: Callable[[str], dict[str, list[str]]]
    tip_parents: Callable[[str], int]
    branches: Callable[[], set[str]]
    make_branch: Callable[[str], None]
    tip: Callable[[str], str]
    notes: Callable[[], dict[str, str]]  # the commit -> its note


def memory_backend(root: Path) -> Backend:
    repo = MemoryRepo({"base.txt": "base"})
    workspace = MemoryWorkspace(repo, run_id=RUN, layout=Layout(root / "wt"))

    def trailers(branch: str) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for key, value in repo.tip(branch).trailers:
            found.setdefault(key, []).append(value)
        return found

    return Backend(
        "memory",
        workspace,
        repo.base,
        lambda branch: {k: v.strip() for k, v in repo.tip(branch).tree.items()},
        lambda branch: [c.subject for c in repo.log(branch)],
        trailers,
        lambda branch: len(repo.tip(branch).parents),
        lambda: set(repo.branches),
        lambda name: repo.create_branch(name),
        lambda branch: repo.branches[branch],
        lambda: dict(repo.notes),
    )


def git_backend(root: Path, template: Path) -> Backend:
    import shutil  # noqa: PLC0415

    (root / "home").mkdir(parents=True)
    shutil.copytree(template, root / "repo")
    repo = Repo(root / "repo", minimal_env(root / "home"), root / "logs")
    workspace = make_workspace(repo, root)

    def read_tree(branch: str) -> dict[str, str]:
        names = repo.sh("ls-tree", "-r", "--name-only", branch).splitlines()
        return {name: repo.sh("show", f"{branch}:{name}") for name in names}

    def trailers(branch: str) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for line in repo.sh("log", "-1", "--format=%(trailers:only,unfold)", branch).splitlines():
            if line.strip():
                key, _, value = line.partition(": ")
                found.setdefault(key, []).append(value)
        return found

    def notes() -> dict[str, str]:
        if not repo.sh("for-each-ref", NOTES_REF):
            return {}
        noted = [line.split()[1] for line in repo.sh("notes", "list").splitlines()]
        return {commit: repo.sh("notes", "show", commit) for commit in noted}

    return Backend(
        "git",
        workspace,
        repo.sh("rev-parse", "HEAD"),
        read_tree,
        lambda branch: repo.sh("log", "--first-parent", "--format=%s", branch).splitlines(),
        trailers,
        lambda branch: len(repo.sh("log", "-1", "--format=%P", branch).split()),
        lambda: set(
            repo.sh("for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()
        ),
        lambda name: _ignore(repo.sh("branch", name)),
        lambda branch: repo.sh("rev-parse", branch),
        notes,
    )


@pytest.fixture(params=["memory", pytest.param("git", marks=pytest.mark.integration)])
def backend(request: pytest.FixtureRequest, tmp_path: Path, template: Path) -> Backend:
    if request.param == "memory":
        return memory_backend(tmp_path)
    return git_backend(tmp_path, template)


def go(step: Step) -> None:
    async def both() -> None:
        await step.payload()
        await step.run()

    asyncio.run(both())


def _ignore(value: object) -> None:
    return None


def verdict(workspace: Any, name: str, payload: dict[str, Any]) -> Decision:
    """The verdict on an operation whose intent was written, with this payload, and no effect."""
    operation = OpenOperation(name, f"{name}:x", Level.EXECUTION, "x", 1, payload=payload)
    decision: Decision = asyncio.run(workspace.reconcile(operation)).decision
    return decision


def intent(step: Step) -> dict[str, Any]:
    async def payload() -> dict[str, Any]:
        return dict(await step.payload())

    return asyncio.run(payload())


def start(b: Backend) -> None:
    ws = b.workspace
    go(ws.create_run_branch(b.base))
    go(ws.create_run_worktree())


def task(b: Backend, task_id: str, attempt: str, files: dict[str, str]) -> None:
    """A task branch, an attempt worktree with ``files`` written in it, and the stage committed."""
    ws = b.workspace
    go(ws.create_task_branch(task_id))
    go(ws.create_attempt_worktree(task_id, attempt))
    worktree = ws.attempt_worktree(attempt)
    for name, text in files.items():
        (worktree / name).parent.mkdir(parents=True, exist_ok=True)
        (worktree / name).write_text(text, encoding="utf-8", newline="\n")
    go(ws.commit_stage(task_id, f"{task_id}-s", attempt, MESSAGE))


# -- before the run --------------------------------------------------------------------------------


def test_the_run_starts_from_the_commit_the_repository_is_at(backend: Backend) -> None:
    assert asyncio.run(backend.workspace.check_ready()) == backend.base


def test_nothing_is_left_to_recover_in_a_clean_repository(backend: Backend) -> None:
    report = asyncio.run(backend.workspace.recover())

    assert (report.removed, report.kept) == ((), ())


# -- the run branch --------------------------------------------------------------------------------


def test_the_run_branch_and_its_worktree_start_at_the_base(backend: Backend) -> None:
    start(backend)

    assert backend.workspace.run_branch in backend.branches()
    assert backend.read_tree(backend.workspace.run_branch) == {"base.txt": "base"}
    assert (backend.workspace.run_worktree / "base.txt").read_text(
        encoding="utf-8"
    ).strip() == "base"


def test_normalization_commits_what_the_formatter_changed_and_nothing_if_it_changed_none(
    backend: Backend,
) -> None:
    start(backend)
    ws, branch = backend.workspace, backend.workspace.run_branch

    async def nothing(path: Path) -> None:
        return None

    async def format_it(path: Path) -> None:
        (path / "base.txt").write_text("formatted\n", encoding="utf-8", newline="\n")

    go(ws.normalize_style(nothing))
    assert backend.subjects(branch) == ["base"]
    go(ws.normalize_style(format_it))

    assert backend.subjects(branch) == ["style: normalize formatting", "base"]
    assert backend.tip_trailers(branch) == {"Stage-Id": [f"normalize-{RUN}"]}
    assert backend.read_tree(branch) == {"base.txt": "formatted"}


# -- a task ----------------------------------------------------------------------------------------


def test_a_stage_is_a_commit_on_the_task_branch_with_the_facts_as_trailers(
    backend: Backend,
) -> None:
    start(backend)

    task(backend, "t1", "t1-a1", {"src/a.py": "one"})

    branch = task_branch_name(RUN, "t1")
    assert backend.subjects(branch) == ["refactor: make it simpler", "base"]
    assert backend.tip_trailers(branch) == {
        "Task-Id": ["t1"],
        "Stage-Id": ["t1-s"],
        "Attempt-Id": ["t1-a1"],
        "Agent": ["fake-cli 1.0"],
        "Policy-Version": ["0.0.0+abc"],
    }
    assert backend.read_tree(branch) == {"base.txt": "base", "src/a.py": "one"}
    assert backend.read_tree(backend.workspace.run_branch) == {"base.txt": "base"}


FACTS = StageFacts(
    evidence=("scope", "complexity"),
    metrics=(("lint_violations", 2.0, 2.0), ("max_ccn", 14.0, 9.0), ("tests", 3.0, None)),
    assumptions=("the parser is only\ncalled from the CLI",),
)


def stage_with_facts(b: Backend) -> str:
    """The branch of a task whose stage was committed with ``FACTS``."""
    start(b)
    ws = b.workspace
    go(ws.create_task_branch("t1"))
    go(ws.create_attempt_worktree("t1", "t1-a1"))
    (ws.attempt_worktree("t1-a1") / "a.py").write_text("one", encoding="utf-8")
    message = CommitMessage("refactor: make it simpler", "", "fake-cli 1.0", "0.0.0+abc", FACTS)
    go(ws.commit_stage("t1", "t1-s", "t1-a1", message))
    return task_branch_name(RUN, "t1")


def test_what_forja_measured_of_the_stage_goes_after_the_other_trailers(backend: Backend) -> None:
    branch = stage_with_facts(backend)

    assert backend.tip_trailers(branch) == {
        "Task-Id": ["t1"],
        "Stage-Id": ["t1-s"],
        "Attempt-Id": ["t1-a1"],
        "Agent": ["fake-cli 1.0"],
        "Policy-Version": ["0.0.0+abc"],
        "Evidence-Id": ["scope", "complexity"],
        "Metric-max-ccn": ["14 -> 9"],
        "Assumption": ["the parser is only called from the CLI"],
    }


def test_every_number_of_the_stage_is_in_the_note_of_its_commit(backend: Backend) -> None:
    branch = stage_with_facts(backend)

    notes = backend.notes()
    assert list(notes) == [backend.tip(branch)]
    assert json.loads(notes[backend.tip(branch)]) == {
        "gates_passed": ["scope", "complexity"],
        "metrics": {
            "lint_violations": {"before": 2.0, "after": 2.0},
            "max_ccn": {"before": 14.0, "after": 9.0},
            "tests": {"before": 3.0, "after": None},
        },
    }


def test_a_stage_nothing_was_measured_of_has_no_note(backend: Backend) -> None:
    start(backend)

    task(backend, "t1", "t1-a1", {"src/a.py": "one"})

    assert backend.notes() == {}


def test_an_attempt_that_changed_nothing_has_nothing_to_commit(backend: Backend) -> None:
    start(backend)
    ws = backend.workspace
    go(ws.create_task_branch("t1"))
    go(ws.create_attempt_worktree("t1", "t1-a1"))

    with pytest.raises(NothingToCommit):
        go(ws.commit_stage("t1", "t1-s", "t1-a1", MESSAGE))


# -- integrating -----------------------------------------------------------------------------------


def test_integration_is_a_merge_commit_that_names_the_task(backend: Backend) -> None:
    start(backend)
    task(backend, "t1", "t1-a1", {"src/a.py": "one"})

    go(backend.workspace.integrate_task("t1", "merge:   make it  simpler"))

    branch = backend.workspace.run_branch
    assert backend.subjects(branch)[0] == "merge: make it simpler"
    assert backend.tip_trailers(branch) == {"Task-Id": ["t1"]}
    assert backend.tip_parents(branch) == 2
    assert backend.read_tree(branch) == {"base.txt": "base", "src/a.py": "one"}
    assert (backend.workspace.run_worktree / "src" / "a.py").read_text(encoding="utf-8") == "one"


def test_integrating_what_is_already_in_makes_no_second_merge(backend: Backend) -> None:
    start(backend)
    task(backend, "t1", "t1-a1", {"src/a.py": "one"})
    go(backend.workspace.integrate_task("t1", "merge"))
    before = backend.tip(backend.workspace.run_branch)

    go(backend.workspace.integrate_task("t1", "merge"))

    assert backend.tip(backend.workspace.run_branch) == before


def test_two_tasks_that_touch_different_files_both_merge(backend: Backend) -> None:
    start(backend)
    task(backend, "t1", "t1-a1", {"src/a.py": "one"})
    task(backend, "t2", "t2-a1", {"src/b.py": "two"})

    go(backend.workspace.integrate_task("t1", "merge one"))
    go(backend.workspace.integrate_task("t2", "merge two"))

    assert backend.read_tree(backend.workspace.run_branch) == {
        "base.txt": "base",
        "src/a.py": "one",
        "src/b.py": "two",
    }


def test_two_tasks_that_change_the_same_file_differently_conflict_and_leave_the_branch_alone(
    backend: Backend,
) -> None:
    start(backend)
    task(backend, "t1", "t1-a1", {"base.txt": "mine"})
    task(backend, "t2", "t2-a1", {"base.txt": "yours"})
    go(backend.workspace.integrate_task("t1", "merge one"))
    before = backend.tip(backend.workspace.run_branch)

    with pytest.raises(IntegrationConflict):
        go(backend.workspace.integrate_task("t2", "merge two"))

    assert backend.tip(backend.workspace.run_branch) == before
    assert backend.read_tree(backend.workspace.run_branch) == {"base.txt": "mine"}


def test_rolling_back_puts_the_branch_and_its_worktree_where_they_were(backend: Backend) -> None:
    start(backend)
    task(backend, "t1", "t1-a1", {"src/a.py": "one"})
    before = backend.tip(backend.workspace.run_branch)
    go(backend.workspace.integrate_task("t1", "merge"))

    go(backend.workspace.rollback_integration("t1"))

    assert backend.tip(backend.workspace.run_branch) == before
    assert not (backend.workspace.run_worktree / "src" / "a.py").exists()


def test_there_is_nothing_to_roll_back_unless_the_branch_ends_with_that_merge(
    backend: Backend,
) -> None:
    start(backend)
    task(backend, "t1", "t1-a1", {"src/a.py": "one"})

    with pytest.raises(NothingToRollBack):
        go(backend.workspace.rollback_integration("t1"))
    go(backend.workspace.integrate_task("t1", "merge"))
    with pytest.raises(NothingToRollBack):
        go(backend.workspace.rollback_integration("t2"))


# -- worktrees -------------------------------------------------------------------------------------


def test_removing_a_worktree_leaves_the_branch(backend: Backend) -> None:
    start(backend)
    task(backend, "t1", "t1-a1", {"src/a.py": "one"})
    worktree = backend.workspace.attempt_worktree("t1-a1")

    go(backend.workspace.remove_worktree("t1-a1"))

    assert not worktree.exists()
    assert task_branch_name(RUN, "t1") in backend.branches()


def test_removing_a_worktree_removes_the_copy_the_agent_worked_in(backend: Backend) -> None:
    start(backend)
    task(backend, "t1", "t1-a1", {"src/a.py": "one"})
    copy = workcopy_path(backend.workspace.attempt_worktree("t1-a1"))
    (copy / "src").mkdir(parents=True)
    (copy / "src" / "a.py").write_text("one", encoding="utf-8")
    other = workcopy_path(backend.workspace.attempt_worktree("t1-a2"))
    other.mkdir()

    go(backend.workspace.remove_worktree("t1-a1"))
    go(backend.workspace.remove_worktree("t1-a1"))  # with nothing left to remove it still ends

    assert copy.name == "t1-a1-copy" and not copy.exists()
    assert other.exists()  # the copy of another attempt is not this one's to remove


def test_an_attempt_worktree_made_again_starts_clean(backend: Backend) -> None:
    start(backend)
    ws = backend.workspace
    go(ws.create_task_branch("t1"))
    go(ws.create_attempt_worktree("t1", "t1-a1"))
    (ws.attempt_worktree("t1-a1") / "junk.txt").write_text("x", encoding="utf-8")

    go(ws.create_attempt_worktree("t1", "t1-a1"))

    assert not (ws.attempt_worktree("t1-a1") / "junk.txt").exists()


# -- watching git ----------------------------------------------------------------------------------


def test_a_branch_made_while_watching_is_a_difference_and_one_made_before_is_not(
    backend: Backend,
) -> None:
    backend.make_branch("early")

    async def watched() -> list[tuple[str, str]]:
        async with backend.workspace.watch() as watch:
            backend.make_branch("sneaky")
        return [(d.kind.value, d.subject) for d in watch.differences]

    assert asyncio.run(watched()) == [("ref_added", "refs/heads/sneaky")]


@pytest.mark.parametrize("who", [("t1", None), (None, "t1-a1")])
def test_a_watch_told_only_the_task_or_only_the_attempt_overlooks_nothing(
    backend: Backend, who: tuple[str | None, str | None]
) -> None:
    other = task_branch_name(RUN, "t2")  # with both named, the branch of another task is Forja's

    async def watched() -> list[tuple[str, str]]:
        async with backend.workspace.watch(*who, lambda: ["t1", "t2"]) as watch:
            backend.make_branch(other)
        return [(d.kind.value, d.subject) for d in watch.differences]

    assert asyncio.run(watched()) == [("ref_added", f"refs/heads/{other}")]


def test_nothing_changing_while_watching_is_no_difference(backend: Backend) -> None:
    async def watched() -> list[Any]:
        async with backend.workspace.watch() as watch:
            pass
        return list(watch.differences)

    assert asyncio.run(watched()) == []


# -- settling what a crash left open ---------------------------------------------------------------


def test_the_verdict_on_every_operation_before_and_after_it_happened(backend: Backend) -> None:
    ws = backend.workspace
    branch = task_branch_name(RUN, "t1")
    outcomes: dict[str, list[Decision]] = {}

    def record(name: str, step: Step, then: Callable[[], None]) -> None:
        payload = intent(step)
        before = verdict(ws, name, payload)
        then()
        outcomes[name] = [before, verdict(ws, name, payload)]

    record(
        "create_run_branch",
        ws.create_run_branch(backend.base),
        lambda: go(ws.create_run_branch(backend.base)),
    )
    go(ws.create_run_worktree())
    record(
        "create_task_branch", ws.create_task_branch("t1"), lambda: go(ws.create_task_branch("t1"))
    )
    record(
        "create_attempt_worktree",
        ws.create_attempt_worktree("t1", "t1-a1"),
        lambda: go(ws.create_attempt_worktree("t1", "t1-a1")),
    )
    worktree = ws.attempt_worktree("t1-a1")
    (worktree / "src").mkdir()
    (worktree / "src" / "a.py").write_text("one", encoding="utf-8")
    record(
        "commit_stage",
        ws.commit_stage("t1", "t1-s", "t1-a1", MESSAGE),
        lambda: go(ws.commit_stage("t1", "t1-s", "t1-a1", MESSAGE)),
    )
    record(
        "integrate_task",
        ws.integrate_task("t1", "merge"),
        lambda: go(ws.integrate_task("t1", "merge")),
    )
    record(
        "rollback_integration",
        ws.rollback_integration("t1"),
        lambda: go(ws.rollback_integration("t1")),
    )
    record("remove_worktree", ws.remove_worktree("t1-a1"), lambda: go(ws.remove_worktree("t1-a1")))

    assert branch in backend.branches()
    redo, done = Decision.REDO, Decision.ALREADY_DONE
    assert outcomes == {
        "create_run_branch": [redo, done],
        "create_task_branch": [redo, done],
        "create_attempt_worktree": [redo, done],
        "commit_stage": [redo, done],
        "integrate_task": [redo, done],
        "rollback_integration": [redo, done],
        "remove_worktree": [redo, done],
    }


def test_a_normalization_is_done_once_its_stage_is_in_the_history(backend: Backend) -> None:
    start(backend)

    async def format_it(path: Path) -> None:
        (path / "base.txt").write_text("formatted\n", encoding="utf-8", newline="\n")

    step = backend.workspace.normalize_style(format_it)
    payload = intent(step)
    before = verdict(backend.workspace, "normalize_style", payload)
    go(step)

    assert (before, verdict(backend.workspace, "normalize_style", payload)) == (
        Decision.REDO,
        Decision.ALREADY_DONE,
    )


def test_a_stage_on_a_branch_that_is_gone_cannot_be_judged(backend: Backend) -> None:
    payload = intent(backend.workspace.commit_stage("t9", "t9-s", "t9-a1", MESSAGE))

    with pytest.raises(ReconciliationError):
        verdict(backend.workspace, "commit_stage", payload)


def test_an_operation_that_is_not_git_is_not_reconciled_here(backend: Backend) -> None:
    operation = OpenOperation("teleport", "teleport:x", Level.EXECUTION, "x", 1)

    with pytest.raises(KeyError, match="teleport is not a git operation"):
        asyncio.run(backend.workspace.reconcile(operation))
