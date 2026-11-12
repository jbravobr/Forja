"""The C# adapter: ``dotnet format`` for format and for the Roslyn analyzers (lint),
``dotnet build`` and ``dotnet test`` for build and tests, and coverlet's data collector for
coverage (Cobertura).

It only describes commands and reads their output; the core runs them. The project to act on is
the one solution (or, failing that, the one project) at the root; with more than one the answer is
not supported, because choosing for the person would measure something they did not mean. Coverlet
is the project's own dependency (``coverlet.collector``); a project without it gets no report, and
the coverage gate is inconclusive rather than a pass.
"""

import hashlib
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from forja.core.adapters import (
    CONTRACT_VERSION,
    CommandPlan,
    Detection,
    FileClass,
    LanguageCapability,
    NotSupported,
    Operation,
    OperationRequest,
    ParseResult,
    ProgressParser,
    ProjectTemplate,
    ToolInfo,
    ToolOutput,
    Unparseable,
    VersionProbe,
)
from forja.core.adapters.ownership import dotted_names, says
from forja.core.reports import ReportFormat, ReportSpec
from forja.languages.csharp import parsers
from forja.languages.csharp.manifest import declared_dependencies
from forja.languages.csharp.template import template

SOLUTIONS = ("*.sln", "*.slnx")
PROJECTS = ("*.csproj",)
CONFIG_FILES = (
    ".editorconfig",
    "global.json",
    "nuget.config",
    "NuGet.Config",
    "Directory.Build.props",
    "Directory.Build.targets",
    "Directory.Packages.props",
)
# The collector writes ``<directory>/<run id>/coverage.cobertura.xml``: the run id is its own.
COVERAGE_DIRECTORY = ".forja-coverage"
COVERAGE_REPORT = f"{COVERAGE_DIRECTORY}/*/coverage.cobertura.xml"
_DOTNET_VERSION = r"(\d+\.\d+\.\d+\S*)"
# What the .NET tool chain reads from the environment beyond the generic list: the user's profile
# (NuGet cache, first-run state), the SDK location and the settings of the user's installation.
DOTNET_ENVIRONMENT = frozenset(
    {
        "LOCALAPPDATA",
        "APPDATA",
        "USERPROFILE",
        "HOME",
        "ProgramData",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "DOTNET_ROOT",
        "DOTNET_CLI_HOME",
        "NUGET_PACKAGES",
        "NUGET_HTTP_CACHE_PATH",
    }
)
_ENVIRONMENT_SET = {
    "NO_COLOR": "1",
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
    "DOTNET_NOLOGO": "1",
    "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
}
_TEST_DIRECTORIES = ("tests", "test")
_TEST_SUFFIXES = ("Tests", "Test", "Specs")
_FIXTURE_DIRECTORIES = frozenset({"testdata", "fixtures", "testassets"})
_SKIPPED_DIRECTORIES = frozenset({"bin", "obj", ".vs", "packages"})
_LOGGER = ("--logger", "console;verbosity=normal")  # one line per test, with its outcome


def _entry(root: Path) -> str | None:
    """The one solution at the root, or else the one project; ``None`` if not exactly one."""
    for patterns in (SOLUTIONS, PROJECTS):
        found = sorted(path.name for pattern in patterns for path in root.glob(pattern))
        if len(found) == 1:
            return found[0]
        if found:
            return None
    return None


def artifacts_dir(root: Path) -> Path:
    """Where the build of the worktree at ``root`` writes ``obj`` and ``bin``: the user's own
    cache, one directory per worktree so two attempts never share a build."""
    base = os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else None
    cache = Path(base) if base else Path.home() / ".cache"
    key = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return cache / "forja" / "dotnet" / key


