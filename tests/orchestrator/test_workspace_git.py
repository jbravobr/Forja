"""The git workspace against a real repository, through the restricted git facade."""

import asyncio
import os
import shutil
import stat
import subprocess
import time
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

import pytest

from forja.core.events import OpenOperation
from forja.core.idempotency import Decision, run_branch_name, task_branch_name
from forja.core.idempotency.git import GitResult
from forja.core.states import Level
from forja.orchestrator.operations import Step
from forja.orchestrator.ports import Workspace
from forja.orchestrator.workspace_git import (
    BODY_LINES,
    HANDLED_OPERATIONS,
    SUBJECT_LIMIT,
    CommitMessage,
    GitWorkspace,
    Identity,
    IntegrationConflict,
    Layout,
    NothingToCommit,
    NothingToRollBack,
    RepositoryNotReady,
    _make_writable_and_retry,
    detect_identity,
    git_environment,
)
from tests.core.idempotency.conftest import Repo
from tests.orchestrator.harness import IDENTITY, RUN

pytestmark = pytest.mark.integration

TASK = "t1abcdef0123"
ATTEMPT = "a1abcdef0123"
MESSAGE = CommitMessage.refactor(
    "split the parser",
    "It had too many branches.\nNow it has two functions.",
    agent="fake-cli 1.0",
    policy_version="0.0.0+abc",
)


def sync[T](awaitable: Awaitable[T]) -> T:
    async def wait() -> T:
        return await awaitable

    return asyncio.run(wait())


def do(step: Step) -> dict[str, Any]:
    """Run a step the way the journal does: the intent payload first, then the work."""
    payload: dict[str, Any] = sync(step.payload())
    sync(step.run())
    return payload


def op(name: str, payload: dict[str, Any]) -> OpenOperation:
    return OpenOperation(name, f"{name}:x", Level.EXECUTION, RUN, 1, payload=payload)


def settle(ws: GitWorkspace, name: str, payload: dict[str, Any]) -> Decision:
    return asyncio.run(ws.reconcile(op(name, payload))).decision


def start_run(ws: GitWorkspace, repo: Repo) -> str:
    base = repo.sh("rev-parse", "HEAD")
    do(ws.create_run_branch(base))
    do(ws.create_run_worktree())
    return base


def start_task(ws: GitWorkspace, repo: Repo) -> None:
    start_run(ws, repo)
    do(ws.create_task_branch(TASK))
    do(ws.create_attempt_worktree(TASK, ATTEMPT))


def edit_in_attempt(ws: GitWorkspace, name: str = "new.txt", text: str = "new\n") -> None:
    (ws.layout.attempt_worktree(ATTEMPT) / name).write_text(text)


def tip(repo: Repo, branch: str) -> str:
    return repo.sh("rev-parse", f"refs/heads/{branch}")


TASK_BRANCH = task_branch_name(RUN, TASK)
RUN_BRANCH = run_branch_name(RUN)


# -- before the run ----------------------------------------------------------------------------


def test_a_clean_repository_on_a_branch_is_ready_and_gives_the_commit_to_start_from(
    workspace: GitWorkspace, repo: Repo
) -> None:
    assert asyncio.run(workspace.check_ready()) == repo.sh("rev-parse", "HEAD")


def test_changes_that_are_not_committed_stop_the_run_before_it_starts(
    workspace: GitWorkspace, repo: Repo
) -> None:
    (repo.path / "base.txt").write_text("changed")
    (repo.path / "untracked.txt").write_text("new")

    with pytest.raises(RepositoryNotReady) as info:
        asyncio.run(workspace.check_ready())

    assert str(info.value) == (
        "2 path(s) have uncommitted changes; commit or stash them first, "
        "because the run works on committed code"
    )


def test_an_untracked_file_alone_is_enough_to_stop_the_run(
    workspace: GitWorkspace, repo: Repo
) -> None:
    (repo.path / "untracked.txt").write_text("new")

    with pytest.raises(RepositoryNotReady, match="1 path"):
        asyncio.run(workspace.check_ready())


