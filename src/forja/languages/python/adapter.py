"""The Python adapter: ruff for format and lint, compileall for build, pytest for tests and
coverage.py (through a small driver) for coverage.

It only describes commands and reads their output; the core runs them. The program for each tool
is a parameter, because ruff comes from Forja's own environment while pytest must run in the
repository's, with its dependencies.
"""

import os
import sys
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
from forja.core.adapters.common import PROPERTY_SEED
from forja.core.reports import ReportFormat, ReportSpec
from forja.languages.python import mypy_config, parsers
from forja.languages.python.manifest import (
    declared_dependencies,
    declared_requirements,
    declares_hypothesis,
    only_dependencies_changed,
)
from forja.languages.python.template import template

MANIFEST = "pyproject.toml"
CONFIG_FILES = (MANIFEST, "ruff.toml", ".ruff.toml", "setup.cfg", "pytest.ini", "tox.ini")
# Directories that hold someone else's code or Forja's own leftovers.
SKIPPED_DIRECTORIES = (
    r"(^|[\\/])(\.git|\.venv|venv|node_modules|__pycache__|\.tox|build|dist)([\\/]|$)"
)
_FIXTURE_DIRECTORIES = frozenset({"fixtures", "testdata"})
_TEST_DIRECTORIES = frozenset({"tests", "test"})
_SOURCE_SUFFIXES = (".py", ".pyi")
_RUFF_VERSION = r"ruff (\S+)"
_MYPY_VERSION = r"mypy (\S+)"
_PYTHON_VERSION = r"Python (\S+)"
_COVERAGE_VERSION = r"Coverage\.py, version (\S+)"
# Where the coverage report lands, relative to the repository (``target/`` is Forja's own
# ignored directory; ``scripts/mutate.py`` already writes its coverage next to it)
COVERAGE_REPORT = "target/coverage/python/coverage.json"
COVERAGE_DRIVER = Path(__file__).with_name("coverage_driver.py")
_PYTEST_OPTIONS = ("-p", "no:cacheprovider", "-q", "--color=no")
_PYTEST_VERSION = r"pytest (\S+)"


def _seed(root: Path) -> tuple[str, ...]:
    return (f"--hypothesis-seed={PROPERTY_SEED}",) if declares_hypothesis(root) else ()


def default_cache_dir() -> Path:
    """Where compiled files go: the user's own cache, never a directory others can write to."""
    base = os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else None
    root = Path(base) if base else Path.home() / ".cache"
    return root / "forja" / "pycache"


