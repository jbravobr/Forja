"""The JavaScript/TypeScript adapter: Prettier for format, ESLint for lint, ``tsc`` for build, and
Vitest or Jest (whichever the project depends on) for tests and coverage (istanbul JSON).

It only describes commands and reads their output; the core runs them. Every tool is the one the
project installed in its own ``node_modules``, started through ``node``: Forja installs none of
them. A tool the project does not depend on makes its operations not supported, and nothing
pretends otherwise.
"""

import json
import re
from collections.abc import Sequence
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
from forja.core.adapters.common import fits_command_line
from forja.core.reports import TEST_LINES_DIRECTORY, ReportFormat, ReportSpec
from forja.languages.typescript import parsers
from forja.languages.typescript.manifest import declared_dependencies
from forja.languages.typescript.template import template

MANIFEST = "package.json"
TSCONFIG = "tsconfig.json"
CONFIG_FILES = (
    MANIFEST,
    TSCONFIG,
    "package-lock.json",
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    "eslint.config.ts",
    ".prettierrc",
    ".prettierrc.json",
    ".prettierrc.js",
    "prettier.config.js",
    "vitest.config.ts",
    "vitest.config.js",
    "vitest.config.mts",
    "jest.config.ts",
    "jest.config.js",
    "jest.config.cjs",
)
# Both runners write ``coverage-final.json`` in the report directory they are given.
COVERAGE_DIRECTORY = ".forja-coverage"
COVERAGE_REPORT = f"{COVERAGE_DIRECTORY}/coverage-final.json"
# The tests of each line are written by the Forja's own pieces, in the same directory the core
# clears after reading them.
TEST_LINES_REPORT = f"{TEST_LINES_DIRECTORY}/linhas.json"
ESLINT_FORMATTER = str(Path(__file__).with_name("eslint_formatter.cjs"))
JEST_REPORTER = str(Path(__file__).with_name("jest_reporter.cjs"))
JEST_SEQUENCER = str(Path(__file__).with_name("jest_sequencer.cjs"))
JEST_LINES_SETUP = str(Path(__file__).with_name("jest_linhas_setup.cjs"))
JEST_LINES_REPORTER = str(Path(__file__).with_name("jest_linhas_reporter.cjs"))
VITEST_CONFIG = str(Path(__file__).with_name("vitest_forja.config.mjs"))
VITEST_LINES = str(Path(__file__).with_name("vitest_linhas.mjs"))
_VERSION = r"(\d+\.\d+\.\d+\S*)"
_NODE_VERSION = r"v(\d+\.\d+\.\d+\S*)"
# Where the package's own command-line script lives, relative to the project's root.
PRETTIER = "node_modules/prettier/bin/prettier.cjs"
ESLINT = "node_modules/eslint/bin/eslint.js"
TSC = "node_modules/typescript/bin/tsc"
VITEST = "node_modules/vitest/vitest.mjs"
JEST = "node_modules/jest/bin/jest.js"
NODE_ENVIRONMENT = frozenset({"LOCALAPPDATA", "APPDATA", "NODE_PATH"})
_SCRIPT_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
_TEST_NAME = re.compile(r"\.(test|spec)\.[cm]?[jt]sx?$")
_FIXTURE_DIRECTORIES = frozenset({"__fixtures__", "__mocks__", "fixtures", "testdata"})
_SKIPPED_DIRECTORIES = frozenset({"node_modules", "dist", "build", "coverage"})
# between a test's file and its name, in the ids of the TAP reports
_TEST_SEPARATOR = " > "
_REGEX_SPECIAL = re.compile(r"[.*+?^${}()|\[\]\\]")


def _dependencies(root: Path) -> frozenset[str]:
    """The packages ``package.json`` depends on; none if it is missing or not what it should be."""
    try:
        document = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    names: set[str] = set()
    for key in ("dependencies", "devDependencies"):
        section = document.get(key) if isinstance(document, dict) else None
        if isinstance(section, dict):
            names.update(str(name) for name in section)
    return frozenset(names)


def _split_test_id(test_id: str) -> tuple[str, str] | None:
    """The file and the name of a test id (``file > describe > test``); ``None`` without a file."""
    file, separator, name = test_id.partition(_TEST_SEPARATOR)
    return (file, name) if separator and file and name else None


def _pattern(names: Sequence[str], *, jest: bool) -> str:
    """An anchored regular expression for the names. Jest matches the names joined by spaces, and
    a reporter may have collapsed the whitespace of a name, so every run of spaces matches any run
    of spaces."""
    alternatives = []
    for name in names:
        text = name.replace(_TEST_SEPARATOR, " ") if jest else name
        parts = [_REGEX_SPECIAL.sub(r"\\\g<0>", part) for part in re.split(r"\s+", text)]
        alternatives.append(r"\s+".join(parts))
    return "^(?:" + "|".join(alternatives) + ")$"


