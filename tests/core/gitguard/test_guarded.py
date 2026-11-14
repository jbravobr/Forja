"""The guard against a real repository: protected branches, published history, worktrees."""

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from forja.core.gitguard import GitCommandRefused, GuardedGit
from forja.core.idempotency import (
    Decision,
    reconcile_commit_stage,
    reconcile_create_attempt_worktree,
    reconcile_create_task_branch,
    reconcile_integrate_task,
    reconcile_remove_worktree,
)
from tests.core.idempotency.conftest import Repo


def run(guard: GuardedGit, *args: str):  # type: ignore[no-untyped-def]
    return asyncio.run(guard.run(*args))


def tip(repo: Repo, ref: str = "HEAD") -> str:
    return repo.sh("rev-parse", ref)


def test_reading_goes_through_untouched(repo: Repo, guard: GuardedGit) -> None:
    assert asyncio.run(guard.lines("rev-parse", "HEAD")) == [tip(repo)]


def test_a_refused_command_is_never_run(repo: Repo, guard: GuardedGit) -> None:
    repo.sh("branch", "keep")

    with pytest.raises(GitCommandRefused):
        run(guard, "branch", "-D", "keep")

    assert "keep" in repo.sh("branch", "--list", "keep")


def test_nothing_is_written_to_a_protected_branch(repo: Repo, guard: GuardedGit) -> None:
    before = tip(repo)
    repo.sh("branch", "other")

    with pytest.raises(GitCommandRefused, match="'main' is a protected branch"):
        run(guard, "commit", "--allow-empty", "-m", "x")
    with pytest.raises(GitCommandRefused, match="protected"):
        run(guard, "merge", "other")
    with pytest.raises(GitCommandRefused, match="protected"):
        run(guard, "reset", "--hard", "HEAD")

    assert tip(repo) == before


def test_other_branches_can_be_committed_to(repo: Repo, guard: GuardedGit) -> None:
    repo.sh("checkout", "-b", "forja/run1/t1")

    assert run(guard, "commit", "--allow-empty", "-m", "ok").exit_code == 0
    assert run(guard, "reset", "--hard", "HEAD~1").exit_code == 0  # still unpublished


def _publish(repo: Repo, guard: GuardedGit, new_remote: Callable[[], Path]) -> None:
    repo.sh("remote", "add", "origin", str(new_remote()))
    repo.sh("checkout", "-b", "feature")
    repo.commit("a.txt")
    repo.commit("b.txt")
    repo.sh("push", "origin", "feature")  # published by someone else, not through the guard
    assert "refs/remotes/origin/feature" in repo.sh("for-each-ref", "refs/remotes")


def test_history_that_is_already_on_the_remote_is_never_rewritten(
    repo: Repo, guard: GuardedGit, new_remote: Callable[[], Path]
) -> None:
    _publish(repo, guard, new_remote)
    before = tip(repo)

    with pytest.raises(GitCommandRefused, match="already published"):
        run(guard, "reset", "--hard", "HEAD~1")
    with pytest.raises(GitCommandRefused, match="already published"):
        run(guard, "commit", "--amend", "--no-edit")

    assert tip(repo) == before
    assert run(guard, "commit", "--allow-empty", "-m", "forward is fine").exit_code == 0


def test_a_detached_head_rewrites_no_named_history_even_at_a_published_commit(
    repo: Repo, guard: GuardedGit, new_remote: Callable[[], Path]
) -> None:
    _publish(repo, guard, new_remote)
    repo.sh("checkout", "--detach", "feature")

    assert run(guard, "reset", "--hard", "HEAD~1").exit_code == 0

    assert tip(repo, "feature") != tip(repo)  # the branch itself did not move


def test_pushing_never_reaches_the_remote_whatever_the_branch(
    repo: Repo, guard: GuardedGit, new_remote: Callable[[], Path]
) -> None:
    remote = new_remote()
    repo.sh("remote", "add", "origin", str(remote))
    repo.sh("branch", "forja-run/r1")

    for branch in ("main", "forja-run/r1"):
        with pytest.raises(GitCommandRefused, match="never pushes"):
            run(guard, "push", "origin", branch)

    assert repo.sh("ls-remote", str(remote)) == ""


def test_worktrees_are_created_removed_and_guarded_like_the_main_repository(
    repo: Repo, guard: GuardedGit, tmp_path: Path
) -> None:
    worktree = tmp_path / "attempt"
    assert run(guard, "worktree", "add", "--detach", str(worktree)).exit_code == 0

    in_worktree = guard.at(worktree)
    assert isinstance(in_worktree, GuardedGit)
    assert asyncio.run(in_worktree.run("reset", "--hard", "HEAD")).exit_code == 0
    with pytest.raises(GitCommandRefused):
        asyncio.run(in_worktree.run("branch", "-D", "main"))

    assert run(guard, "worktree", "remove", "--force", str(worktree)).exit_code == 0
    assert not worktree.exists()


def test_clones_for_other_directories_keep_numbering_their_logs_together(
    repo: Repo, guard: GuardedGit, tmp_path: Path
) -> None:
    worktree = tmp_path / "attempt"
    run(guard, "worktree", "add", "--detach", str(worktree))
    asyncio.run(guard.at(worktree).run("status"))
    run(guard, "status")

    logs = sorted(p.name for p in (tmp_path / "guard-logs").glob("git-*.log"))
    assert len(logs) == len(set(logs)) >= 3


def test_every_reconciliation_of_the_idempotency_table_runs_through_the_guard(
    repo: Repo, guard: GuardedGit, tmp_path: Path, new_remote: Callable[[], Path]
) -> None:
    """The guard must not get in the way of the reads the reconcilers do."""
    repo.sh("branch", "forja/r/t1")
    worktree = tmp_path / "wt"
    repo.sh("worktree", "add", "--detach", str(worktree))
    head = tip(repo)
    remote = new_remote()
    repo.sh("remote", "add", "origin", str(remote))
    repo.sh("push", "origin", "main")

    async def all_of_them() -> list[Decision]:
        verdicts = [
            await reconcile_create_task_branch(guard, "forja/r/t1"),
            await reconcile_create_attempt_worktree(guard, worktree, head),
            await reconcile_commit_stage(guard, "forja/r/t1", "s1"),
            await reconcile_integrate_task(guard, "forja/r/t1", "main"),
            await reconcile_remove_worktree(guard, worktree),
        ]
        return [verdict.decision for verdict in verdicts]

    assert asyncio.run(all_of_them()) == [
        Decision.ALREADY_DONE,  # the branch is there
        Decision.ALREADY_DONE,  # the worktree is on the commit
        Decision.REDO,  # no commit carries the stage
        Decision.ALREADY_DONE,  # the branch is already in main
        Decision.REDO,  # the worktree is still there
    ]
