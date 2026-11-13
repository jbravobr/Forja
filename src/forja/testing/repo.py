"""Git, reduced to what Forja's workspace uses: text files, branches, commits and merges."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

Tree = Mapping[str, str]  # repository-relative path -> text


@dataclass(frozen=True)
class MemoryCommit:
    sha: str
    tree: Tree
    subject: str
    body: str = ""
    trailers: tuple[tuple[str, str], ...] = ()
    parents: tuple[str, ...] = ()

    def trailer(self, key: str) -> list[str]:
        return [value for name, value in self.trailers if name == key]


class MergeConflict(Exception):
    """The same file changed differently on both sides."""


@dataclass
class MemoryRepo:
    """A repository whose first commit holds ``files`` and whose only branch is ``main``."""

    files: Mapping[str, str]
    branch: str = "main"
    commits: dict[str, MemoryCommit] = field(default_factory=dict)
    branches: dict[str, str] = field(default_factory=dict)
    worktrees: dict[str, str] = field(default_factory=dict)  # normalized path -> head at creation
    notes: dict[str, str] = field(default_factory=dict)  # sha -> the note of the commit

    def __post_init__(self) -> None:
        first = self.commit(self.files, "base", parents=())
        self.branches[self.branch] = first.sha
        self.base = first.sha

    # -- commits and branches -------------------------------------------------------------------

    def commit(
        self,
        tree: Tree,
        subject: str,
        *,
        body: str = "",
        trailers: tuple[tuple[str, str], ...] = (),
        parents: tuple[str, ...],
    ) -> MemoryCommit:
        made = MemoryCommit(
            f"{len(self.commits) + 1:040x}", dict(tree), subject, body, trailers, parents
        )
        self.commits[made.sha] = made
        return made

    def tip(self, branch: str) -> MemoryCommit:
        return self.commits[self.branches[branch]]

    def create_branch(self, name: str, sha: str | None = None) -> None:
        self.branches[name] = sha or self.branches[self.branch]

    def advance(self, branch: str, made: MemoryCommit) -> None:
        self.branches[branch] = made.sha

    def log(self, branch: str) -> list[MemoryCommit]:
        """The commits reachable by following first parents from the tip, newest first."""
        found = []
        current: MemoryCommit | None = self.tip(branch)
        while current is not None:
            found.append(current)
            current = self.commits[current.parents[0]] if current.parents else None
        return found

    def ancestors(self, sha: str) -> set[str]:
        seen: set[str] = set()
        pending = [sha]
        while pending:
            current = pending.pop()
            if current not in seen:
                seen.add(current)
                pending += self.commits[current].parents
        return seen

    def is_ancestor(self, older: str, newer: str) -> bool:
        return older in self.ancestors(newer)

    def merge_base(self, a: str, b: str) -> str:
        common = self.ancestors(a) & self.ancestors(b)
        # the newest common commit: the one that is not an ancestor of another common commit
        return next(
            sha for sha in common if not any(sha in self.ancestors(o) - {o} for o in common)
        )

    # -- merging ----------------------------------------------------------------------------------

    def merge_trees(self, ours: str, theirs: str) -> dict[str, str]:
        """A three-way merge by file: a file changed on one side only takes that change."""
        base = self.commits[self.merge_base(ours, theirs)].tree
        mine, yours = self.commits[ours].tree, self.commits[theirs].tree
        merged: dict[str, str] = {}
        for path in sorted(base.keys() | mine.keys() | yours.keys()):
            b, o, t = base.get(path), mine.get(path), yours.get(path)
            if o == t or t == b:
                chosen = o
            elif o == b:
                chosen = t
            else:
                raise MergeConflict(path)
            if chosen is not None:
                merged[path] = chosen
        return merged


def read_tree(directory: Path) -> dict[str, str]:
    """The text files under ``directory``, by relative path with forward slashes."""
    return {
        path.relative_to(directory).as_posix(): path.read_text(
            encoding="utf-8"
        )  # Linux defaults to utf-8; Windows tests catch it
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def write_tree(directory: Path, tree: Tree) -> None:
    """Make ``directory`` hold exactly ``tree``."""
    import shutil  # noqa: PLC0415

    if directory.exists():
        shutil.rmtree(directory)
    for relative, text in tree.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            text, encoding="utf-8", newline="\n"
        )  # Linux defaults; Windows tests catch it
    directory.mkdir(parents=True, exist_ok=True)
