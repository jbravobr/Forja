"""The Go adapter: gofmt for format, golangci-lint for lint (when installed), ``go build`` and
``go test`` for build, tests and coverage (``-coverprofile``).

It only describes commands and reads their output; the core runs them. Go has no lint tool of its
own that the toolchain ships, so without ``golangci_lint`` the lint operations are not supported
and nothing pretends otherwise; ``forja doctor`` says so.
"""

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
from forja.core.adapters.common import PROPERTY_SEED
from forja.core.adapters.executor import locate_in_path
from forja.core.adapters.ownership import says
from forja.core.reports import ReportFormat, ReportSpec
from forja.languages.go import parsers
from forja.languages.go.manifest import declared_dependencies
from forja.languages.go.template import template

MANIFEST = "go.mod"
CONFIG_FILES = (
    MANIFEST,
    "go.sum",
    "go.work",
    ".golangci.yml",
    ".golangci.yaml",
    ".golangci.toml",
    ".golangci.json",
)
# ``go test`` cannot create the directory of its profile, so it lands at the root and is removed
# once read.
COVERAGE_REPORT = ".forja-cover.out"
# rapid reads these when the test binary starts. Its flags exist only in a package that imports
# rapid, so a flag on ``go test ./...`` would fail every other package; the variables are harmless
# there.
_RAPID = {"RAPID_SEED": str(PROPERTY_SEED), "RAPID_NOFAILFILE": "true"}
_GO_VERSION = r"go version go(\S+)"
_LINT_VERSION = r"version (\S+)"
# What the Go tool chain reads from the environment beyond the generic list: the build cache
# (``LOCALAPPDATA`` on Windows), module locations and the settings of the user's own installation.
GO_ENVIRONMENT = frozenset(
    {
        "LOCALAPPDATA",
        "GOCACHE",
        "GOPATH",
        "GOMODCACHE",
        "GOROOT",
        "GOFLAGS",
        "GOPROXY",
        "GOSUMDB",
        "GOTOOLCHAIN",
        "GOENV",
    }
)


def _packages(files: tuple[str, ...]) -> tuple[str, ...]:
    """The package directories of the files: the linter type-checks a package, and a file given
    alone does not compile without the others of its package."""
    directories = {Path(f).parent.as_posix() for f in files}
    return tuple(sorted("." if d == "." else f"./{d}" for d in directories))


def find_golangci_lint(environ: Mapping[str, str] | None = None) -> tuple[str, ...] | None:
    """The golangci-lint on the PATH, or ``None``: it is not installed by Forja."""
    path = (environ or {}).get("PATH")
    found = locate_in_path("golangci-lint", path)
    return (str(found),) if found else None