def test_a_detached_head_stops_the_run(workspace: GitWorkspace, repo: Repo) -> None:
    repo.sh("checkout", "--detach")

    with pytest.raises(RepositoryNotReady) as info:
        asyncio.run(workspace.check_ready())

    assert str(info.value) == "HEAD is detached; check out a branch and try again"


# -- identity and settings ---------------------------------------------------------------------


def test_the_agent_is_the_author_and_the_owner_of_the_repository_is_the_committer(
    workspace: GitWorkspace, repo: Repo
) -> None:
    repo.sh("config", "user.name", "Real Dev")
    repo.sh("config", "user.email", "real@example.com")

    identity = asyncio.run(detect_identity(workspace._git, "claude-code"))  # noqa: SLF001

    assert identity == Identity(
        "Forja (claude-code)", "forja@localhost", "Real Dev", "real@example.com"
    )


def test_without_an_identity_in_git_the_committer_is_forja(
    workspace: GitWorkspace, repo: Repo
) -> None:
    repo.sh("config", "user.name", "")
    identity = asyncio.run(detect_identity(workspace._git, "cli"))  # noqa: SLF001

    assert (identity.committer_name, identity.committer_email) == ("Forja", "forja@localhost")
    assert identity.author_name == "Forja (cli)"


def test_the_environment_carries_the_identity_and_long_path_support(tmp_path: Path) -> None:
    env = git_environment({"PATH": "x"}, IDENTITY)

    assert env == {
        "PATH": "x",
        "GIT_AUTHOR_NAME": "Forja (fake-cli)",
        "GIT_AUTHOR_EMAIL": "forja@localhost",
        "GIT_COMMITTER_NAME": "Dev Person",
        "GIT_COMMITTER_EMAIL": "dev@example.com",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.longpaths",
        "GIT_CONFIG_VALUE_0": "true",
    }


def test_worktrees_live_under_a_short_directory_named_after_the_run(tmp_path: Path) -> None:
    layout = Layout.for_run(tmp_path, "0123456789abcdef")

    assert layout.root == tmp_path / "wt" / "01234567"
    assert layout.run_worktree == tmp_path / "wt" / "01234567" / "run"
    assert layout.attempt_worktree("a1") == tmp_path / "wt" / "01234567" / "a1"


def test_a_commit_message_has_a_short_subject_a_trimmed_body_and_the_facts() -> None:
    long_intent = "reduce the complexity of  the\n  parser " + "x" * 100

    message = CommitMessage.refactor(
        long_intent,
        "  why\n" + "\n".join(f"line {i}" for i in range(30)),
        agent="cli 1",
        policy_version="p",
    )

    assert message.subject.startswith("refactor: reduce the complexity of the parser xxx")
    assert len(message.subject) == SUBJECT_LIMIT
    assert len(message.body.splitlines()) == BODY_LINES
    assert message.body.startswith("why\nline 0")
    assert (message.agent, message.policy_version) == ("cli 1", "p")
    assert CommitMessage.refactor("a", "", agent="c", policy_version="p").body == ""
    assert (
        CommitMessage.refactor("a   ", "x", agent="c", policy_version="p").subject == "refactor: a"
    )


# -- the run branch and its worktree -----------------------------------------------------------


def test_the_run_branch_starts_at_the_base_commit(workspace: GitWorkspace, repo: Repo) -> None:
    base = repo.sh("rev-parse", "HEAD")
    repo.commit("later.txt")  # main moves on; the run does not follow

    payload = do(workspace.create_run_branch(base))

    assert payload == {"branch": RUN_BRANCH, "base": base}
    assert tip(repo, RUN_BRANCH) == base
    assert settle(workspace, "create_run_branch", payload) == Decision.ALREADY_DONE


def test_a_run_branch_that_was_never_made_is_redone(workspace: GitWorkspace) -> None:
    assert settle(workspace, "create_run_branch", {"branch": RUN_BRANCH}) == Decision.REDO