class PythonAdapter:
    contract_version = CONTRACT_VERSION
    language = "python"
    dependency_ecosystem = "PyPI"  # the registry of its manifests
    grammar = "python"
    test_root = "tests"  # the directory the test author writes in unless the policy says otherwise
    capabilities = frozenset(
        {
            LanguageCapability.TEST_FILTER_BY_FILE,
            LanguageCapability.COVERAGE,
            LanguageCapability.STRUCTURAL_METERS,
        }
    )

    def __init__(
        self,
        *,
        python: tuple[str, ...] = (sys.executable,),
        ruff: tuple[str, ...] = (sys.executable, "-m", "ruff"),
        mypy: tuple[str, ...] = (sys.executable, "-m", "mypy"),
        cache_dir: Path | None = None,
    ) -> None:
        self._python = python
        self._ruff = ruff
        self._mypy = mypy
        self._cache = str(cache_dir or default_cache_dir())

    # -- finding and classifying ---------------------------------------------------------------

    def detect(self, root: Path) -> Detection | None:
        if not (root / MANIFEST).is_file():
            return None
        probe = VersionProbe(self._plan(root, (*self._python, "--version")), _PYTHON_VERSION)
        return Detection("python", (".",), (MANIFEST,), probe)

    def declared_dependencies(self, path: str, text: str) -> frozenset[str] | None:
        """The packages a manifest declares; ``None`` when ``path`` is not one of this
        language's manifests, ``ValueError`` when it is one that cannot be read."""
        return declared_dependencies(path, text)

    def declared_requirements(self, path: str, text: str) -> frozenset[str]:
        """The whole plain requirements a manifest declares, for the installer."""
        return declared_requirements(path, text)

    def only_dependencies_changed(self, path: str, before: str, after: str) -> bool | None:
        """Whether an edit of a manifest of this language touches only the dependencies it
        declares, each added one a plain registry requirement; ``None`` for any other file."""
        return only_dependencies_changed(path, before, after)

    def unmeasurable(self, operation: Operation, root: Path) -> str | None:
        """Why ``operation`` cannot measure the project at ``root`` as the project asks to be
        measured, or ``None`` when it can. The types of a project whose mypy settings load
        plugins are one such case: without them mypy reports errors the project does not have."""
        if operation is not Operation.TYPES:
            return None
        settings = mypy_config.read(root)
        if settings is None:
            return None
        if settings.plugins:
            return (
                f"the mypy settings in {settings.source} load plugins, which are code of the "
                "repository the Forja does not run"
            )
        if settings.text(root) is None:
            return (
                f"the mypy settings in {settings.source} have a name or a value the Forja "
                "cannot copy without the risk of adding a setting of its own"
            )
        return None

    def classify(self, path: str) -> FileClass | None:
        parts = path.replace("\\", "/").split("/")
        name, directories = parts[-1], set(parts[:-1])
        if not name.endswith(_SOURCE_SUFFIXES):
            return None
        if directories & _TEST_DIRECTORIES and directories & _FIXTURE_DIRECTORIES:
            return FileClass.FIXTURE
        if (
            directories & _TEST_DIRECTORIES
            or name.startswith("test_")
            or name.endswith(("_test.py", "_test.pyi"))
            or name == "conftest.py"
        ):
            return FileClass.TEST
        return FileClass.PRODUCTION

    # -- commands ----------------------------------------------------------------------------

    def tool_info(self, operation: Operation) -> ToolInfo | NotSupported:
        here = Path(".")
        python = VersionProbe(self._plan(here, (*self._python, "--version")), _PYTHON_VERSION)
        match operation:
            case Operation.FORMAT | Operation.LINT | Operation.LINT_FIX:
                ruff = VersionProbe(self._plan(here, (*self._ruff, "--version")), _RUFF_VERSION)
                return ToolInfo("ruff", ruff, "python", python)
            case Operation.TYPES:
                mypy = VersionProbe(self._plan(here, (*self._mypy, "--version")), _MYPY_VERSION)
                return ToolInfo("mypy", mypy, "python", python)
            case Operation.BUILD:
                return ToolInfo("compileall", python, "python", python)
            case Operation.TEST:
                pytest = VersionProbe(
                    self._plan(here, (*self._python, "-m", "pytest", "--version")), _PYTEST_VERSION
                )
                return ToolInfo("pytest", pytest, "python", python)
            case Operation.COVERAGE:
                coverage = VersionProbe(
                    self._plan(here, (*self._python, "-m", "coverage", "--version")),
                    _COVERAGE_VERSION,
                )
                return ToolInfo("coverage.py", coverage, "python", python)
        return NotSupported(operation.value, "the Python adapter does not offer it")

    def plan(self, operation: Operation, request: OperationRequest) -> CommandPlan | NotSupported:
        targets = request.files or (".",)
        match operation:
            case Operation.FORMAT:
                check = ("--check",) if request.check_only else ()
                argv = (
                    *self._ruff,
                    "format",
                    *check,
                    "--no-cache",
                    "--force-exclude",
                    "--",
                    *targets,
                )
            case Operation.LINT:
                argv = (
                    *self._ruff,
                    "check",
                    "--ignore-noqa",
                    *self._ruff_options(),
                    "--",
                    *targets,
                )
            case Operation.LINT_FIX:
                argv = (
                    *self._ruff,
                    "check",
                    "--fix",
                    "--exit-zero",
                    *self._ruff_options(),
                    "--",
                    *targets,
                )
            case Operation.TYPES:
                # mypy reads a copy of the repository's settings, without the plugins: a plugin
                # would run code the attempt may have written. No settings, mypy's defaults.
                settings = mypy_config.read(request.root)
                # settings with no copy to trust are not read: ``unmeasurable`` says so
                copy = None if settings is None else settings.write(request.root, Path(self._cache))
                config = "" if copy is None else str(copy)
                argv = (
                    *self._mypy,
                    *("--python-executable", self._python[0]),
                    *(f"--config-file={config}", "--follow-imports=silent"),
                    *("--output", "json", "--cache-dir", os.devnull),
                    "--disable-error-code=import-not-found",
                    "--disable-error-code=import-untyped",
                    "--",
                    *targets,
                )
                read = () if settings is None or copy is None else (settings.source,)
                return self._plan(request.root, argv, config_files=read)
            case Operation.BUILD:
                argv = (
                    *self._python,
                    "-m",
                    "compileall",
                    "-q",
                    "-x",
                    SKIPPED_DIRECTORIES,
                    "--",
                    *targets,
                )
            case Operation.TEST if request.tests:
                return NotSupported("test", "filtering by test name is not offered")
            case Operation.TEST:
                argv = (
                    *self._python,
                    *("-m", "pytest", "-p", "no:cacheprovider", "-q", "-rA", "--color=no"),
                    *_seed(request.root),
                    *(("--", *request.test_files) if request.test_files else ()),
                )
            case Operation.COVERAGE if request.tests:
                return NotSupported("coverage", "filtering by test name is not offered")
            case Operation.COVERAGE:
                argv = (
                    *self._python,
                    str(COVERAGE_DRIVER),
                    *("--report", COVERAGE_REPORT, "--"),
                    *_PYTEST_OPTIONS,
                    *_seed(request.root),
                    *(("--", *request.test_files) if request.test_files else ()),
                )
            case _:
                return NotSupported(operation.value, "the Python adapter does not offer it")
        return self._plan(
            request.root,
            argv,
            config_files=CONFIG_FILES,
            source_path=operation in (Operation.TEST, Operation.COVERAGE),
        )

    def report(self, operation: Operation) -> ReportSpec | NotSupported:
        if operation is Operation.COVERAGE:
            return ReportSpec(ReportFormat.COVERAGE_PY_JSON, COVERAGE_REPORT)
        return NotSupported(operation.value, "the Python adapter writes no such report")

    @staticmethod
    def _ruff_options() -> tuple[str, ...]:
        return ("--output-format=concise", "--no-cache", "--force-exclude")

    def _plan(
        self,
        root: Path,
        argv: tuple[str, ...],
        config_files: tuple[str, ...] = (),
        source_path: bool = False,
    ) -> CommandPlan:
        # Compiled files go to one private place outside the worktree: nothing lands in the diff.
        env = {"NO_COLOR": "1", "PYTHONPYCACHEPREFIX": self._cache}
        # An editable install points at the original checkout; tests of a copy must import the copy.
        if source_path and (root / "src").is_dir():
            env["PYTHONPATH"] = str(root / "src")
        return CommandPlan(
            argv,
            root,
            env_set=env,
            config_files=config_files,
        )

    # -- reading what they print ---------------------------------------------------------------

    def parse(self, operation: Operation, output: ToolOutput) -> ParseResult:
        match operation:
            case Operation.FORMAT:
                return parsers.parse_format(output)
            case Operation.LINT | Operation.LINT_FIX:
                return parsers.parse_lint(output)
            case Operation.TYPES:
                return parsers.parse_types(output)
            case Operation.BUILD:
                return parsers.parse_build(output)
            case Operation.TEST:
                return parsers.parse_tests(output)
        return Unparseable(f"the Python adapter does not read {operation.value} output")

    def progress_parser(self, operation: Operation) -> ProgressParser | None:
        return None

    def template(self, name: str) -> ProjectTemplate:
        return template(name)
