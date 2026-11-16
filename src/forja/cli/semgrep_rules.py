"""The rules Semgrep runs with are the user's, never the Forja's.

They come from ``--semgrep-config`` or ``execucao.toml``, else from ``.semgrep.yml`` or
``.semgrep/`` in the base commit. A registry reference is refused, since the rules would then
come from the network. The run reads a copy made outside the attempts' worktrees, so an agent
that edits the rules cannot silence a finding.
"""

import shutil
from pathlib import Path
from typing import Protocol

from forja.checks.changes import unquote, write_files

RULE_FILE = ".semgrep.yml"
RULE_DIR = ".semgrep"
BAD_CHARACTERS = ("\\", ":", "\x00")
REGISTRY = ("p/", "r/", "s/", "semgrep.dev/")


class RulesError(Exception):
    """The rules the person named cannot be used; the message says what to do."""


class GitLines(Protocol):
    async def lines(self, *args: str) -> list[str]: ...


def explicit_rules(value: str) -> Path:
    """The local file or directory of rules the person named, as an absolute path."""
    if value == "auto" or value.startswith(REGISTRY) or "://" in value:
        raise RulesError(
            f"`{value}` aponta para o registro do Semgrep. A Forja só usa regras locais que "
            "você fornece: um arquivo ou um diretório."
        )
    path = Path(value)
    if not path.exists():
        raise RulesError(f"Não encontrei as regras do Semgrep em {value}.")
    return path.resolve()


async def rules_in_commit(git: GitLines, commit: str) -> tuple[str, ...]:
    """The files of ``.semgrep.yml`` and ``.semgrep/`` in ``commit``."""
    listed = await git.lines("ls-tree", "-r", "--name-only", commit, "--", RULE_FILE, RULE_DIR)
    names = tuple(unquote(name) for name in listed)
    for name in names:
        if not _inside(name):
            raise RulesError(f"O nome `{name}` no commit base não serve como arquivo de regras.")
    return names


def _inside(name: str) -> bool:
    """Whether a name from the commit stays under its own directory when written to disk."""
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return not any(char in name for char in BAD_CHARACTERS)


def _renamed(name: str) -> str:
    """Without the leading dot, so no tool takes the copy for a hidden file."""
    return name.removeprefix(".")


async def export_rules(git: GitLines, commit: str, explicit: str, target: Path) -> Path | None:
    """A copy of the rules in ``target``, which the run reads; ``None`` when there are none."""
    shutil.rmtree(target, ignore_errors=True)
    if explicit:
        source = explicit_rules(explicit)
        target.mkdir(parents=True)
        copy = target / source.name
        if source.is_dir():
            shutil.copytree(source, copy)
        else:
            shutil.copy2(source, copy)
        return copy
    names = await rules_in_commit(git, commit)
    if not names:
        return None
    texts = {}
    for name in names:
        text = "\n".join(await git.lines("show", f"{commit}:{name}"))
        texts[_renamed(name)] = text + "\n"
    write_files(texts, target)
    return target