def test_the_run_worktree_has_the_run_branch_checked_out_and_not_the_users_tree(
    workspace: GitWorkspace, repo: Repo
) -> None:
    base = start_run(workspace, repo)

    run_tree = workspace.layout.run_worktree
    assert repo.sh("branch", "--show-current", cwd=run_tree) == RUN_BRANCH
    assert repo.sh("rev-parse", "HEAD", cwd=run_tree) == base
    assert repo.sh("branch", "--show-current") == "main"


def test_the_payload_of_a_worktree_says_where_it_is_and_which_commit_to_expect(
    workspace: GitWorkspace, repo: Repo
) -> None:
    base = repo.sh("rev-parse", "HEAD")
    do(workspace.create_run_branch(base))

    payload = sync(workspace.create_run_worktree().payload())

    assert payload == {"worktree": str(workspace.layout.run_worktree), "expected_head": base}


def test_a_worktree_step_can_be_done_again_after_a_crash_left_things_behind(
    workspace: GitWorkspace, repo: Repo
) -> None:
    start_run(workspace, repo)
    stale = workspace.layout.run_worktree / "leftover.txt"
    stale.write_text("half-done")

    do(workspace.create_run_worktree())  # the worktree is registered and has a stray file

    assert not stale.exists()
    assert repo.sh("branch", "--show-current", cwd=workspace.layout.run_worktree) == RUN_BRANCH
    shutil.rmtree(workspace.layout.run_worktree)  # the directory gone, git still has the record
    do(workspace.create_run_worktree())
    assert (workspace.layout.run_worktree / "base.txt").exists()


def test_a_worktree_is_reconciled_by_its_place_and_its_commit(
    workspace: GitWorkspace, repo: Repo
) -> None:
    base = start_run(workspace, repo)
    payload = {"worktree": str(workspace.layout.run_worktree), "expected_head": base}

    assert settle(workspace, "create_attempt_worktree", payload) == Decision.ALREADY_DONE
    assert (
        settle(workspace, "create_attempt_worktree", {**payload, "expected_head": "0" * 40})
        == Decision.REDO
    )


# -- the style commit --------------------------------------------------------------------------


async def format_base(worktree: Path) -> None:
    (worktree / "base.txt").write_text("formatted\n")


async def format_nothing(worktree: Path) -> None:
    return None


def test_what_the_formatter_changes_is_committed_with_the_runs_trailer(
    workspace: GitWorkspace, repo: Repo
) -> None:
    start_run(workspace, repo)

    payload = do(workspace.normalize_style(format_base))

    run_tree = workspace.layout.run_worktree
    assert payload == {"run_branch": RUN_BRANCH, "stage_id": f"normalize-{RUN}"}
    assert repo.sh("log", "-1", "--format=%s", cwd=run_tree) == "style: normalize formatting"
    assert repo.sh("log", "-1", "--format=%(trailers:key=Stage-Id,valueonly)", cwd=run_tree) == (
        f"normalize-{RUN}"
    )
    assert (run_tree / "base.txt").read_text() == "formatted\n"
    assert settle(workspace, "normalize_style", payload) == Decision.ALREADY_DONE


def test_a_formatter_with_nothing_to_do_makes_no_commit(
    workspace: GitWorkspace, repo: Repo
) -> None:
    base = start_run(workspace, repo)

    payload = do(workspace.normalize_style(format_nothing))

    assert tip(repo, RUN_BRANCH) == base
    assert settle(workspace, "normalize_style", payload) == Decision.REDO


def test_a_formatter_that_only_changes_line_endings_makes_no_commit_and_does_not_fail(
    workspace: GitWorkspace, repo: Repo
) -> None:
    repo.sh("config", "core.autocrlf", "true")
    (repo.path / "lines.txt").write_bytes(b"a\r\nb\r\n")
    repo.sh("add", "lines.txt")
    repo.sh("commit", "-m", "lines")
    base = start_run(workspace, repo)
    lines = workspace.layout.run_worktree / "lines.txt"
    assert lines.read_bytes() == b"a\r\nb\r\n"  # the checkout is in CRLF

    async def format_to_lf(worktree: Path) -> None:
        lines.write_bytes(b"a\nb\n")  # what gofmt does: git sees no change after normalizing

    do(workspace.normalize_style(format_to_lf))

    assert tip(repo, RUN_BRANCH) == base


