"""Types shared by language adapters and executor adapters (contract 4, adapter division).

An adapter never runs a process itself. It describes the command (``CommandPlan``) and reads the
result (``parse``); the core runs the command through the process runner, which owns the Job
Object, the isolation level and the tool-run record. Adapters therefore stay pure and testable.
"""

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from forja.core.artifacts.llm_output import check_relative_path
from forja.core.events.models import canonical_json
from forja.core.evidence.models import Target
from forja.core.isolation import filter_environment
from forja.core.process import ProcessSpec

CONTRACT_VERSION = 1
# The seed the Forja gives the property-testing libraries that take one from the command line or
# the environment, so every repeated run of the tests draws the same cases.
PROPERTY_SEED = 20261106


@dataclass(frozen=True)
class NotSupported:
    """What an adapter answers for an operation it does not offer. Never an exception."""

    what: str
    reason: str = ""


@dataclass(frozen=True)
class CommandPlan:
    """A command to run, fully described.

    ``env_allow`` names variables the tool needs from the host environment beyond the generic
    list (a CLI's own credential, say). ``env_set`` are values the adapter sets itself (such as
    ``NO_COLOR``). ``config_files`` are the repository-relative files that change what the command
    does; their content is part of the configuration hash used as cache key.
    """

    argv: tuple[str, ...]
    cwd: Path
    env_allow: frozenset[str] = frozenset()
    env_set: Mapping[str, str] = field(default_factory=dict)
    stdin_file: Path | None = None
    config_files: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.argv or not self.argv[0]:
            raise ValueError("a command plan needs a program")
        if not all(isinstance(part, str) for part in self.argv):
            raise ValueError("every part of argv must be a string")
        for name in (*self.env_allow, *self.env_set):
            if not name or "=" in name:
                raise ValueError(f"invalid environment variable name {name!r}")
        for path in self.config_files:
            check_relative_path(path)


def build_spec(
    plan: CommandPlan, host_env: Mapping[str, str], *, timeout: float | None, grace: float = 5.0
) -> ProcessSpec:
    """The process spec for a plan: filtered environment, no implicit inheritance."""
    environment = filter_environment(host_env, adapter_allow=plan.env_allow, extra=plan.env_set)
    return ProcessSpec(
        plan.argv, plan.cwd, environment.values, timeout, grace, stdin_file=plan.stdin_file
    )


def compute_config_hash(plan: CommandPlan, root: Path) -> str:
    """Hash of everything that defines how a tool behaves, for the measurement cache key.

    The command line, the names of the host variables it may read (never their values, which
    could be secrets), the values the adapter sets, and the content of the declared configuration
    files. A missing file hashes as ``absent``, so creating it changes the hash.
    """
    files: dict[str, str] = {}
    for relative in plan.config_files:
        path = root / relative
        files[relative] = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "absent"
        )
    document = {
        "argv": list(plan.argv),
        "env_allow": sorted(plan.env_allow),
        "env_set": dict(sorted(plan.env_set.items())),
        "files": files,
    }
    return hashlib.sha256(canonical_json(document)).hexdigest()


@dataclass(frozen=True)
class VersionProbe:
    """How to learn a version: a command and a regular expression with one capture group."""

    plan: CommandPlan
    pattern: str

    def __post_init__(self) -> None:
        if re.compile(self.pattern).groups != 1:
            raise ValueError("the version pattern needs exactly one capture group")


def extract_version(probe: VersionProbe, lines: Sequence[str]) -> str | None:
    for line in lines:
        match = re.search(probe.pattern, line)
        if match:
            return match.group(1)
    return None


@dataclass(frozen=True)
class ToolInfo:
    """Names and version probes of the tool and of the toolchain behind it."""

    tool: str
    tool_probe: VersionProbe
    toolchain: str
    toolchain_probe: VersionProbe


@dataclass(frozen=True)
class ToolOutput:
    """What a finished command left behind, for ``parse``."""

    exit_code: int | None
    log_path: Path
    root: Path | None = None  # where the command ran, for a tool that prints absolute paths


_WORSE = {"skipped": 0, "passed": 1, "failed": 2, "error": 2}


def keep_worst(outcomes: dict[str, str], test: str, outcome: str) -> None:
    """Record ``outcome`` for ``test`` unless a worse one is already there. A runner may name
    several cases alike (one per set of arguments): if one of them failed the test failed, and
    the last to be read must not pass for all."""
    known = outcomes.get(test)
    if known is None or _WORSE.get(outcome, 1) > _WORSE.get(known, 1):
        outcomes[test] = outcome


def read_output_lines(log_path: Path) -> list[str]:
    """Lines of the raw log without the ``[out] ``/``[err] `` channel prefix. Never raises."""
    try:
        raw = log_path.read_bytes()
    except OSError:
        return []
    lines = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        lines.append(line[6:] if line.startswith(("[out] ", "[err] ")) else line)
    return lines


@dataclass(frozen=True)
class EvidenceDraft:
    """A measurement without id or context: the core adds who ran it, when, and on what."""

    measurement: str
    target: Target
    value: bool | int | float | str
    unit: str


@dataclass(frozen=True)
class Parsed:
    drafts: tuple[EvidenceDraft, ...]


@dataclass(frozen=True)
class Unparseable:
    """The output could not be understood; the gate that needed it is inconclusive."""

    reason: str


ParseResult = Parsed | Unparseable


@dataclass(frozen=True)
class Progress:
    """Real progress of a tool run; only produced when the tool reports a measured count."""

    done: int
    total: int
    label: str = ""


class ProgressParser(Protocol):
    def feed(self, line: str) -> Progress | None:
        """Look at one output line. Never raises."""
        ...