class GoAdapter:
    contract_version = CONTRACT_VERSION
    language = "go"
    dependency_ecosystem = "Go"  # the registry of its manifests
    grammar = "go"
    # what switches the linter off in a line, and what skips a test
    suppressions = (r"//\s*nolint", r"\.Skip(?:f|Now)?\(")
    capabilities = frozenset({LanguageCapability.COVERAGE, LanguageCapability.STRUCTURAL_METERS})

    def __init__(
        self,
        *,
        go: tuple[str, ...] = ("go",),
        gofmt: tuple[str, ...] = ("gofmt",),
        golangci_lint: tuple[str, ...] | None = None,
    ) -> None:
        self._go = go
        self._gofmt = gofmt
        self._lint = golangci_lint

    # -- finding and classifying ---------------------------------------------------------------

    def detect(self, root: Path) -> Detection | None:
        if not (root / MANIFEST).is_file():
            return None
        probe = VersionProbe(self._plan(root, (*self._go, "version")), _GO_VERSION)
        return Detection("go", (".",), (MANIFEST,), probe)

    def owns_test(self, test_id: str, path: str, text: str) -> bool:
        """The ids (``package/TestX/sub``) do not carry the file: ``func TestX`` has to be in it."""
        name = next((s for s in test_id.split("/") if s.startswith("Test")), "")
        return says(text, f"func {name}") if name else False

    def declared_dependencies(self, path: str, text: str) -> frozenset[str] | None:
        """The packages a manifest declares; ``None`` when ``path`` is not one of this
        language's manifests, ``ValueError`` when it is one that cannot be read."""
        return declared_dependencies(path, text)

    def classify(self, path: str) -> FileClass | None:
        parts = path.replace("\\", "/").split("/")
        name, directories = parts[-1], parts[:-1]
        if not name.endswith(".go") or "vendor" in directories:
            return None
        # the Go tool chain skips files and directories that start with "_" or "."
        if any(part[:1] in ("_", ".") and part not in (".", "..") for part in parts):
            return None
        if "testdata" in directories:  # the Go tool chain ignores it; it holds sample inputs
            return FileClass.FIXTURE
        return FileClass.TEST if name.endswith("_test.go") else FileClass.PRODUCTION

    # -- commands ----------------------------------------------------------------------------

    def tool_info(self, operation: Operation) -> ToolInfo | NotSupported:
        here = Path(".")
        go = VersionProbe(self._plan(here, (*self._go, "version")), _GO_VERSION)
        match operation:
            case Operation.FORMAT:  # gofmt has no version of its own: it ships with Go
                return ToolInfo("gofmt", go, "go", go)
            case Operation.LINT | Operation.LINT_FIX if self._lint is not None:
                lint = VersionProbe(self._plan(here, (*self._lint, "--version")), _LINT_VERSION)
                return ToolInfo("golangci-lint", lint, "go", go)
            case Operation.BUILD | Operation.TEST | Operation.COVERAGE:
                return ToolInfo("go", go, "go", go)
        return NotSupported(operation.value, "the Go adapter does not offer it")

    def plan(self, operation: Operation, request: OperationRequest) -> CommandPlan | NotSupported:
        targets = request.files or (".",)
        match operation:
            case Operation.FORMAT:
                write = () if request.check_only else ("-w",)
                argv = (*self._gofmt, "-l", *write, "--", *targets)
            case Operation.LINT | Operation.LINT_FIX if self._lint is None:
                return NotSupported(operation.value, "golangci-lint is not installed")
            case Operation.LINT | Operation.LINT_FIX:
                assert self._lint is not None
                fix = ("--fix",) if operation is Operation.LINT_FIX else ()
                argv = (*self._lint, "run", "--color=never", *fix, *_packages(request.files))
            case Operation.BUILD:
                argv = (*self._go, "build", "./...")
            case Operation.TEST | Operation.COVERAGE if request.test_files or request.tests:
                return NotSupported(operation.value, "Go tests are selected by package, not file")
            case Operation.TEST:
                argv = (*self._go, "test", "-count=1", "-json", "./...")
            case Operation.COVERAGE:
                argv = (
                    *self._go,
                    "test",
                    "-count=1",
                    "-json",
                    "-coverpkg=./...",
                    f"-coverprofile={COVERAGE_REPORT}",
                    "./...",
                )
            case _:
                return NotSupported(operation.value, "the Go adapter does not offer it")
        return self._plan(request.root, argv, config_files=CONFIG_FILES)

    def report(self, operation: Operation) -> ReportSpec | NotSupported:
        if operation is Operation.COVERAGE:
            return ReportSpec(ReportFormat.GO_COVERPROFILE, COVERAGE_REPORT)
        return NotSupported(operation.value, "the Go adapter writes no such report")

    @staticmethod
    def _plan(root: Path, argv: tuple[str, ...], config_files: tuple[str, ...] = ()) -> CommandPlan:
        return CommandPlan(
            argv,
            root,
            env_allow=GO_ENVIRONMENT,
            env_set={"NO_COLOR": "1", **_RAPID},
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
        return Unparseable(f"the Go adapter does not read {operation.value} output")

    def progress_parser(self, operation: Operation) -> ProgressParser | None:
        return None

    def template(self, name: str) -> ProjectTemplate:
        return template(name)
