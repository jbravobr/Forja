"""The executor adapter interface: what the core needs to know about an agent CLI."""

import os
import re
import string
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from forja.core.adapters.common import CommandPlan
from forja.core.artifacts.models import Producer

AGENT_DIR = ".forja-agent"  # reserved inside the working copy; excluded from manifests and scope
DIARY_FILE = "diary.md"  # inside ``AGENT_DIR``: every earlier attempt of the stage, whole


# -- what the agent does, as the interface shows it --------------------------------------------


class ErrorKind(StrEnum):
    TRANSIENT = "transient"  # timeout, rate limit, 5xx: wait and try again
    PERSISTENT = "persistent"  # no network for minutes: pause the run
    BLOCKING = "blocking"  # expired authentication, quota gone: stop safely


@dataclass(frozen=True)
class TextActivity:
    text: str


@dataclass(frozen=True)
class FileReadActivity:
    path: str


@dataclass(frozen=True)
class FileEditActivity:
    path: str
    added: int
    removed: int


@dataclass(frozen=True)
class CommandActivity:
    command: str
    exit_code: int | None = None


@dataclass(frozen=True)
class UsageActivity:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class ErrorActivity:
    message: str
    kind: ErrorKind


AgentActivity = (
    TextActivity
    | FileReadActivity
    | FileEditActivity
    | CommandActivity
    | UsageActivity
    | ErrorActivity
)


# -- invoking the agent ------------------------------------------------------------------------


@dataclass(frozen=True)
class RolePermissions:
    """What a role may do. Writing is also policed by the scope gate; this restricts the CLI."""

    can_edit_files: bool
    allowed_commands: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentRequest:
    """One call to an agent. The package is a file; it never travels as an argument."""

    role: Producer
    package: Path
    workdir: Path
    permissions: RolePermissions
    model: str = ""  # empty: the model the user configured in the CLI

    def __post_init__(self) -> None:
        if self.role is Producer.FORJA:
            raise ValueError("Forja itself is not an agent role")
        if self.model.startswith("-"):
            raise ValueError("a model name cannot start with a dash")


def model_args(request: AgentRequest) -> tuple[str, ...]:
    """The ``--model`` flag every supported CLI takes; nothing when the user's own is wanted."""
    return ("--model", request.model) if request.model else ()


@dataclass(frozen=True)
class PermissionSetup:
    """Files (relative to ``AGENT_DIR``) and extra flags that apply a role's permissions."""

    files: Mapping[str, bytes]
    extra_args: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in self.files:
            parts = name.replace("\\", "/").split("/")
            if not name or name.startswith(("/", "\\")) or ".." in parts or ":" in name:
                raise ValueError(f"permission file {name!r} must stay inside {AGENT_DIR}")


@runtime_checkable
class InstructionChannel(Protocol):
    """An adapter whose CLI takes standing instructions outside the package and the diff.

    The Forja asks for it only to repeat text it already put in the package (the voice), so a CLI
    without it loses nothing but the repetition.
    """

    def instruction_setup(self, text: str) -> PermissionSetup: ...


# -- finding the CLI ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscoverySpec:
    """Where to look for the CLI and how to ask its version."""

    executable_names: tuple[str, ...]
    extra_locations: tuple[str, ...] = ()  # directories; environment variables are expanded
    version_args: tuple[str, ...] = ("--version",)
    version_pattern: str = r"(\d+(?:\.\d+)+)"
    min_version: str = "0"

    def __post_init__(self) -> None:
        if not self.executable_names:
            raise ValueError("a CLI needs at least one executable name")
        if re.compile(self.version_pattern).groups != 1:
            raise ValueError("the version pattern needs exactly one capture group")


@dataclass(frozen=True)
class ProbeTask:
    """A trivial job that proves a CLI works in the environment where it will run."""

    prompt: str
    expected_file: str
    expected_content: str
    expected_event_kinds: frozenset[str]


class ExecutorAdapter(Protocol):
    """Everything the core asks of an agent CLI. Pure: it plans and parses, the core runs.

    ``enforces_permissions`` is False when the CLI offers no way to restrict a role; the core
    then records that limitation for the PR.
    """

    @property
    def contract_version(self) -> int: ...

    @property
    def name(self) -> str: ...

    @property
    def discovery(self) -> DiscoverySpec: ...

    @property
    def env_allow(self) -> frozenset[str]: ...

    @property
    def enforces_permissions(self) -> bool: ...

    @property
    def probe(self) -> ProbeTask: ...

    def permission_setup(self, role: Producer, permissions: RolePermissions) -> PermissionSetup: ...

    def plan_invocation(
        self, request: AgentRequest, setup: PermissionSetup, executable: Path
    ) -> CommandPlan: ...

    def parse_event(self, line: str) -> AgentActivity | None: ...


