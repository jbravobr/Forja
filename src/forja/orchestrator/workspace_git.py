"""The git side of an execution: branches, worktrees and commits, as steps the journal runs once.

Nothing here talks to a remote. Every git command goes through the git passed in, which in
production is the restricted facade, so what the allow-list refuses is refused here too. The
agent never works in these worktrees: it works in a copy without ``.git``, and Forja brings the
result back (that part lives with the agent runner).

Layout of a run:

- the run branch ``forja-run/<run>`` is checked out in its own worktree, so merges and the style
  commit never touch the user's working tree;
- each task has a branch ``forja/<run>/<task>``;
- each attempt has a throw-away worktree that checks out the task branch itself, so committing
  moves the branch and no command that moves refs by hand is needed.
"""

import json
import os
import re
import shutil
import stat
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forja.core.dependencies import link_dependencies, stage_all, unlink_dependencies
from forja.core.events import OpenOperation
from forja.core.gitguard import GitWatch
from forja.core.gitguard.snapshot import Difference, DifferenceKind
from forja.core.idempotency import (
    OperationName,
    Reconciliation,
    normalize_stage_id,
    reconcile_commit_stage,
    reconcile_create_attempt_worktree,
    reconcile_create_run_branch,
    reconcile_create_task_branch,
    reconcile_integrate_task,
    reconcile_normalize_style,
    reconcile_remove_worktree,
    reconcile_rollback_integration,
    run_branch_name,
    task_branch_name,
)
from forja.core.idempotency.git import Git
from forja.core.locking import IndexLockReport, remove_orphan_index_locks
from forja.core.locking.index_locks import git_is_running
from forja.orchestrator.operations import Step

FORJA_NAME = "Forja"
FORJA_EMAIL = "forja@localhost"
# Git matches these case-insensitively; as constants they are not an opportunity for a typo.
_USER_NAME_KEY = "user.name"
_USER_EMAIL_KEY = "user.email"
_TASK_TRAILER = "Task-Id"
SUBJECT_LIMIT = 72
BODY_LINES = 20
# What git reads as a trailer. The body is written by the agent, so a line like this in it could
# add a Stage-Id or Task-Id that Forja never put there, and reconciliation would believe it.
_TRAILER_LIKE = re.compile(r"^\s*[A-Za-z][A-Za-z0-9-]*\s*:")
# Conventional Commits, with the types the commits of a stage have: type, optional scope, text.
_CONVENTIONAL = re.compile(r"^(docs|feat|fix|refactor|style|test)(\([a-z0-9][a-z0-9-]*\))?: \S")
_NOT_IN_A_TRAILER_NAME = re.compile(r"[^A-Za-z0-9]+")
# Where git keeps the notes of the commits. Forja writes one for each stage, so a task that
# commits while the agent of another runs moves this ref.
NOTES_REF = "refs/notes/commits"
HANDLED_OPERATIONS = frozenset(
    {
        OperationName.CREATE_RUN_BRANCH,
        OperationName.CREATE_ATTEMPT_WORKTREE,
        OperationName.NORMALIZE_STYLE,
        OperationName.CREATE_TASK_BRANCH,
        OperationName.COMMIT_STAGE,
        OperationName.INTEGRATE_TASK,
        OperationName.ROLLBACK_INTEGRATION,
        OperationName.REMOVE_WORKTREE,
    }
)


class RepositoryNotReady(Exception):
    """The repository cannot be refactored right now; the message says what to do."""


class NothingToCommit(Exception):
    """The attempt was approved but its worktree holds no change."""


class IntegrationConflict(Exception):
    """The task branch did not merge into the run branch; the merge was abandoned."""


class NothingToRollBack(Exception):
    """The tip of the run branch is not the merge of this task."""


class NotConventional(ValueError):
    """The subject of a commit is not a Conventional Commits line."""


# -- who the commits are from, and the settings every git command of a run shares ----------------


@dataclass(frozen=True)
class Identity:
    """The agent CLI is the author; whoever owns the repository is the committer."""

    author_name: str
    author_email: str
    committer_name: str
    committer_email: str