def test_a_crash_between_the_formatter_and_the_commit_does_not_lose_the_formatting(
    workspace: GitWorkspace, repo: Repo
) -> None:
    base = start_run(workspace, repo)
    asyncio.run(format_base(workspace.layout.run_worktree))  # the formatter ran, then the crash

    do(workspace.normalize_style(format_nothing))  # redone: nothing new to format

    assert tip(repo, RUN_BRANCH) != base
    assert repo.sh("status", "--porcelain", cwd=workspace.layout.run_worktree) == ""


# -- tasks and attempts ------------------------------------------------------------------------


def test_a_task_branch_starts_at_the_tip_of_the_run_branch(
    workspace: GitWorkspace, repo: Repo
) -> None:
    base = start_run(workspace, repo)

    payload = do(workspace.create_task_branch(TASK))

    assert payload == {"branch": TASK_BRANCH}
    assert tip(repo, TASK_BRANCH) == base
    assert settle(workspace, "create_task_branch", payload) == Decision.ALREADY_DONE


def test_an_attempt_works_on_the_task_branch_itself(workspace: GitWorkspace, repo: Repo) -> None:
    start_task(workspace, repo)

    attempt_tree = workspace.layout.attempt_worktree(ATTEMPT)
    assert repo.sh("branch", "--show-current", cwd=attempt_tree) == TASK_BRANCH
    assert (attempt_tree / "base.txt").exists()


def test_an_approved_attempt_becomes_one_commit_on_the_task_branch(
    workspace: GitWorkspace, repo: Repo
) -> None:
    start_task(workspace, repo)
    run_tip = tip(repo, RUN_BRANCH)
    edit_in_attempt(workspace)

    payload = do(workspace.commit_stage(TASK, "s1", ATTEMPT, MESSAGE))

    assert payload == {"task_branch": TASK_BRANCH, "stage_id": "s1"}
    assert tip(repo, RUN_BRANCH) == run_tip  # the run branch is untouched until the integration
    assert repo.sh("log", "-1", "--format=%s", TASK_BRANCH) == ("refactor: split the parser")
    body = repo.sh("log", "-1", "--format=%b", TASK_BRANCH)
    assert body.startswith("It had too many branches.\nNow it has two functions.")
    trailers = repo.sh("log", "-1", "--format=%(trailers:only,unfold)", TASK_BRANCH).splitlines()
    assert [t for t in trailers if t] == [
        f"Task-Id: {TASK}",
        "Stage-Id: s1",
        f"Attempt-Id: {ATTEMPT}",
        "Agent: fake-cli 1.0",
        "Policy-Version: 0.0.0+abc",
    ]
    assert (
        repo.sh("log", "-1", "--format=%an <%ae>", TASK_BRANCH)
        == "Forja (fake-cli) <forja@localhost>"
    )
    assert repo.sh("log", "-1", "--format=%cn <%ce>", TASK_BRANCH) == "Dev Person <dev@example.com>"
    assert settle(workspace, "commit_stage", payload) == Decision.ALREADY_DONE


def test_a_message_without_a_reason_has_no_body(workspace: GitWorkspace, repo: Repo) -> None:
    start_task(workspace, repo)
    edit_in_attempt(workspace)
    message = CommitMessage.refactor("tidy", "", agent="cli", policy_version="p")

    do(workspace.commit_stage(TASK, "s1", ATTEMPT, message))

    assert repo.sh("log", "-1", "--format=%b", TASK_BRANCH).startswith("Task-Id:")


def test_new_files_deleted_files_and_changes_are_all_committed(
    workspace: GitWorkspace, repo: Repo
) -> None:
    start_task(workspace, repo)
    tree = workspace.layout.attempt_worktree(ATTEMPT)
    (tree / "base.txt").unlink()
    (tree / "sub" / "dir").mkdir(parents=True)
    edit_in_attempt(workspace, "sub/dir/new.txt")

    do(workspace.commit_stage(TASK, "s1", ATTEMPT, MESSAGE))

    changed = repo.sh("diff", "--name-status", f"{RUN_BRANCH}..{TASK_BRANCH}").splitlines()
    assert sorted(changed) == ["A\tsub/dir/new.txt", "D\tbase.txt"]


