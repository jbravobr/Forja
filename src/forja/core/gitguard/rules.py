"""What Forja itself may ask git to do.

An allow-list of subcommands with checks on their flags. Anything not listed fails. Rules that
need the state of the repository (is this branch protected? is its tip already published?) are
applied by ``GuardedGit``; this module only decides from the words of the command.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass


class GitCommandRefused(Exception):
    def __init__(self, args_: Sequence[str], reason: str) -> None:
        self.reason = reason
        super().__init__(f"git {' '.join(args_)}: {reason}")


@dataclass(frozen=True)
class Command:
    sub: str
    flags: frozenset[str]  # "--force", "-f"; values after "=" are dropped
    positionals: tuple[str, ...]


# Options whose next word is their value, so it is not mistaken for a flag or a positional.
VALUE_OPTIONS: dict[str, frozenset[str]] = {
    "commit": frozenset(
        {
            "-m",
            "--message",
            "-F",
            "--file",
            "-C",
            "-c",
            "--author",
            "--date",
            "--cleanup",
            "--trailer",
        }
    ),
    "worktree": frozenset({"-b", "-B", "--reason"}),
    "tag": frozenset({"-m", "--message", "-F", "--file", "-u", "--local-user"}),
    "notes": frozenset({"-m", "--message", "-F", "--file", "-C", "-c", "--ref"}),
    "merge": frozenset({"-m", "--message", "-F", "--file", "-s", "--strategy", "-X"}),
    "checkout": frozenset({"-b", "--orphan"}),
    "switch": frozenset({"-c", "--create", "--orphan"}),
    "fetch": frozenset({"--depth", "--deepen", "--jobs", "-j"}),
    "reset": frozenset(),
}

READ_ONLY = frozenset(
    {
        "rev-parse",
        "show-ref",
        "for-each-ref",
        "log",
        "show",
        "ls-remote",
        "ls-files",
        "status",
        "diff",
        "merge-base",
        "cat-file",
        "rev-list",
        "describe",
        "ls-tree",
    }
)
# Options that make git start a program named by the caller.
RUNS_A_PROGRAM = {"--upload-pack", "--exec", "--receive-pack", "--ext-diff"}
WRITING = frozenset(
    {
        "init",
        "add",
        "commit",
        "checkout",
        "switch",
        "branch",
        "tag",
        "worktree",
        "merge",
        "fetch",
        "reset",
        "notes",
        "config",
        "symbolic-ref",
        "remote",
    }
)
ALLOWED = READ_ONLY | WRITING

# Subcommands that put a commit on the branch HEAD is on, or move that branch.
HEAD_WRITERS = frozenset({"commit", "merge", "reset"})


def parse(args: Sequence[str]) -> Command:
    if not args:
        raise GitCommandRefused(args, "no subcommand")
    if args[0].startswith("-"):
        raise GitCommandRefused(args, "options before the subcommand are not allowed")
    sub, rest = args[0], args[1:]
    value_options = VALUE_OPTIONS.get(sub, frozenset())
    flags: set[str] = set()
    positionals: list[str] = []
    ended = False
    i = 0
    while i < len(rest):
        token = rest[i]
        if ended or not token.startswith("-") or token == "-":
            positionals.append(token)
        elif token == "--":
            ended = True
        elif token.startswith("--"):
            name, equals, _ = token.partition("=")
            flags.add(name)
            if not equals and name in value_options:
                i += 1
        else:
            cluster = token[1:]
            for position, char in enumerate(cluster):
                flag = f"-{char}"
                flags.add(flag)
                if flag in value_options:
                    if position == len(cluster) - 1:
                        i += 1  # the value is the next word
                    break
        i += 1
    return Command(sub, frozenset(flags), tuple(positionals))


def _refuse_flags(args: Sequence[str], command: Command, forbidden: set[str], why: str) -> None:
    # git takes a long option cut short (``--upload-pa`` for ``--upload-pack``): a prefix of a
    # forbidden name is that name
    hit = sorted(
        flag
        for flag in command.flags
        if flag in forbidden
        or (flag.startswith("--") and len(flag) > 2 and any(f.startswith(flag) for f in forbidden))
    )
    if hit:
        raise GitCommandRefused(args, f"{', '.join(hit)} {why}")


def _check_config(args: Sequence[str], command: Command) -> None:
    reads = {"--get", "--get-all", "--get-regexp", "--list", "-l"}
    if not command.flags & reads:
        raise GitCommandRefused(args, "config is read-only")
    _refuse_flags(
        args,
        command,
        {
            "--add",
            "--unset",
            "--unset-all",
            "--replace-all",
            "--remove-section",
            "--rename-section",
            "--edit",
            "-e",
        },
        "writes",
    )


def _check_symbolic_ref(args: Sequence[str], command: Command) -> None:
    if len(command.positionals) != 1 or command.flags & {"-d", "--delete"}:
        raise GitCommandRefused(args, "symbolic-ref may only read")


def _check_remote(args: Sequence[str], command: Command) -> None:
    positionals = command.positionals
    if positionals and positionals[0] not in {"get-url", "show"}:
        raise GitCommandRefused(args, "remote may only be read")


def _check_branch(args: Sequence[str], command: Command) -> None:
    _refuse_flags(
        args,
        command,
        {"-d", "-D", "--delete", "-m", "-M", "--move", "-f", "--force", "-c", "-C", "--copy"},
        "deletes, renames or moves a branch",
    )
    _refuse_flags(
        args,
        command,
        {"--set-upstream-to", "-u", "--unset-upstream", "--edit-description"},
        "edits configuration",
    )


def _check_tag(args: Sequence[str], command: Command) -> None:
    _refuse_flags(args, command, {"-d", "--delete", "-f", "--force"}, "deletes or moves a tag")


def _check_worktree(args: Sequence[str], command: Command) -> None:
    positionals = command.positionals
    if not positionals or positionals[0] not in {"add", "list", "remove", "prune"}:
        raise GitCommandRefused(args, "worktree allows only add, list, remove and prune")
    _refuse_flags(args, command, {"-B"}, "resets an existing branch")
    if positionals[0] == "add":
        _refuse_flags(args, command, {"--force", "-f"}, "bypasses worktree safety checks")


def _check_notes(args: Sequence[str], command: Command) -> None:
    positionals = command.positionals
    if not positionals or positionals[0] not in {"add", "append", "show", "list"}:
        raise GitCommandRefused(args, "notes allows only add, append, show and list")
    # a note under another ref would write where the branch rules do not look
    _refuse_flags(args, command, {"--ref"}, "writes outside the notes of the commits")


def _check_checkout(args: Sequence[str], command: Command) -> None:
    _refuse_flags(args, command, {"-B"}, "resets an existing branch")


def _check_switch(args: Sequence[str], command: Command) -> None:
    _refuse_flags(args, command, {"-C", "--force-create"}, "resets an existing branch")


def _check_fetch(args: Sequence[str], command: Command) -> None:
    _refuse_flags(args, command, {"--prune", "-p", "--prune-tags", "--update-head-ok"}, "")
    _refuse_flags(args, command, RUNS_A_PROGRAM | {"--refmap"}, "is not allowed")
    for refspec in command.positionals[1:]:
        if ":" in refspec or refspec.startswith("+"):
            # ``<source>:<destination>`` writes a local ref, a protected branch included
            raise GitCommandRefused(args, f"the refspec {refspec!r} writes a local ref")


# "commit" has no entry: --amend is judged against the state of the branch by GuardedGit.
_CHECKERS: dict[str, Callable[[Sequence[str], Command], None]] = {
    "config": _check_config,
    "symbolic-ref": _check_symbolic_ref,
    "remote": _check_remote,
    "branch": _check_branch,
    "tag": _check_tag,
    "worktree": _check_worktree,
    "notes": _check_notes,
    "checkout": _check_checkout,
    "switch": _check_switch,
    "fetch": _check_fetch,
}


def check(args: Sequence[str]) -> Command:
    """Parse ``args`` and refuse what Forja must never do. Returns the parsed command."""
    command = parse(args)
    sub = command.sub
    if sub == "push":
        raise GitCommandRefused(args, "Forja never pushes; publishing is for the user")
    if sub not in ALLOWED:
        raise GitCommandRefused(args, f"subcommand {sub!r} is not allowed")

    if sub in READ_ONLY:
        _refuse_flags(args, command, {"--output", "-o"}, "would write a file")
        _refuse_flags(args, command, RUNS_A_PROGRAM, "runs a program")
        if sub == "ls-remote":  # its short form of --upload-pack
            _refuse_flags(args, command, {"-u"}, "runs a program")
        return command

    checker = _CHECKERS.get(sub)
    if checker is not None:
        checker(args, command)
    return command


def writes(command: Command) -> bool:
    """Whether the command changes the repository, so it must take the git mutex.

    Reading commands never wait for the mutex; git guards its own files against a read that
    overlaps a write.
    """
    sub, flags, positionals = command.sub, command.flags, command.positionals
    if sub in READ_ONLY or sub in {"config", "symbolic-ref", "remote"}:
        return False
    if sub == "branch":
        return bool(positionals) and not flags & {"--list", "-l"}
    if sub == "tag":
        return bool(positionals) and not flags & {"--list", "-l"}
    if sub == "worktree":
        return bool(positionals) and positionals[0] != "list"
    if sub == "notes":
        return bool(positionals) and positionals[0] in {"add", "append"}
    return True
