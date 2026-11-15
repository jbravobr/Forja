from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Outcome(StrEnum):
    EXITED = "exited"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    SPAWN_FAILED = "spawn_failed"


MAX_ARGUMENT_CHARS = 8192
MAX_COMMAND_CHARS = 30000  # below the 32767 characters Windows allows for a whole command line
_SHELL_META = frozenset("&|<>^%" + chr(34) + chr(10) + chr(13))


class UnsafeCommand(ValueError):
    """The command could be misread by the system or smuggle a payload through its arguments."""


def check_command_safety(command: Sequence[str]) -> None:
    """Refuse commands that are dangerous on Windows or that carry the package as an argument.

    Large inputs travel in a file or on stdin, never as an argument, so no argument may be big.
    ``.cmd`` and ``.bat`` files run through ``cmd.exe``, whose quoting rules have allowed command
    injection from crafted arguments (BatBadBut, 2024); for them every shell metacharacter is
    refused.
    """
    if not command or not command[0]:
        raise UnsafeCommand("the command has no program")
    total = 0
    for argument in command:
        if chr(0) in argument:
            raise UnsafeCommand("an argument contains a NUL character")
        if len(argument) > MAX_ARGUMENT_CHARS:
            raise UnsafeCommand(
                f"an argument has {len(argument)} characters; large input goes in a file or stdin"
            )
        total += len(argument) + 1
    if total > MAX_COMMAND_CHARS:
        raise UnsafeCommand("the command line is too long")
    if Path(command[0]).suffix.lower() in {".cmd", ".bat"}:
        for argument in command[1:]:
            if _SHELL_META & set(argument):
                raise UnsafeCommand(
                    f"{Path(command[0]).name} runs through cmd.exe; the argument {argument[:40]!r} "
                    "has characters that could be misread"
                )


@dataclass(frozen=True)
class ProcessSpec:
    """Everything the runner needs to start one process.

    ``env`` is the complete environment of the child; nothing is inherited implicitly.
    ``timeout`` is mandatory (``None`` means no limit) so that callers decide it explicitly.
    ``grace`` is how long a polite stop request is given before the whole tree is killed.
    ``stdin_file`` is fed to the process as its standard input (otherwise stdin is empty).
    ``low_priority`` makes the process and all it starts yield the processor to everything else
    on the machine: for long work nobody waits on at the keyboard.
    """

    command: Sequence[str]
    cwd: Path
    env: Mapping[str, str]
    timeout: float | None
    grace: float = 5.0
    stdin_file: Path | None = None
    low_priority: bool = False

    def __post_init__(self) -> None:
        check_command_safety(self.command)


@dataclass(frozen=True)
class ProcessIdentity:
    """Opaque identity of a started process, used to find leftovers after a crash.

    ``creation_token`` is the OS process creation time in platform units. It is ``None`` when the
    process could not be inspected, in which case it can never be matched.
    """

    pid: int
    creation_token: int | None


@dataclass(frozen=True)
class ProcessResult:
    outcome: Outcome
    exit_code: int | None
    duration: float
    log_path: Path
    peak_memory: int | None = None  # bytes, whole tree; None when the platform cannot measure