def test_an_attempt_that_changed_nothing_cannot_be_committed(
    workspace: GitWorkspace, repo: Repo
) -> None:
    start_task(workspace, repo)

    with pytest.raises(NothingToCommit) as info:
        do(workspace.commit_stage(TASK, "s1", ATTEMPT, MESSAGE))

    assert str(info.value) == f"attempt {ATTEMPT} changed nothing"


def test_a_commit_that_was_never_made_is_redone(workspace: GitWorkspace, repo: Repo) -> None:
    start_task(workspace, repo)

    payload = {"task_branch": TASK_BRANCH, "stage_id": "s1"}

    assert settle(workspace, "commit_stage", payload) == Decision.REDO


# -- integration and its rollback --------------------------------------------------------------


def commit_task(workspace: GitWorkspace, name: str = "new.txt") -> None:
    edit_in_attempt(workspace, name)
    do(workspace.commit_stage(TASK, "s1", ATTEMPT, MESSAGE))
    do(workspace.remove_worktree(ATTEMPT))


def test_a_task_is_merged_with_a_commit_that_names_it(workspace: GitWorkspace, repo: Repo) -> None:
    start_task(workspace, repo)
    commit_task(workspace)
    before = tip(repo, RUN_BRANCH)

    payload = do(workspace.integrate_task(TASK, "integrate: " + "x" * 100))

    assert payload == {"task_branch": TASK_BRANCH, "run_branch": RUN_BRANCH}
    run_tree = workspace.layout.run_worktree
    parents = repo.sh("log", "-1", "--format=%P", RUN_BRANCH).split()
    assert len(parents) == 2 and parents[0] == before
    subject = repo.sh("log", "-1", "--format=%s", RUN_BRANCH)
    assert subject.startswith("integrate: xxx") and len(subject) == SUBJECT_LIMIT
    assert repo.sh("log", "-1", "--format=%(trailers:key=Task-Id,valueonly)", RUN_BRANCH) == TASK
    assert (run_tree / "new.txt").exists()
    assert settle(workspace, "integrate_task", payload) == Decision.ALREADY_DONE


def test_a_task_with_nothing_new_integrates_without_error(
    workspace: GitWorkspace, repo: Repo
) -> None:
    start_run(workspace, repo)
    do(workspace.create_task_branch(TASK))
    before = tip(repo, RUN_BRANCH)

    do(workspace.integrate_task(TASK, "integrate"))

    assert tip(repo, RUN_BRANCH) == before


def test_a_conflicting_merge_is_abandoned_and_leaves_the_run_branch_as_it_was(
    workspace: GitWorkspace, repo: Repo
) -> None:
    start_task(workspace, repo)
    run_tree = workspace.layout.run_worktree
    edit_in_attempt(workspace, "base.txt", "from the task\n")
    do(workspace.commit_stage(TASK, "s1", ATTEMPT, MESSAGE))
    (run_tree / "base.txt").write_text("from the run\n")
    repo.sh("add", "base.txt", cwd=run_tree)
    repo.sh("commit", "-m", "run moved on", cwd=run_tree)
    before = tip(repo, RUN_BRANCH)

    with pytest.raises(IntegrationConflict) as info:
        do(workspace.integrate_task(TASK, "integrate"))

    assert str(info.value) == f"{TASK_BRANCH} does not merge into {RUN_BRANCH}"
    assert tip(repo, RUN_BRANCH) == before
    assert repo.sh("status", "--porcelain", cwd=run_tree) == ""


