"""A ``Workspace`` over ``MemoryRepo``: the same steps and verdicts as ``GitWorkspace``, no git.

Worktrees are plain directories, so an agent or a measurer that writes files into one works the
same; what is in memory is the history around them.
"""

import os
import shutil
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from forja.core.events import OpenOperation
from forja.core.gitguard.snapshot import Difference, DifferenceKind
from forja.core.idempotency import (
    Decision,
    Reconciliation,
    ReconciliationError,
    normalize_stage_id,
    run_branch_name,
    task_branch_name,
)
from forja.core.locking.index_locks import IndexLockReport
from forja.orchestrator.operations import Step
from forja.orchestrator.workspace_git import (
    SUBJECT_LIMIT,
    CommitMessage,
    IntegrationConflict,
    Layout,
    NothingToCommit,
    NothingToRollBack,
    RepositoryNotReady,
    others_work,
    workcopy_path,
)
from forja.testing.repo import MemoryRepo, MergeConflict, read_tree, write_tree

_DONE = Reconciliation(Decision.ALREADY_DONE)
_REDO = Reconciliation(Decision.REDO)


def _verdict(done: bool) -> Reconciliation:
    return _DONE if done else _REDO


def _key(path: Path) -> str:
    return os.path.normcase(os.path.realpath(path))


class MemoryWatch:
    """What changed in the branches between entering and leaving the block."""

    def __init__(
        self, repo: MemoryRepo, ignore: Callable[[Difference], bool] | None = None
    ) -> None:
        self._repo = repo
        self._ignore = ignore
        self._before: dict[str, str] = {}
        self.differences: list[Difference] = []

    async def __aenter__(self) -> Self:
        self._before = dict(self._repo.branches)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        after = self._repo.branches
        found = []
        for name in sorted(self._before.keys() | after.keys()):
            ref = f"refs/heads/{name}"
            before, now = self._before.get(name), after.get(name)
            if before is None:
                found.append(Difference(DifferenceKind.REF_ADDED, ref, None, now))
            elif now is None:
                found.append(Difference(DifferenceKind.REF_REMOVED, ref, before, None))
            elif before != now:
                found.append(Difference(DifferenceKind.REF_MOVED, ref, before, now))
        self.differences = [d for d in found if self._ignore is None or not self._ignore(d)]