async def detect_identity(git: Git, cli_name: str) -> Identity:
    """Author ``Forja (<cli>)``; committer is the identity git already has, or Forja's own."""

    async def configured(key: str, fallback: str) -> str:
        result = await git.run("config", "--get", key)
        value = result.lines[0].strip() if result.exit_code == 0 and result.lines else ""
        return value or fallback

    return Identity(
        f"{FORJA_NAME} ({cli_name})",
        FORJA_EMAIL,
        await configured(_USER_NAME_KEY, FORJA_NAME),
        await configured(_USER_EMAIL_KEY, FORJA_EMAIL),
    )


def git_environment(base: Mapping[str, str], identity: Identity) -> dict[str, str]:
    """``base`` plus the identity and long-path support, given as environment variables.

    The restricted facade refuses ``-c`` before the subcommand, so settings travel this way.
    """
    return {
        **base,
        "GIT_AUTHOR_NAME": identity.author_name,
        "GIT_AUTHOR_EMAIL": identity.author_email,
        "GIT_COMMITTER_NAME": identity.committer_name,
        "GIT_COMMITTER_EMAIL": identity.committer_email,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.longpaths",
        "GIT_CONFIG_VALUE_0": "true",
    }


@dataclass(frozen=True)
class Layout:
    """Where the worktrees of a run live: a short path, because Windows paths are limited."""

    root: Path

    @classmethod
    def for_run(cls, home: Path, run_id: str) -> "Layout":
        return cls(home / "wt" / run_id[:8])

    def worktree(self, name: str) -> Path:
        return self.root / name

    @property
    def run_worktree(self) -> Path:
        return self.worktree("run")

    def attempt_worktree(self, attempt_id: str) -> Path:
        return self.worktree(attempt_id)


def workcopy_path(worktree: Path) -> Path:
    """Where the copy the agent works in lies: beside the attempt's worktree, never inside it.

    The agent runner makes it and the workspace removes it with the worktree, so both name it here.
    """
    return worktree.with_name(f"{worktree.name}-copy")


_REF_KINDS = frozenset(
    {DifferenceKind.REF_ADDED, DifferenceKind.REF_REMOVED, DifferenceKind.REF_MOVED}
)


def _same_path(path: str | Path) -> str:
    return os.path.normcase(os.path.realpath(path))


def others_work(
    run_id: str,
    layout: Layout,
    task_id: str,
    attempt_id: str,
    tasks: Callable[[], Collection[str]],
) -> Callable[[Difference], bool]:
    """Whether a difference is Forja's own work for another task of the run (a branch, the run
    branch, a worktree of the run), which tasks running together do while an agent is called. The
    task's own branch and attempt worktree, and anything outside the run, stay the agent's."""
    own_branch = f"refs/heads/{task_branch_name(run_id, task_id)}"
    run_branch = f"refs/heads/{run_branch_name(run_id)}"
    own_tree = _same_path(layout.attempt_worktree(attempt_id))
    root = _same_path(layout.root) + os.sep

    def theirs(difference: Difference) -> bool:
        subject = difference.subject
        if difference.kind in _REF_KINDS:
            if subject in (run_branch, NOTES_REF):
                return True
            names = {f"refs/heads/{task_branch_name(run_id, other)}" for other in tasks()}
            return subject in names - {own_branch}
        if difference.kind in (DifferenceKind.CONFIG_CHANGED, DifferenceKind.HOOKS_CHANGED):
            return False
        tree = _same_path(subject)
        return tree.startswith(root) and tree != own_tree

    return theirs


def conventional(subject: str) -> str:
    """``subject`` as it is, or ``NotConventional`` when it is not ``type(scope): text``."""
    if not _CONVENTIONAL.match(subject):
        raise NotConventional(f"{subject!r} is not a Conventional Commits subject")
    return subject