def test_a_merge_cut_short_by_a_crash_does_not_block_the_next_one(
    workspace: GitWorkspace, repo: Repo
) -> None:
    start_task(workspace, repo)
    run_tree = workspace.layout.run_worktree
    edit_in_attempt(workspace, "base.txt", "from the task\n")
    do(workspace.commit_stage(TASK, "s1", ATTEMPT, MESSAGE))
    (run_tree / "base.txt").write_text("from the run\n")
    repo.sh("add", "base.txt", cwd=run_tree)
    repo.sh("commit", "-m", "run moved on", cwd=run_tree)
    who = {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    subprocess.run(
        ["git", "merge", "--no-ff", TASK_BRANCH],
        cwd=run_tree,
        env={**repo.env, **who},
        capture_output=True,
    )
    assert repo.sh("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=run_tree)  # it is in progress
    second, second_attempt = "t2abcdef0123", "a2abcdef0123"
    do(workspace.create_task_branch(second))
    do(workspace.create_attempt_worktree(second, second_attempt))
    (workspace.layout.attempt_worktree(second_attempt) / "second.txt").write_text("2\n")
    do(workspace.commit_stage(second, "s2", second_attempt, MESSAGE))

    do(workspace.integrate_task(second, "integrate the second"))

    assert repo.sh("status", "--porcelain", cwd=run_tree) == ""
    assert (run_tree / "second.txt").exists()
    assert len(repo.sh("log", "-1", "--format=%P", RUN_BRANCH).split()) == 2


def test_the_integration_can_be_undone_and_the_run_branch_goes_back(
    workspace: GitWorkspace, repo: Repo
) -> None:
    start_task(workspace, repo)
    commit_task(workspace)
    before = tip(repo, RUN_BRANCH)
    do(workspace.integrate_task(TASK, "integrate"))

    payload = do(workspace.rollback_integration(TASK))

    assert payload == {"run_branch": RUN_BRANCH, "expected_tip": before}
    assert tip(repo, RUN_BRANCH) == before
    assert not (workspace.layout.run_worktree / "new.txt").exists()
    assert settle(workspace, "rollback_integration", payload) == Decision.ALREADY_DONE
    assert tip(repo, TASK_BRANCH) != before  # the task branch keeps its commit


def test_an_integration_that_is_still_in_place_is_redone(
    workspace: GitWorkspace, repo: Repo
) -> None:
    start_task(workspace, repo)
    commit_task(workspace)
    before = tip(repo, RUN_BRANCH)
    do(workspace.integrate_task(TASK, "integrate"))

    payload = {"run_branch": RUN_BRANCH, "expected_tip": before}

    assert settle(workspace, "rollback_integration", payload) == Decision.REDO


def test_only_the_merge_of_the_named_task_can_be_undone(
    workspace: GitWorkspace, repo: Repo
) -> None:
    start_task(workspace, repo)
    commit_task(workspace)
    do(workspace.integrate_task(TASK, "integrate"))

    with pytest.raises(NothingToRollBack) as other:
        sync(workspace.rollback_integration("other-task").payload())
    assert str(other.value) == f"{RUN_BRANCH} does not end with the merge of other-task"

    run_tree = workspace.layout.run_worktree
    (run_tree / "later.txt").write_text("x")
    repo.sh("add", "later.txt", cwd=run_tree)
    repo.sh("commit", "-m", "later", cwd=run_tree)
    with pytest.raises(NothingToRollBack):
        sync(workspace.rollback_integration(TASK).payload())


# -- removing worktrees ------------------------------------------------------------------------


def test_an_attempt_worktree_and_git_s_record_of_it_disappear(
    workspace: GitWorkspace, repo: Repo
) -> None:
    start_task(workspace, repo)
    tree = workspace.layout.attempt_worktree(ATTEMPT)
    (tree / "read_only.txt").write_text("x")
    os.chmod(tree / "read_only.txt", 0o444)

    payload = do(workspace.remove_worktree(ATTEMPT))

    assert payload == {"worktree": str(tree)}
    assert not tree.exists()
    assert str(tree.name) not in repo.sh("worktree", "list")
    assert settle(workspace, "remove_worktree", payload) == Decision.ALREADY_DONE


def test_the_run_worktree_is_removed_by_the_name_run(workspace: GitWorkspace, repo: Repo) -> None:
    start_run(workspace, repo)

    payload = do(workspace.remove_worktree("run"))

    assert payload == {"worktree": str(workspace.layout.run_worktree)}
    assert not workspace.layout.run_worktree.exists()


def test_removing_a_worktree_that_is_already_gone_is_fine(workspace: GitWorkspace) -> None:
    payload = do(workspace.remove_worktree(ATTEMPT))

    assert payload == {"worktree": str(workspace.layout.attempt_worktree(ATTEMPT))}
    assert not workspace.layout.attempt_worktree(ATTEMPT).exists()


def test_a_worktree_still_there_is_redone(workspace: GitWorkspace, repo: Repo) -> None:
    start_task(workspace, repo)
    path = str(workspace.layout.attempt_worktree(ATTEMPT))

    assert settle(workspace, "remove_worktree", {"worktree": path}) == Decision.REDO


# -- settling, recovering, watching ------------------------------------------------------------


def test_only_git_operations_are_settled_here(workspace: GitWorkspace) -> None:
    assert {
        "create_run_branch",
        "create_attempt_worktree",
        "normalize_style",
        "create_task_branch",
        "commit_stage",
        "integrate_task",
        "rollback_integration",
        "remove_worktree",
    } == HANDLED_OPERATIONS
    with pytest.raises(KeyError, match="call_agent_cli is not a git operation"):
        asyncio.run(workspace.reconcile(op("call_agent_cli", {})))


def test_recovering_removes_a_stale_lock_and_forgets_a_worktree_that_is_gone(
    workspace: GitWorkspace, repo: Repo
) -> None:
    start_task(workspace, repo)
    shutil.rmtree(workspace.layout.attempt_worktree(ATTEMPT))  # directory gone, record stays
    lock = repo.path / ".git" / "index.lock"
    lock.write_text("")
    old = time.time() - 3600
    os.utime(lock, (old, old))

    report = asyncio.run(workspace.recover())

    assert not lock.exists() and report.removed
    assert ATTEMPT not in repo.sh("worktree", "list")


def test_the_watch_sees_a_ref_that_moved_while_the_agent_was_running(
    workspace: GitWorkspace, repo: Repo
) -> None:
    async def scenario() -> list[Any]:
        async with workspace.watch() as watch:
            repo.sh("branch", "sneaky")
        return watch.differences

    assert asyncio.run(scenario())


def test_the_watch_sees_nothing_when_nothing_moved(workspace: GitWorkspace) -> None:
    async def scenario() -> list[Any]:
        async with workspace.watch() as watch:
            pass
        return watch.differences

    assert asyncio.run(scenario()) == []


def test_the_workspace_satisfies_the_port(workspace: GitWorkspace) -> None:
    port: Workspace = workspace

    assert port.run_branch == RUN_BRANCH


# -- more edges ----------------------------------------------------------------------------------


class _Says:
    """A git that answers ``config --get`` with whatever the test needs."""

    def __init__(self, exit_code: int, lines: list[str]) -> None:
        self._result = GitResult(exit_code, lines)

    async def run(self, *args: str) -> GitResult:
        return self._result


def identity_from(exit_code: int, lines: list[str]) -> Identity:
    git: Any = _Says(exit_code, lines)
    return sync(detect_identity(git, "cli"))


def test_a_configured_identity_is_used_only_when_git_answered_and_said_something() -> None:
    assert identity_from(0, [" Real Dev "]).committer_name == "Real Dev"
    assert identity_from(0, []).committer_name == "Forja"
    assert identity_from(0, ["  "]).committer_name == "Forja"
    assert identity_from(1, ["Real Dev"]).committer_name == "Forja"


def test_the_identity_is_asked_for_under_the_right_names() -> None:
    asked: list[tuple[str, ...]] = []

    class Recording:
        async def run(self, *args: str) -> GitResult:
            asked.append(args)
            return GitResult(1, [])

    git: Any = Recording()
    sync(detect_identity(git, "cli"))

    assert asked == [("config", "--get", "user.name"), ("config", "--get", "user.email")]


def test_the_subject_is_cut_at_the_limit_without_leaving_a_space_at_the_end() -> None:
    intent = "a" * 61 + " " + "b" * 20  # the 72nd character is the space

    message = CommitMessage.refactor(intent, "", agent="c", policy_version="p")

    assert message.subject == "refactor: " + "a" * 61


def test_the_body_loses_the_spaces_at_the_end_of_lines_but_keeps_the_indentation() -> None:
    message = CommitMessage.refactor("x", "first   \n  second  ", agent="c", policy_version="p")

    assert message.body == "first\n  second"


def test_every_worktree_name_is_a_directory_under_the_run_root(tmp_path: Path) -> None:
    layout = Layout(tmp_path)

    assert layout.worktree("anything") == tmp_path / "anything"
    assert layout.run_worktree == layout.worktree("run")
    assert layout.attempt_worktree("a1") == layout.worktree("a1")


def test_a_task_branch_starts_where_the_run_branch_is_now_not_where_the_repository_is(
    workspace: GitWorkspace, repo: Repo
) -> None:
    start_run(workspace, repo)
    do(workspace.normalize_style(format_base))  # the run branch moves one commit ahead

    do(workspace.create_task_branch(TASK))

    assert tip(repo, TASK_BRANCH) == tip(repo, RUN_BRANCH)
    assert tip(repo, TASK_BRANCH) != repo.sh("rev-parse", "HEAD")


def test_a_read_only_file_is_made_writable_before_the_removal_is_retried(tmp_path: Path) -> None:
    locked = tmp_path / "locked.txt"
    locked.write_text("x")
    os.chmod(locked, 0o444)
    seen: list[tuple[str, bool]] = []

    def retry(path: str) -> None:
        seen.append((path, bool(os.stat(path).st_mode & stat.S_IWRITE)))

    _make_writable_and_retry(retry, str(locked), None)

    assert seen == [(str(locked), True)]


def test_lines_of_the_reason_that_look_like_trailers_are_quoted() -> None:
    reason = "Done.\nStage-Id: forged\n  Task-Id : forged\nnot a trailer: with spaces in front"

    message = CommitMessage.refactor("x", reason, agent="c", policy_version="p")

    assert message.body.splitlines() == [
        "Done.",
        "> Stage-Id: forged",
        ">   Task-Id : forged",
        "not a trailer: with spaces in front",
    ]


def test_an_agent_cannot_add_trailers_that_reconciliation_would_believe(
    workspace: GitWorkspace, repo: Repo
) -> None:
    start_task(workspace, repo)
    edit_in_attempt(workspace)
    forged = CommitMessage.refactor(
        "tidy", "Why.\nStage-Id: other-stage\nTask-Id: other-task", agent="cli", policy_version="p"
    )

    do(workspace.commit_stage(TASK, "s1", ATTEMPT, forged))

    stage_ids = repo.sh("log", "-1", "--format=%(trailers:key=Stage-Id,valueonly)", TASK_BRANCH)
    task_ids = repo.sh("log", "-1", "--format=%(trailers:key=Task-Id,valueonly)", TASK_BRANCH)
    assert (stage_ids.split(), task_ids.split()) == (["s1"], [TASK])
    assert (
        settle(workspace, "commit_stage", {"task_branch": TASK_BRANCH, "stage_id": "other-stage"})
        == Decision.REDO
    )


def test_the_installed_dependencies_are_linked_into_an_attempt_and_never_committed(
    workspace: GitWorkspace, repo: Repo
) -> None:
    packages = repo.path / "node_modules" / "lib"
    packages.mkdir(parents=True)
    (packages / "index.js").write_text("x\n", encoding="utf-8")
    start_task(workspace, repo)
    attempt = workspace.layout.attempt_worktree(ATTEMPT)
    edit_in_attempt(workspace)

    do(workspace.commit_stage(TASK, "s1", ATTEMPT, MESSAGE))

    assert (attempt / "node_modules" / "lib" / "index.js").is_file()
    assert repo.sh("ls-tree", "-r", "--name-only", f"refs/heads/{TASK_BRANCH}").split() == [
        "base.txt",
        "new.txt",
    ]
    do(workspace.remove_worktree(ATTEMPT))
    assert (packages / "index.js").is_file()