# -- versions ----------------------------------------------------------------------------------


def version_tuple(text: str) -> tuple[int, ...]:
    """``'2.1.10'`` becomes ``(2, 1, 10)``. Anything else gives an empty tuple."""
    match = re.search(r"\d+(?:\.\d+)*", text)
    return tuple(int(part) for part in match.group(0).split(".")) if match else ()


def meets_minimum(found: str, minimum: str) -> bool:
    """Whether ``found`` is at least ``minimum``; ``2.0`` and ``2.0.0`` are the same version.

    A version that cannot be read never meets a minimum.
    """
    have, need = version_tuple(found), version_tuple(minimum)
    if not have:
        return False
    width = max(len(have), len(need))

    def padded(parts: tuple[int, ...]) -> tuple[int, ...]:
        return parts + (0,) * (width - len(parts))

    return padded(have) >= padded(need)


# -- choosing the executable -------------------------------------------------------------------


class Source(StrEnum):
    EXPLICIT = "explicit"  # execution file or flag
    ENVIRONMENT = "environment"  # FORJA_EXECUTOR_<NAME>
    USER_CONFIG = "user_config"
    DISCOVERY = "discovery"


@dataclass(frozen=True)
class ExecutorChoice:
    path: Path
    source: Source


class ExecutorNotFound(Exception):
    """A path that was given by name does not exist. No silent fallback to something else."""

    def __init__(self, source: Source, path: Path) -> None:
        self.source = source
        self.path = path
        super().__init__(f"the executor given by {source.value} does not exist: {path}")


def environment_variable(adapter_name: str) -> str:
    return "FORJA_EXECUTOR_" + re.sub(r"[^A-Za-z0-9]", "_", adapter_name).upper()


def _is_executable_file(path: Path) -> bool:
    return path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))


def _search(discovery: DiscoverySpec, environ: Mapping[str, str]) -> list[Path]:
    directories = [d for d in environ.get("PATH", "").split(os.pathsep) if d]
    for location in discovery.extra_locations:
        expanded = string.Template(location).safe_substitute(environ)
        directories.append(os.path.expandvars(expanded) if os.name == "nt" else expanded)
    extensions = [""]
    if os.name == "nt":
        extensions += [e.lower() for e in environ.get("PATHEXT", ".EXE;.CMD;.BAT").split(";") if e]
    found: dict[str, Path] = {}
    for directory in directories:
        for name in discovery.executable_names:
            for extension in extensions:
                candidate = Path(directory) / (name + extension)
                if _is_executable_file(candidate):
                    found.setdefault(os.path.normcase(os.path.realpath(candidate)), candidate)
    return list(found.values())


def find_executors(
    adapter: ExecutorAdapter,
    *,
    explicit: Path | None = None,
    environ: Mapping[str, str] | None = None,
    user_config: Mapping[str, str] | None = None,
) -> list[ExecutorChoice]:
    """Candidates in order of precedence: explicit, environment variable, user config, discovery.

    A path given explicitly, by variable or by user config must exist; if it does not, that is an
    error, never a fallback. Without any of them, discovery returns every installation it finds
    (the user picks one when there are several).
    """
    env = os.environ if environ is None else environ
    config = user_config or {}
    given: list[tuple[Source, str | os.PathLike[str] | None]] = [
        (Source.EXPLICIT, explicit),
        (Source.ENVIRONMENT, env.get(environment_variable(adapter.name))),
        (Source.USER_CONFIG, config.get(adapter.name)),
    ]
    for source, value in given:
        if value:
            path = Path(value)
            if not path.is_file():
                raise ExecutorNotFound(source, path)
            return [ExecutorChoice(path, source)]
    return [ExecutorChoice(path, Source.DISCOVERY) for path in _search(adapter.discovery, env)]


# -- the liveness proof ------------------------------------------------------------------------


def check_probe(task: ProbeTask, workdir: Path, activities: Sequence[AgentActivity]) -> list[str]:
    """What the proof of life found wrong; an empty list means the CLI passed."""
    problems = []
    target = workdir / task.expected_file
    if not target.is_file():
        problems.append(f"the CLI did not create {task.expected_file}")
    elif target.read_text(encoding="utf-8", errors="replace").strip() != task.expected_content:
        problems.append(f"{task.expected_file} does not have the expected content")
    seen = {type(activity).__name__ for activity in activities}
    for kind in sorted(task.expected_event_kinds - seen):
        problems.append(f"no {kind} event was readable from the output")
    errors = [a for a in activities if isinstance(a, ErrorActivity)]
    problems.extend(f"the CLI reported an error: {error.message}" for error in errors)
    return problems