@dataclass(frozen=True)
class StageFacts:
    """What Forja knows of an approved stage: the checks that approved it, each number as the
    base of the run had it and as the stage leaves it (``None`` for one not measured), and what
    the agent said it had to guess."""

    evidence: tuple[str, ...] = ()
    metrics: tuple[tuple[str, float | None, float | None], ...] = ()
    assumptions: tuple[str, ...] = ()

    def trailers(self) -> tuple[tuple[str, str], ...]:
        """One ``Evidence-Id`` for each check, one ``Metric-<name>`` for each number that
        changed, one ``Assumption`` for each guess; each value on a single line."""
        found = [("Evidence-Id", check) for check in self.evidence]
        for name, before, after in self.metrics:
            if before is not None and after is not None and before != after:
                key = _NOT_IN_A_TRAILER_NAME.sub("-", name).strip("-")
                found.append((f"Metric-{key}", f"{before:g} -> {after:g}"))
        guesses = (" ".join(statement.split()) for statement in self.assumptions)
        found += [("Assumption", guess) for guess in guesses if guess]
        return tuple(found)

    def note(self) -> str:
        """Every number, changed or not, and the checks, as the git note of the commit; empty
        when nothing was measured."""
        if not self.metrics:
            return ""
        numbers = {name: {"before": b, "after": a} for name, b, a in self.metrics}
        return json.dumps({"gates_passed": [*self.evidence], "metrics": numbers}, indent=2)


@dataclass(frozen=True)
class CommitMessage:
    """Subject from the task, reason from the agent, facts from Forja as trailers."""

    subject: str
    body: str
    agent: str
    policy_version: str
    facts: StageFacts = field(default_factory=StageFacts)

    def trailers(
        self, task_id: str, attempt_id: str, *stage: tuple[str, str]
    ) -> tuple[tuple[str, str], ...]:
        """The trailers of the commit, in the order they are written; ``stage`` is the
        ``Stage-Id`` of the commit that says the stage is done."""
        return (
            ("Task-Id", task_id),
            *stage,
            ("Attempt-Id", attempt_id),
            ("Agent", self.agent),
            ("Policy-Version", self.policy_version),
            *self.facts.trailers(),
        )

    @classmethod
    def refactor(
        cls, intent: str, reason: str, *, agent: str, policy_version: str
    ) -> "CommitMessage":
        return cls.of("refactor", intent, reason, agent=agent, policy_version=policy_version)

    @classmethod
    def of(
        cls, kind: str, intent: str, reason: str, *, agent: str, policy_version: str
    ) -> "CommitMessage":
        """``kind`` is the conventional-commit type: refactor, fix or test."""
        subject = conventional(f"{kind}: {' '.join(intent.split())}"[:SUBJECT_LIMIT].rstrip())
        lines = reason.strip().splitlines()[:BODY_LINES]
        body = "\n".join(_defuse(line.rstrip()) for line in lines)
        return cls(subject, body.strip(), agent, policy_version)


# -- the workspace -------------------------------------------------------------------------------