class MemoryWorkspace:
    def __init__(self, repo: MemoryRepo, *, run_id: str, layout: Layout) -> None:
        self.repo = repo
        self.run_id = run_id
        self.layout = layout
        self.run_branch = run_branch_name(run_id)
        self.dirty = False  # set it to make ``check_ready`` refuse, like a repository with edits

    @property
    def run_worktree(self) -> Path:
        return self.layout.run_worktree

    def attempt_worktree(self, attempt_id: str) -> Path:
        return self.layout.attempt_worktree(attempt_id)

    # -- before the run ---------------------------------------------------------------------------

    async def check_ready(self) -> str:
        if self.dirty:
            raise RepositoryNotReady(
                "1 path(s) have uncommitted changes; commit or stash them first"
            )
        return self.repo.tip(self.repo.branch).sha

    async def recover(self) -> IndexLockReport:
        return IndexLockReport((), ())

    def watch(
        self,
        task_id: str | None = None,
        attempt_id: str | None = None,
        tasks: Callable[[], Collection[str]] = tuple,
    ) -> MemoryWatch:
        if task_id is None or attempt_id is None:
            return MemoryWatch(self.repo)
        return MemoryWatch(
            self.repo, others_work(self.run_id, self.layout, task_id, attempt_id, tasks)
        )

    # -- the run branch and its worktree ----------------------------------------------------------

    def create_run_branch(self, base_commit: str) -> Step:
        async def run() -> None:
            self.repo.create_branch(self.run_branch, base_commit)

        return Step.plain({"branch": self.run_branch, "base": base_commit}, run)

    def create_run_worktree(self) -> Step:
        return self._add_worktree(self.layout.run_worktree, self.run_branch)

    def normalize_style(self, apply: Callable[[Path], Awaitable[None]]) -> Step:
        stage_id = normalize_stage_id(self.run_id)

        async def run() -> None:
            worktree = self.layout.run_worktree
            await apply(worktree)
            tip = self.repo.tip(self.run_branch)
            tree = read_tree(worktree)
            if tree != tip.tree:
                made = self.repo.commit(
                    tree,
                    "style: normalize formatting",
                    trailers=(("Stage-Id", stage_id),),
                    parents=(tip.sha,),
                )
                self.repo.advance(self.run_branch, made)

        return Step.plain({"run_branch": self.run_branch, "stage_id": stage_id}, run)

    # -- a task -----------------------------------------------------------------------------------

    def create_task_branch(self, task_id: str) -> Step:
        branch = task_branch_name(self.run_id, task_id)

        async def run() -> None:
            self.repo.create_branch(branch, self.repo.branches[self.run_branch])

        return Step.plain({"branch": branch}, run)

    def create_attempt_worktree(self, task_id: str, attempt_id: str) -> Step:
        return self._add_worktree(
            self.layout.attempt_worktree(attempt_id), task_branch_name(self.run_id, task_id)
        )

    def commit_stage(
        self,
        task_id: str,
        stage_id: str,
        attempt_id: str,
        message: CommitMessage,
        apart: Sequence[str] = (),
        apart_message: CommitMessage | None = None,
    ) -> Step:
        branch = task_branch_name(self.run_id, task_id)

        def commit(
            tree: Mapping[str, str], note: CommitMessage, *trailers: tuple[str, str]
        ) -> None:
            tip = self.repo.tip(branch)
            made = self.repo.commit(
                tree,
                note.subject,
                body=note.body,
                trailers=note.trailers(task_id, attempt_id, *trailers),
                parents=(tip.sha,),
            )
            self.repo.advance(branch, made)
            if trailers and note.facts.note():  # only the commit that ends the stage has one
                self.repo.notes[made.sha] = note.facts.note()

        async def run() -> None:
            tip = self.repo.tip(branch)
            tree = read_tree(self.layout.attempt_worktree(attempt_id))
            if tree == tip.tree:
                raise NothingToCommit(f"attempt {attempt_id} changed nothing")
            changed = {p for p in tree.keys() | tip.tree.keys() if tree.get(p) != tip.tree.get(p)}
            first = changed & set(apart)
            if apart_message is not None and first and first != changed:
                middle = {p: t for p, t in tip.tree.items() if p not in first}
                middle |= {p: tree[p] for p in first if p in tree}
                commit(middle, apart_message)
            commit(tree, message, ("Stage-Id", stage_id))

        return Step.plain({"task_branch": branch, "stage_id": stage_id}, run)

    def integrate_task(self, task_id: str, subject: str) -> Step:
        branch = task_branch_name(self.run_id, task_id)
        line = " ".join(subject.split())[:SUBJECT_LIMIT]

        async def run() -> None:
            ours, theirs = self.repo.branches[self.run_branch], self.repo.branches[branch]
            if self.repo.is_ancestor(theirs, ours):
                return  # git says "Already up to date" and makes no merge commit
            try:
                tree = self.repo.merge_trees(ours, theirs)
            except MergeConflict:
                raise IntegrationConflict(
                    f"{branch} does not merge into {self.run_branch}"
                ) from None
            made = self.repo.commit(
                tree, line, trailers=(("Task-Id", task_id),), parents=(ours, theirs)
            )
            self.repo.advance(self.run_branch, made)
            write_tree(self.layout.run_worktree, tree)

        return Step.plain({"task_branch": branch, "run_branch": self.run_branch}, run)

    def rollback_integration(self, task_id: str) -> Step:
        async def payload() -> dict[str, Any]:
            return {"run_branch": self.run_branch, "expected_tip": self._before_merge(task_id)}

        async def run() -> None:
            target = self._before_merge(task_id)
            self.repo.branches[self.run_branch] = target
            write_tree(self.layout.run_worktree, self.repo.commits[target].tree)

        return Step(payload, run)

    def remove_worktree(self, name: str) -> Step:
        path = self.layout.worktree(name)

        async def run() -> None:
            self._clear(path)
            self._clear(workcopy_path(path))

        return Step.plain({"worktree": str(path)}, run)

    # -- settling an open operation ---------------------------------------------------------------

    async def reconcile(self, operation: OpenOperation) -> Reconciliation:
        p: Any = operation.payload
        repo = self.repo
        match operation.operation:
            case "create_run_branch" | "create_task_branch":
                return _verdict(p["branch"] in repo.branches)
            case "create_attempt_worktree":
                path = Path(p["worktree"])
                return _verdict(
                    repo.worktrees.get(_key(path)) == p["expected_head"] and path.is_dir()
                )
            case "normalize_style":
                return self._staged(p["run_branch"], p["stage_id"])
            case "commit_stage":
                return self._staged(p["task_branch"], p["stage_id"])
            case "integrate_task":
                for branch in (p["task_branch"], p["run_branch"]):
                    self._require(branch)
                return _verdict(
                    repo.is_ancestor(
                        repo.branches[p["task_branch"]], repo.branches[p["run_branch"]]
                    )
                )
            case "rollback_integration":
                self._require(p["run_branch"])
                return _verdict(repo.branches[p["run_branch"]] == p["expected_tip"])
            case "remove_worktree":
                path = Path(p["worktree"])
                return _verdict(_key(path) not in repo.worktrees and not path.exists())
        raise KeyError(f"{operation.operation} is not a git operation")

    # -- helpers ----------------------------------------------------------------------------------

    def _require(self, branch: str) -> None:
        if branch not in self.repo.branches:
            raise ReconciliationError(f"branch {branch!r} does not exist")

    def _staged(self, branch: str, stage_id: str) -> Reconciliation:
        self._require(branch)
        seen = {value for made in self.repo.log(branch) for value in made.trailer("Stage-Id")}
        return _verdict(stage_id in seen)

    def _add_worktree(self, path: Path, branch: str) -> Step:
        async def payload() -> dict[str, Any]:
            return {"worktree": str(path), "expected_head": self.repo.branches[branch]}

        async def run() -> None:
            self._clear(path)
            tip = self.repo.tip(branch)
            write_tree(path, tip.tree)
            self.repo.worktrees[_key(path)] = tip.sha

        return Step(payload, run)

    def _before_merge(self, task_id: str) -> str:
        tip = self.repo.tip(self.run_branch)
        if task_id not in {value.strip() for value in tip.trailer("Task-Id")}:
            raise NothingToRollBack(f"{self.run_branch} does not end with the merge of {task_id}")
        return tip.parents[0]

    def _clear(self, path: Path) -> None:
        if path.exists():
            shutil.rmtree(path)
        self.repo.worktrees.pop(_key(path), None)