class CSharpAdapter:
    contract_version = CONTRACT_VERSION
    language = "csharp"
    dependency_ecosystem = "NuGet"  # the registry of its manifests
    grammar = "csharp"
    # what switches off the analysers in a region or a member, and the coverage of a member
    suppressions = (
        r"#\s*pragma\s+warning\s+disable",
        r"SuppressMessage",
        r"ExcludeFromCodeCoverage",
    )
    capabilities = frozenset({LanguageCapability.COVERAGE, LanguageCapability.STRUCTURAL_METERS})

    def __init__(self, *, dotnet: tuple[str, ...] = ("dotnet",)) -> None:
        self._dotnet = dotnet

    # -- finding and classifying ---------------------------------------------------------------

    def detect(self, root: Path) -> Detection | None:
        manifests = sorted(
            path.name for pattern in (*SOLUTIONS, *PROJECTS) for path in root.glob(pattern)
        )
        if not manifests:
            return None
        probe = VersionProbe(self._plan(root, (*self._dotnet, "--version")), _DOTNET_VERSION)
        return Detection("csharp", (".",), tuple(manifests), probe)

    def owns_test(self, test_id: str, path: str, text: str) -> bool:
        """The ids (``Namespace.Class.Method``) do not carry the file: its class and method are
        written in it."""
        names = dotted_names(test_id)
        return names is not None and all(says(text, name) for name in names)

    def declared_dependencies(self, path: str, text: str) -> frozenset[str] | None:
        """The packages a manifest declares; ``None`` when ``path`` is not one of this
        language's manifests, ``ValueError`` when it is one that cannot be read."""
        return declared_dependencies(path, text)

    def classify(self, path: str) -> FileClass | None:
        parts = path.replace("\\", "/").split("/")
        name, directories = parts[-1], parts[:-1]
        if (
            not name.endswith(".cs")
            or name.endswith(".g.cs")
            or _SKIPPED_DIRECTORIES & set(directories)
        ):
            return None
        lowered = [directory.lower() for directory in directories]
        if _FIXTURE_DIRECTORIES & set(lowered):
            return FileClass.FIXTURE
        stem = name.removesuffix(".cs")
        in_tests = any(
            directory in _TEST_DIRECTORIES or directory.endswith(_TEST_SUFFIXES)
            for directory in directories
        )
        if in_tests or stem.endswith(_TEST_SUFFIXES):
            return FileClass.TEST
        return FileClass.PRODUCTION

    # -- commands ----------------------------------------------------------------------------

    def tool_info(self, operation: Operation) -> ToolInfo | NotSupported:
        dotnet = VersionProbe(self._plan(Path("."), (*self._dotnet, "--version")), _DOTNET_VERSION)
        match operation:
            case Operation.FORMAT | Operation.LINT | Operation.LINT_FIX:
                return ToolInfo("dotnet format", dotnet, "dotnet", dotnet)
            case Operation.BUILD | Operation.TEST | Operation.COVERAGE:
                return ToolInfo("dotnet", dotnet, "dotnet", dotnet)
        return NotSupported(operation.value, "the C# adapter does not offer it")

    def plan(self, operation: Operation, request: OperationRequest) -> CommandPlan | NotSupported:
        entry = _entry(request.root)
        if entry is None:
            return NotSupported(
                operation.value, "the root has no single solution or project to act on"
            )
        dotnet = self._dotnet
        include = ("--include", *request.files) if request.files else ()
        match operation:
            case Operation.FORMAT:
                check = ("--verify-no-changes",) if request.check_only else ()
                argv = (*dotnet, "format", "whitespace", entry, *check, *include)
            case Operation.LINT | Operation.LINT_FIX:
                check = () if operation is Operation.LINT_FIX else ("--verify-no-changes",)
                argv = (*dotnet, "format", "analyzers", entry, *check, *include)
            case Operation.BUILD:
                argv = (*dotnet, "build", entry, "--nologo")
            case Operation.TEST | Operation.COVERAGE if request.tests or request.test_files:
                return NotSupported(operation.value, "filtering the tests is not offered")
            case Operation.TEST:
                argv = (*dotnet, "test", entry, "--nologo", *_LOGGER)
            case Operation.COVERAGE:
                argv = (
                    *dotnet,
                    "test",
                    entry,
                    "--nologo",
                    *_LOGGER,
                    "--collect:XPlat Code Coverage",
                    "--results-directory",
                    COVERAGE_DIRECTORY,
                )
            case _:
                return NotSupported(operation.value, "the C# adapter does not offer it")
        return self._plan(request.root, argv, config_files=CONFIG_FILES)

    def report(self, operation: Operation) -> ReportSpec | NotSupported:
        if operation is Operation.COVERAGE:
            return ReportSpec(ReportFormat.COBERTURA_XML, COVERAGE_REPORT)
        return NotSupported(operation.value, "the C# adapter writes no such report")

    @staticmethod
    def _plan(root: Path, argv: tuple[str, ...], config_files: tuple[str, ...] = ()) -> CommandPlan:
        # Build output goes to one place per worktree outside it: ``obj`` and ``bin`` ignored by
        # git would otherwise show as hidden files in the worktree (the scope gate rejects them).
        env: Mapping[str, str] = {
            **_ENVIRONMENT_SET,
            "UseArtifactsOutput": "true",
            "ArtifactsPath": str(artifacts_dir(root)),
        }
        return CommandPlan(
            argv, root, env_allow=DOTNET_ENVIRONMENT, env_set=env, config_files=config_files
        )

    # -- reading what they print ---------------------------------------------------------------

    def parse(self, operation: Operation, output: ToolOutput) -> ParseResult:
        match operation:
            case Operation.FORMAT:
                return parsers.parse_format(output)
            case Operation.LINT | Operation.LINT_FIX:
                return parsers.parse_lint(output)
            case Operation.BUILD:
                return parsers.parse_build(output)
            case Operation.TEST:
                return parsers.parse_tests(output)
        return Unparseable(f"the C# adapter does not read {operation.value} output")

    def progress_parser(self, operation: Operation) -> ProgressParser | None:
        return None

    def template(self, name: str) -> ProjectTemplate:
        return template(name)
