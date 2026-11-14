"""The language adapter interface: what the core needs to know about a language ecosystem."""

import dataclasses
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from forja.core.adapters.common import (
    CommandPlan,
    NotSupported,
    ParseResult,
    ProgressParser,
    ToolInfo,
    ToolOutput,
    VersionProbe,
)
from forja.core.policy.registry import LANGUAGES


class Operation(StrEnum):
    FORMAT = "format"
    LINT = "lint"
    LINT_FIX = "lint_fix"
    TYPES = "types"
    BUILD = "build"
    TEST = "test"
    COVERAGE = "coverage"
    MUTATION = "mutation"
    E2E = "e2e"


# Every adapter must offer these; the rest are optional and answer ``NotSupported``.
REQUIRED_OPERATIONS = frozenset({Operation.BUILD, Operation.TEST})


class LanguageCapability(StrEnum):
    TEST_FILTER_BY_FILE = "test_filter_by_file"
    TEST_FILTER_BY_TEST = "test_filter_by_test"
    COVERAGE = "coverage"
    MUTATION = "mutation"
    MUTATION_INCREMENTAL = "mutation_incremental"
    E2E = "e2e"
    PROGRESS_REALTIME = "progress_realtime"
    STRUCTURAL_METERS = "structural_meters"
    REQUIRES_WINDOWS = "requires_windows"  # the project only builds on Windows (reduced isolation)


# A capability that implies an operation can be planned.
CAPABILITY_OPERATION: Mapping[LanguageCapability, Operation] = {
    LanguageCapability.COVERAGE: Operation.COVERAGE,
    LanguageCapability.MUTATION: Operation.MUTATION,
    LanguageCapability.E2E: Operation.E2E,
}


class FileClass(StrEnum):
    PRODUCTION = "production"
    TEST = "test"
    FIXTURE = "fixture"
    DOCUMENTATION = "documentation"
    OTHER = "other"  # configuration, lock files, CI: nothing claims them


DOCUMENT_SUFFIXES = (".md", ".rst")
# Outside ``docs/`` only these stems are documentation: any other Markdown file may be read by a
# tool as instructions for the agent, and the role that could write it would be giving orders to
# the roles that come after it.
TOP_LEVEL_DOCUMENTS = ("readme", "changelog", "contributing")
# The families of files that tell an agent what to do, wherever they are (``CLAUDE.local.md``,
# ``AGENTS.override.md``, ``copilot-instructions.md``...).
AGENT_INSTRUCTIONS = re.compile(
    r"^(claude|agents?|gemini|grok|copilot|conventions|windsurf)([._-].*)?\.(md|rst)$"
)


def is_documentation(path: str) -> bool:
    """A Markdown or reStructuredText file under ``docs/`` or a README, a changelog or a guide to
    contributing: no language owns these, so the core does. Never a file for agents, nor anything
    in a hidden directory."""
    parts = path.replace("\\", "/").split("/")
    name = parts[-1].lower()
    if not name.endswith(DOCUMENT_SUFFIXES):
        return False
    if AGENT_INSTRUCTIONS.match(name) or any(part.startswith(".") for part in parts):
        return False
    return parts[0].lower() == "docs" or name.startswith(TOP_LEVEL_DOCUMENTS)


@dataclass(frozen=True)
class OperationRequest:
    """What to run. Filters and ``incremental`` need the matching capability, or the answer is
    ``NotSupported``: silently running everything would hide that the filter was ignored."""

    root: Path
    files: tuple[str, ...] = ()  # limit format and lint to these repository-relative files
    test_files: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    incremental: bool = False
    check_only: bool = False  # format: verify without rewriting
    # tests: the same copy is tested again and again, and nobody reads a report of the run. A
    # hint, not a filter: an adapter with nothing to save by it plans the same command
    rerun: bool = False


# What a new project may be called: lower-case words joined by hyphens. Every language derives its
# own identifier from it (a module, a package, a namespace) without having to escape anything.
PROJECT_NAME = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")
PROJECT_NAME_LIMIT = 40
GITATTRIBUTES = "* text=auto eol=lf\n"


def valid_project_name(name: str) -> bool:
    return len(name) <= PROJECT_NAME_LIMIT and PROJECT_NAME.fullmatch(name) is not None


def project_words(name: str) -> tuple[str, ...]:
    """The words of a project name: ``my-tool`` is ``("my", "tool")``."""
    return tuple(name.split("-"))


@dataclass(frozen=True)
class ProjectTemplate:
    """What a language needs for an empty directory to become a project that builds and passes.

    ``files`` maps repository-relative paths to their text. ``setup`` is what the environment has to
    do before the build can run (an installation that fetches packages), one command per entry, run
    in the project's root: the tools cannot fetch them by themselves.
    """

    files: Mapping[str, str]
    setup: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class Detection:
    """A language found in the repository, and where."""

    language: str
    roots: tuple[str, ...]  # repository-relative directories holding a manifest; "." for the top
    manifests: tuple[str, ...]
    toolchain_probe: VersionProbe | None = None


class LanguageAdapter(Protocol):
    """Everything the core asks of a language. Pure: reads files, never runs processes.

    ``language`` is one of the six supported languages and is also the key of the thresholds in
    the policy. ``grammar`` names the tree-sitter grammar for the structural meters, or ``None``.
    ``classify`` answers ``None`` for files that are not this adapter's business.
    """

    @property
    def contract_version(self) -> int: ...

    @property
    def language(self) -> str: ...

    @property
    def capabilities(self) -> frozenset[LanguageCapability]: ...

    @property
    def grammar(self) -> str | None: ...

    def detect(self, root: Path) -> Detection | None: ...

    def classify(self, path: str) -> FileClass | None: ...

    def tool_info(self, operation: Operation) -> ToolInfo | NotSupported: ...

    def plan(
        self, operation: Operation, request: OperationRequest
    ) -> CommandPlan | NotSupported: ...

    def parse(self, operation: Operation, output: ToolOutput) -> ParseResult: ...

    def progress_parser(self, operation: Operation) -> ProgressParser | None: ...

    def template(self, name: str) -> ProjectTemplate:
        """The files of a new project called ``name`` (a valid project name): its manifest, code
        and tests, one test that passes, and the ignore and attribute files."""
        ...


def plan_with_override(
    adapter: LanguageAdapter,
    operation: Operation,
    request: OperationRequest,
    overrides: Mapping[str, Mapping[str, tuple[str, ...]]],
) -> CommandPlan | NotSupported:
    """The adapter's plan, with the command line replaced by the repository's own if it has one.

    The override only applies where the adapter supports the operation, so what an adapter claims
    stays true. Everything else in the plan (directory, environment, configuration files) is kept,
    and the adapter still reads the output.
    """
    plan = adapter.plan(operation, request)
    if isinstance(plan, NotSupported):
        return plan
    replacement = overrides.get(adapter.language, {}).get(operation.value)
    if replacement is None:
        return plan
    # "python -m pytest ..." in a repository's file means the repository's interpreter, which the
    # adapter chose (its virtual environment), not whichever "python" the system finds first
    own = Path(plan.argv[0]).stem.lower()
    if own == "python" and replacement[0].lower() in ("python", "python3"):
        replacement = (plan.argv[0], *replacement[1:])
    return dataclasses.replace(plan, argv=replacement)


def valid_language(language: str) -> bool:
    return language in LANGUAGES