# -- a CLI described by a command template -----------------------------------------------------


_PLACEHOLDERS = frozenset({"package", "cwd", "role", "executable"})


@dataclass(frozen=True)
class TemplateExecutorAdapter:
    """A CLI described only by data: an argv template and what it needs from the environment.

    Placeholders: ``{package}`` (the package file), ``{cwd}``, ``{role}`` and ``{executable}``.
    With ``package_via_stdin`` the package is piped to the CLI instead. Every output line is shown
    as text, and no role permission can be enforced, which the core records.
    """

    name: str
    discovery: DiscoverySpec
    argv_template: tuple[str, ...]
    probe: ProbeTask
    env_allow: frozenset[str] = frozenset()
    package_via_stdin: bool = False
    contract_version: int = 1
    enforces_permissions: bool = False

    def __post_init__(self) -> None:
        if not self.argv_template:
            raise ValueError("the command template is empty")
        formatter = string.Formatter()
        used: set[str] = set()
        for part in self.argv_template:
            for _, field_name, _, _ in formatter.parse(part):
                if field_name is not None:
                    used.add(field_name)
        unknown = used - _PLACEHOLDERS
        if unknown:
            raise ValueError(f"unknown placeholders in the template: {sorted(unknown)}")
        if not self.package_via_stdin and "package" not in used:
            raise ValueError("the template must deliver the package: use {package} or stdin")

    def permission_setup(self, role: Producer, permissions: RolePermissions) -> PermissionSetup:
        return PermissionSetup({})

    def plan_invocation(
        self, request: AgentRequest, setup: PermissionSetup, executable: Path
    ) -> CommandPlan:
        values = {
            "package": str(request.package),
            "cwd": str(request.workdir),
            "role": request.role.value,
            "executable": str(executable),
        }
        argv = tuple(part.format(**values) for part in self.argv_template)
        return CommandPlan(
            (*argv, *setup.extra_args),
            request.workdir,
            env_allow=self.env_allow,
            stdin_file=request.package if self.package_via_stdin else None,
        )

    def parse_event(self, line: str) -> AgentActivity | None:
        return TextActivity(line) if line.strip() else None


GENERIC_ENVIRONMENT = frozenset(
    {
        "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT",
        "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE",
    }
)  # fmt: skip

GENERIC_PROBE = ProbeTask(
    prompt=(
        "Create a file named probe.txt in the working directory root"
        " (not inside .forja-agent), containing exactly: forja-ok"
    ),
    expected_file="probe.txt",
    expected_content="forja-ok",
    expected_event_kinds=frozenset(),  # a CLI described by a template may print nothing readable
)


@dataclass(frozen=True)
class CommandTemplate:
    """What a user declares about a CLI the Forja does not know: how to call it and where it is.

    ``argv`` takes the placeholders of ``TemplateExecutorAdapter``. ``env_allow`` adds to the
    variables every generic CLI gets (proxy, certificates and what a Windows process needs).
    """

    argv: tuple[str, ...]
    via_stdin: bool = False
    executables: tuple[str, ...] = ()  # empty: the name of the CLI
    version_args: tuple[str, ...] = ("--version",)
    env_allow: tuple[str, ...] = ()


def template_adapter(name: str, template: CommandTemplate) -> TemplateExecutorAdapter:
    return TemplateExecutorAdapter(
        name=name,
        discovery=DiscoverySpec(
            executable_names=template.executables or (name,), version_args=template.version_args
        ),
        argv_template=template.argv,
        probe=GENERIC_PROBE,
        env_allow=GENERIC_ENVIRONMENT | frozenset(template.env_allow),
        package_via_stdin=template.via_stdin,
    )


def locate_in_path(name: str, path: str | None = None) -> Path | None:
    """First executable called ``name`` on the PATH, as the platform would find it."""
    raw = os.environ.get("PATH", "") if path is None else path
    here = os.path.normcase(os.path.realpath(os.getcwd()))
    suffixes = [""]
    if sys.platform == "win32":
        wanted = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").lower().split(";")
        suffixes = [""] if name.lower().endswith(tuple(wanted)) else wanted
    for directory in raw.split(os.pathsep):
        # An empty or relative entry means "the current directory", which in a repository the
        # Forja was pointed at is where an attacker's `git.exe` or `claude.exe` would be.
        if not directory or not os.path.isabs(directory):
            continue
        if os.path.normcase(os.path.realpath(directory)) == here:
            continue
        for suffix in suffixes:
            candidate = os.path.join(directory, name + suffix)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return Path(candidate)
    return None