class TypeScriptAdapter:
    contract_version = CONTRACT_VERSION
    language = "typescript"
    dependency_ecosystem = "npm"  # the registry of its manifests
    grammar = "typescript"
    # what switches off the linter, the compiler, the formatter and the coverage in a file
    suppressions = (
        r"eslint-disable",
        r"@ts-(?:ignore|nocheck|expect-error)",
        r"prettier-ignore",
        r"(?:istanbul|c8|v8)\s+ignore",
    )
    capabilities = frozenset(
        {
            LanguageCapability.COVERAGE,
            LanguageCapability.STRUCTURAL_METERS,
            LanguageCapability.TEST_FILTER_BY_TEST,
            LanguageCapability.TEST_LINES,
        }
    )

    def __init__(self, *, node: tuple[str, ...] = ("node",)) -> None:
        self._node = node

    # -- finding and classifying ---------------------------------------------------------------

    def detect(self, root: Path) -> Detection | None:
        if not ((root / MANIFEST).is_file() and (root / TSCONFIG).is_file()):
            return None
        probe = VersionProbe(self._plan(root, (*self._node, "--version")), _NODE_VERSION)
        return Detection("typescript", (".",), (MANIFEST, TSCONFIG), probe)

    def owns_test(self, test_id: str, path: str, text: str) -> bool:
        """The ids are ``file > suite > test`` with the file relative to the runner's root, which
        may be a folder below the repository's; a file that did not load is its own id."""
        head = test_id.split(" > ")[0].replace("\\", "/")
        where = path.replace("\\", "/")
        return bool(head) and (where == head or where.endswith("/" + head))

    def declared_dependencies(self, path: str, text: str) -> frozenset[str] | None:
        """The packages a manifest declares; ``None`` when ``path`` is not one of this
        language's manifests, ``ValueError`` when it is one that cannot be read."""
        return declared_dependencies(path, text)

    def classify(self, path: str) -> FileClass | None:
        parts = path.replace("\\", "/").split("/")
        name, directories = parts[-1], frozenset(parts[:-1])
        if not name.endswith(_SCRIPT_SUFFIXES) or directories & _SKIPPED_DIRECTORIES:
            return None
        if ".config." in name:  # eslint.config.js, vitest.config.ts: the tools' own settings
            return None
        if directories & _FIXTURE_DIRECTORIES:
            return FileClass.FIXTURE
        if _TEST_NAME.search(name) or "__tests__" in directories:
            return FileClass.TEST
        return FileClass.PRODUCTION

    # -- commands ----------------------------------------------------------------------------

    def tool_info(self, operation: Operation) -> ToolInfo | NotSupported:
        here = Path(".")
        node = VersionProbe(self._plan(here, (*self._node, "--version")), _NODE_VERSION)
        match operation:
            case Operation.FORMAT:
                return self._info("prettier", PRETTIER, node)
            case Operation.LINT | Operation.LINT_FIX:
                return self._info("eslint", ESLINT, node)
            case Operation.BUILD:
                return self._info("tsc", TSC, node)
            case Operation.TEST | Operation.COVERAGE | Operation.TEST_LINES:
                return ToolInfo("vitest or jest", node, "node", node)
        return NotSupported(operation.value, "the JavaScript adapter does not offer it")

    def _info(self, name: str, script: str, node: VersionProbe) -> ToolInfo:
        probe = VersionProbe(self._plan(Path("."), (*self._node, script, "--version")), _VERSION)
        return ToolInfo(name, probe, "node", node)

    def plan(self, operation: Operation, request: OperationRequest) -> CommandPlan | NotSupported:
        dependencies = _dependencies(request.root)
        targets = request.files or (".",)
        node = self._node
        match operation:
            case Operation.FORMAT if "prettier" in dependencies:
                write = () if request.check_only else ("--write",)
                argv = (*node, PRETTIER, "-l", *write, "--", *targets)
            case Operation.LINT | Operation.LINT_FIX if "eslint" in dependencies:
                fix = ("--fix",) if operation is Operation.LINT_FIX else ()
                argv = (*node, ESLINT, "-f", ESLINT_FORMATTER, *fix, "--", *targets)
            case Operation.BUILD if "typescript" in dependencies:
                argv = (*node, TSC, "--noEmit", "--pretty", "false", "-p", ".")
            case Operation.TEST | Operation.COVERAGE if request.test_files:
                return NotSupported(operation.value, "filtering the tests by file is not offered")
            case Operation.COVERAGE if request.tests:
                return NotSupported(operation.value, "filtering the tests is not offered")
            case Operation.TEST if request.tests:
                return self._filtered(request.root, request.tests, request.rerun, dependencies)
            case Operation.TEST | Operation.COVERAGE:
                runner = self._runner(operation, dependencies, request.rerun)
                if isinstance(runner, NotSupported):
                    return runner
                argv = runner
            case Operation.TEST_LINES:
                lines = self._test_lines(dependencies)
                if isinstance(lines, NotSupported):
                    return lines
                argv = lines
            case Operation.FORMAT | Operation.LINT | Operation.LINT_FIX | Operation.BUILD:
                return NotSupported(operation.value, "the project does not depend on that tool")
            case _:
                return NotSupported(operation.value, "the JavaScript adapter does not offer it")
        return self._plan(request.root, argv, config_files=CONFIG_FILES)

    def _runner(
        self, operation: Operation, dependencies: frozenset[str], rerun: bool
    ) -> tuple[str, ...] | NotSupported:
        coverage = operation is Operation.COVERAGE
        if "vitest" in dependencies:
            extra = (
                (
                    "--coverage.enabled",
                    "--coverage.reporter=json",
                    f"--coverage.reportsDirectory={COVERAGE_DIRECTORY}",
                )
                if coverage
                else ()
            )
            stop = ("--bail=1",) if rerun and not coverage else ()
            return (*self._node, VITEST, "run", "--reporter=tap-flat", *extra, *stop)
        if "jest" in dependencies:
            extra = (
                (
                    "--coverage",
                    "--coverageReporters=json",
                    f"--coverageDirectory={COVERAGE_DIRECTORY}",
                )
                if coverage
                else ()
            )
            stop = ("--bail",) if rerun and not coverage else ()
            return (*self._node, JEST, f"--reporters={JEST_REPORTER}", *extra, *stop)
        return NotSupported(operation.value, "the project depends on neither vitest nor jest")

    def _filtered(
        self, root: Path, tests: tuple[str, ...], rerun: bool, dependencies: frozenset[str]
    ) -> CommandPlan | NotSupported:
        """The tests asked for, in series, one file's tests after another in the order of the ids.
        The name pattern also takes a same-named test of another file asked for: a superset of the
        tests asked, never a subset."""
        split = [_split_test_id(test) for test in tests]
        if any(part is None for part in split):
            return NotSupported("test", "a test id without its file")
        pairs = [part for part in split if part is not None]
        files = tuple(dict.fromkeys(file for file, _ in pairs))
        names = tuple(dict.fromkeys(name for _, name in pairs))
        if any(file.startswith("-") for file in files):
            # the files go as arguments: one named like an option would be read as one
            return NotSupported("test", "a test file named like an option")
        if "vitest" in dependencies:
            stop = ("--bail=1",) if rerun else ()
            argv = (
                *self._node,
                VITEST,
                "run",
                *files,
                "-t",
                _pattern(names, jest=False),
                "--reporter=tap-flat",
                "--no-file-parallelism",
                f"--config={VITEST_CONFIG}",
                *stop,
            )
        elif "jest" in dependencies:
            stop = ("--bail",) if rerun else ()
            argv = (
                *self._node,
                JEST,
                *files,
                f"--reporters={JEST_REPORTER}",
                "-t",
                _pattern(names, jest=True),
                "-i",
                f"--testSequencer={JEST_SEQUENCER}",
                *stop,
            )
        else:
            return NotSupported("test", "the project depends on neither vitest nor jest")
        if not fits_command_line(argv):
            return NotSupported("test", "more tests than fit in one command")
        return self._plan(root, argv, config_files=CONFIG_FILES, order=",".join(files))

    def _test_lines(self, dependencies: frozenset[str]) -> tuple[str, ...] | NotSupported:
        if "vitest" in dependencies:
            return (*self._node, VITEST_LINES)
        if "jest" in dependencies:
            return (
                *self._node,
                JEST,
                "--coverage",
                "--coverageReporters=json",
                f"--coverageDirectory={TEST_LINES_DIRECTORY}/cobertura",
                f"--setupFilesAfterEnv={JEST_LINES_SETUP}",
                f"--reporters={JEST_LINES_REPORTER}",
            )
        return NotSupported("test_lines", "the project depends on neither vitest nor jest")

    def report(self, operation: Operation) -> ReportSpec | NotSupported:
        if operation is Operation.COVERAGE:
            return ReportSpec(ReportFormat.ISTANBUL_JSON, COVERAGE_REPORT)
        if operation is Operation.TEST_LINES:
            return ReportSpec(ReportFormat.TEST_LINES_JSON, TEST_LINES_REPORT)
        return NotSupported(operation.value, "the JavaScript adapter writes no such report")

    @staticmethod
    def _plan(
        root: Path,
        argv: tuple[str, ...],
        config_files: tuple[str, ...] = (),
        order: str | None = None,
    ) -> CommandPlan:
        env: dict[str, str] = {"NO_COLOR": "1"}
        if order is not None:
            env["FORJA_FILE_ORDER"] = order  # read by the sequencers, which order the files
        return CommandPlan(
            argv,
            root,
            env_allow=NODE_ENVIRONMENT,
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
            case Operation.BUILD:
                return parsers.parse_build(output)
            case Operation.TEST:
                return parsers.parse_tests(output)
        return Unparseable(f"the JavaScript adapter does not read {operation.value} output")

    def progress_parser(self, operation: Operation) -> ProgressParser | None:
        return None

    def template(self, name: str) -> ProjectTemplate:
        return template(name)