class GitWorkspace:
    def __init__(
        self,
        git: Git,
        *,
        run_id: str,
        layout: Layout,
        git_running: Callable[[], bool] = git_is_running,
        repo: Path | None = None,
    ) -> None:
        self._git = git
        self._repo = repo  # where the installed dependencies are; None links nothing
        self._git_running = git_running
        self.run_id = run_id
        self.layout = layout
        self.run_branch = run_branch_name(run_id)

    @property
    def run_worktree(self) -> Path:
        return self.layout.run_worktree

    def attempt_worktree(self, attempt_id: str) -> Path:
        return self.layout.attempt_worktree(attempt_id)

    # -- before the run ---------------------------------------------------------------------------

    async def check_ready(self) -> str:
        """The commit the run starts from; refuses a repository the run could not honour."""
        head = await self._git.run("symbolic-ref", "HEAD")
        if head.exit_code != 0:
            raise RepositoryNotReady("HEAD is detached; check out a branch and try again")
        changes = await self._git.lines("status", "--porcelain")
        if changes:
            raise RepositoryNotReady(
                f"{len(changes)} path(s) have uncommitted changes; commit or stash them first, "
                "because the run works on committed code"
            )
        (commit,) = await self._git.lines("rev-parse", "HEAD")
        return commit

    async def recover(self) -> IndexLockReport:
        """What a resume does before reconciling: stale locks go, stale worktree records go."""
        report = await remove_orphan_index_locks(self._git, git_running=self._git_running)
        await self._git.lines("worktree", "prune")
        return report

    def watch(
        self,
        task_id: str | None = None,
        attempt_id: str | None = None,
        tasks: Callable[[], Collection[str]] = tuple,
    ) -> GitWatch:
        """Photograph refs, HEAD, index, config and hooks around a call to the agent. With the task
        and the attempt, the work Forja does for the run's other tasks is not counted."""
        if task_id is None or attempt_id is None:
            return GitWatch(self._git)
        theirs = others_work(self.run_id, self.layout, task_id, attempt_id, tasks)
        return GitWatch(self._git, theirs)

    # -- the run branch and its worktree ----------------------------------------------------------

    def create_run_branch(self, base_commit: str) -> Step:
        async def run() -> None:
            await self._git.lines("branch", self.run_branch, base_commit)

        return Step.plain({"branch": self.run_branch, "base": base_commit}, run)

    def create_run_worktree(self) -> Step:
        return self._add_worktree(self.layout.run_worktree, self.run_branch)

    def normalize_style(self, apply: Callable[[Path], Awaitable[None]]) -> Step:
        """Run ``apply`` (the formatter) in the run worktree and commit whatever it changed."""
        stage_id = normalize_stage_id(self.run_id)

        async def run() -> None:
            worktree = self.layout.run_worktree
            await apply(worktree)
            git = self._git.at(worktree)
            if await git.lines("status", "--porcelain"):
                await stage_all(git)
                # with core.autocrlf a rewrite of line endings shows in status but stages nothing
                if await git.lines("diff", "--cached", "--name-only"):
                    await git.lines(
                        "commit",
                        "-m",
                        "style: normalize formatting",
                        "--trailer",
                        f"Stage-Id: {stage_id}",
                    )

        return Step.plain({"run_branch": self.run_branch, "stage_id": stage_id}, run)

    # -- a task ---------------------------------------------------------------------------------

    def create_task_branch(self, task_id: str) -> Step:
        branch = task_branch_name(self.run_id, task_id)

        async def run() -> None:
            await self._git.lines("branch", branch, self.run_branch)

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
        """Commit the stage's work. The files in ``apart`` go first in a commit of their own, with
        ``apart_message`` and no ``Stage-Id``, when the stage changed other files too: only the
        last commit says the stage is done, so a run cut between the two repeats only the last."""
        branch = task_branch_name(self.run_id, task_id)

        def arguments(note: CommitMessage, *trailers: tuple[str, str]) -> list[str]:
            args = ["commit", "-m", note.subject]
            if note.body:
                args += ["-m", note.body]
            for key, value in note.trailers(task_id, attempt_id, *trailers):
                args += ["--trailer", f"{key}: {value}"]
            return args

        async def run() -> None:
            git = self._git.at(self.layout.attempt_worktree(attempt_id))
            await stage_all(git)
            changed = await git.lines("diff", "--cached", "--name-only")
            if not changed:
                raise NothingToCommit(f"attempt {attempt_id} changed nothing")
            first = [path for path in changed if path in set(apart)]
            if apart_message is not None and first and len(first) < len(changed):
                await git.run("reset", "-q")
                await git.run("add", "--", *first)
                await git.lines(*arguments(apart_message))
                await stage_all(git)
            await git.lines(*arguments(message, ("Stage-Id", stage_id)))
            detail = message.facts.note()
            if detail:
                await git.lines("notes", "add", "-f", "-m", detail, "HEAD")

        return Step.plain({"task_branch": branch, "stage_id": stage_id}, run)

    def integrate_task(self, task_id: str, subject: str) -> Step:
        """Merge the task branch into the run branch with a merge commit that names the task."""
        branch = task_branch_name(self.run_id, task_id)
        message = f"{' '.join(subject.split())[:SUBJECT_LIMIT]}\n\nTask-Id: {task_id}"

        async def run() -> None:
            git = self._git.at(self.layout.run_worktree)
            await git.run("merge", "--abort")  # a merge cut short by a crash would block this one
            result = await git.run("merge", "--no-ff", "-m", message, branch)
            if result.exit_code != 0:
                await git.run("merge", "--abort")
                raise IntegrationConflict(f"{branch} does not merge into {self.run_branch}")

        return Step.plain({"task_branch": branch, "run_branch": self.run_branch}, run)

    def rollback_integration(self, task_id: str) -> Step:
        """Put the run branch back where it was before the merge of ``task_id``."""

        async def payload() -> dict[str, Any]:
            return {
                "run_branch": self.run_branch,
                "expected_tip": await self._before_merge(task_id),
            }

        async def run() -> None:
            target = await self._before_merge(task_id)
            await self._git.at(self.layout.run_worktree).lines("reset", "--hard", target)

        return Step(payload, run)

    def remove_worktree(self, name: str) -> Step:
        """Remove the worktree of an attempt, or the run worktree when ``name`` is ``run``."""
        path = self.layout.worktree(name)

        async def run() -> None:
            await self._clear(path)
            # the copy the agent worked in is as large as the worktree and nothing reads it again
            copy = workcopy_path(path)
            if copy.exists():
                shutil.rmtree(copy, onexc=_make_writable_and_retry)

        return Step.plain({"worktree": str(path)}, run)

    # -- settling an open operation -------------------------------------------------------------

    async def reconcile(self, operation: OpenOperation) -> Reconciliation:
        """What became of one of this workspace's operations that has an intent but no effect."""
        p: Any = operation.payload
        git = self._git
        match operation.operation:
            case "create_run_branch":
                return await reconcile_create_run_branch(git, p["branch"])
            case "create_task_branch":
                return await reconcile_create_task_branch(git, p["branch"])
            case "create_attempt_worktree":
                return await reconcile_create_attempt_worktree(
                    git, Path(p["worktree"]), p["expected_head"]
                )
            case "normalize_style":
                return await reconcile_normalize_style(git, p["run_branch"], p["stage_id"])
            case "commit_stage":
                return await reconcile_commit_stage(git, p["task_branch"], p["stage_id"])
            case "integrate_task":
                return await reconcile_integrate_task(git, p["task_branch"], p["run_branch"])
            case "rollback_integration":
                return await reconcile_rollback_integration(git, p["run_branch"], p["expected_tip"])
            case "remove_worktree":
                return await reconcile_remove_worktree(git, Path(p["worktree"]))
        raise KeyError(f"{operation.operation} is not a git operation")

    # -- helpers --------------------------------------------------------------------------------

    def _add_worktree(self, path: Path, branch: str) -> Step:
        async def payload() -> dict[str, Any]:
            (tip,) = await self._git.lines("rev-parse", f"refs/heads/{branch}")
            return {"worktree": str(path), "expected_head": tip}

        async def run() -> None:
            await self._clear(path)
            await self._git.lines("worktree", "add", str(path), branch)
            if self._repo is not None:
                link_dependencies(self._repo, path)

        return Step(payload, run)

    async def _before_merge(self, task_id: str) -> str:
        """The commit the run branch had before the merge of ``task_id``, which must be its tip."""
        tip = f"refs/heads/{self.run_branch}"
        trailers = await self._git.lines(
            "log", "-1", f"--format=%(trailers:key={_TASK_TRAILER},valueonly)", tip
        )
        if task_id not in {line.strip() for line in trailers}:
            raise NothingToRollBack(f"{self.run_branch} does not end with the merge of {task_id}")
        (before,) = await self._git.lines("rev-parse", f"{tip}^1")
        return before

    async def _clear(self, path: Path) -> None:
        """Make ``path`` and git's record of it disappear, whatever state an interruption left."""
        if path.exists():
            unlink_dependencies(path)
            # only read-only files (Windows) need the handler; Linux cannot tell it from none
            shutil.rmtree(path, onexc=_make_writable_and_retry)
        await self._git.lines("worktree", "prune")


def _defuse(line: str) -> str:
    """Quote a line that git would read as a trailer."""
    return f"> {line}" if _TRAILER_LIKE.match(line) else line


def _make_writable_and_retry(function: Callable[[str], object], path: str, _error: Any) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)
