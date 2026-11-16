import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from forja.core.adapters import (
    AdapterRegistry,
    CommandPlan,
    EvidenceDraft,
    NotSupported,
    Operation,
    OperationRequest,
    Parsed,
    ToolOutput,
    Unparseable,
)
from forja.core.adapters.language import Detection, FileClass
from forja.core.adapters.projects import owner_of
from forja.core.adapters.scoped import ScopedAdapter
from forja.core.evidence import Target, TargetKind
from forja.core.reports import ReportFormat, ReportSpec
from tests.core.adapters.fakes import FakeLanguageAdapter


class Recording(FakeLanguageAdapter):
    """Plans with what it was given and parses to fixed drafts."""

    def __init__(self, drafts: tuple[EvidenceDraft, ...] = ()) -> None:
        self.requests: list[OperationRequest] = []
        self.drafts = drafts

    def plan(self, operation: Operation, request: OperationRequest) -> CommandPlan | NotSupported:
        self.requests.append(request)
        return CommandPlan(("tool", *request.files), request.root, config_files=("setup.cfg",))

    def parse(self, operation: Operation, output: ToolOutput) -> Parsed | Unparseable:
        return Parsed(self.drafts)

    def report(self, operation: Operation) -> ReportSpec | NotSupported:
        return ReportSpec(ReportFormat.COVERAGE_PY_JSON, "cov.json", ("src",))

    def owns_test(self, test_id: str, path: str, text: str) -> bool:
        return test_id == "tests/test_a.py::test_x" and path == "tests/test_a.py"


class Go(FakeLanguageAdapter):
    language = "go"

    def detect(self, root: Path) -> Detection | None:
        return Detection("go", (".",), ("go.mod",)) if (root / "go.mod").is_file() else None

    def classify(self, path: str) -> FileClass | None:
        return FileClass.PRODUCTION if path.endswith(".go") else None


class RecordingGo(Recording):
    language = "go"


def _draft(kind: TargetKind, identifier: str) -> EvidenceDraft:
    return EvidenceDraft("m", Target(kind=kind, id=identifier), 1, "u")


def test_the_root_project_changes_nothing(tmp_path: Path) -> None:
    inner = Recording()
    scoped = ScopedAdapter(inner)
    request = OperationRequest(root=tmp_path, files=("a.py",))

    plan = scoped.plan(Operation.LINT, request)

    assert isinstance(plan, CommandPlan)
    assert inner.requests == [request]
    assert plan.config_files == ("setup.cfg",)
    assert scoped.classify("a.py") is FileClass.PRODUCTION


def test_the_inner_adapter_sees_the_project_directory_and_paths_relative_to_it(
    tmp_path: Path,
) -> None:
    inner = Recording()
    scoped = ScopedAdapter(inner, "web/app")
    request = OperationRequest(
        root=tmp_path,
        files=("web/app/src/a.py", "other/b.py"),
        test_files=("web/app/tests/test_a.py",),
        changed_files=("web/app/src/a.py",),
    )

    plan = scoped.plan(Operation.LINT, request)

    assert isinstance(plan, CommandPlan)
    seen = inner.requests[0]
    assert seen.root == tmp_path / "web" / "app"
    assert seen.files == ("src/a.py",)
    assert seen.test_files == ("tests/test_a.py",)
    assert seen.changed_files == ("src/a.py",)
    assert plan.cwd == tmp_path / "web" / "app"
    assert plan.config_files == ("web/app/setup.cfg",)


@pytest.mark.parametrize("name", ["files", "test_files", "changed_files"])
def test_files_that_are_all_elsewhere_are_not_supported_instead_of_running_everything(
    tmp_path: Path, name: str
) -> None:
    inner = Recording()
    scoped = ScopedAdapter(inner, "web")

    request = dataclasses.replace(OperationRequest(root=tmp_path), **{name: ("api/a.py",)})  # type: ignore[arg-type]

    plan = scoped.plan(Operation.LINT, request)

    assert isinstance(plan, NotSupported)
    assert inner.requests == []


def test_a_request_without_files_runs_the_whole_project(tmp_path: Path) -> None:
    inner = Recording()

    ScopedAdapter(inner, "web").plan(Operation.TEST, OperationRequest(root=tmp_path))

    assert inner.requests[0].files == ()


def test_a_directory_named_like_the_base_prefix_is_not_inside_it() -> None:
    scoped = ScopedAdapter(Recording(), "web")

    assert scoped.classify("web/a.py") is FileClass.PRODUCTION
    assert scoped.classify("website/a.py") is None
    assert scoped.classify("a.py") is None


def test_what_the_inner_adapter_does_not_offer_is_not_supported(tmp_path: Path) -> None:
    scoped = ScopedAdapter(FakeLanguageAdapter(), "web")

    plan = scoped.plan(Operation.E2E, OperationRequest(root=tmp_path))

    assert isinstance(plan, NotSupported)


def test_paths_in_results_gain_the_project_prefix() -> None:
    drafts = (
        _draft(TargetKind.FILE, "src/a.py"),
        _draft(TargetKind.MODULE, "build"),
        _draft(TargetKind.SYMBOL, "tests/test_a.py::test_x"),
    )
    scoped = ScopedAdapter(Recording(drafts), "web")

    result = scoped.parse(Operation.TEST, ToolOutput(0, Path("log")))

    assert isinstance(result, Parsed)
    assert [d.target.id for d in result.drafts] == [
        "web/src/a.py",
        "build",
        "web/tests/test_a.py::test_x",
    ]


def test_a_test_id_stays_as_it_is_for_a_language_that_names_tests_without_their_file() -> None:
    drafts = (_draft(TargetKind.SYMBOL, "TestAdd"), _draft(TargetKind.SYMBOL, "a::b"))

    result = ScopedAdapter(RecordingGo(drafts), "svc").parse(
        Operation.TEST, ToolOutput(0, Path("log"))
    )

    assert isinstance(result, Parsed)
    assert [d.target.id for d in result.drafts] == ["TestAdd", "a::b"]


def test_the_report_is_looked_for_under_the_project() -> None:
    spec = ScopedAdapter(Recording(), "web").report(Operation.COVERAGE)

    assert spec == ReportSpec(ReportFormat.COVERAGE_PY_JSON, "web/cov.json", ("web/src",))


def test_the_index_of_a_report_for_each_test_is_looked_for_under_the_project_too() -> None:
    class Indexed(Recording):
        def report(self, operation: Operation) -> ReportSpec | NotSupported:
            return ReportSpec(ReportFormat.GO_COVERPROFILE, ".t/*.out", index=".t/index.json")

    spec = ScopedAdapter(Indexed(), "web").report(Operation.TEST_LINES)

    assert spec == ReportSpec(
        ReportFormat.GO_COVERPROFILE, "web/.t/*.out", index="web/.t/index.json"
    )
    assert ScopedAdapter(Indexed(), ".").report(Operation.TEST_LINES) == Indexed().report(
        Operation.TEST_LINES
    )


def test_the_inner_adapter_is_told_the_mutated_file_relative_to_the_project_and_the_tests_as_given(
    tmp_path: Path,
) -> None:
    inner = Recording()
    request = OperationRequest(
        root=tmp_path, rerun=True, mutated="web/app/pkg/a.go", tests=("pkg/TestA", "pkg/TestB")
    )

    ScopedAdapter(inner, "web/app").plan(Operation.TEST, request)
    ScopedAdapter(inner, "web/app").plan(Operation.TEST, OperationRequest(root=tmp_path))

    told, plain = inner.requests
    assert told.mutated == "pkg/a.go"
    # the ids are those of the project, as its own reports give them: nothing to translate
    assert (told.tests, told.rerun) == (("pkg/TestA", "pkg/TestB"), True)
    assert plain.mutated is None


def test_ownership_is_asked_with_paths_relative_to_the_project() -> None:
    scoped = ScopedAdapter(Recording(), "web")

    owns = getattr(scoped, "owns_test")  # noqa: B009
    assert owns("web/tests/test_a.py::test_x", "web/tests/test_a.py", "")


def test_an_adapter_without_ownership_stays_without_it() -> None:
    assert not hasattr(ScopedAdapter(FakeLanguageAdapter(), "web"), "owns_test")


def test_detection_looks_in_the_project_directory(tmp_path: Path) -> None:
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "pyproject.toml").write_text("")

    found = ScopedAdapter(FakeLanguageAdapter(), "web").detect(tmp_path)

    assert isinstance(found, Detection)
    assert found.roots == ("web",)
    assert ScopedAdapter(FakeLanguageAdapter(), "api").detect(tmp_path) is None


def test_the_file_belongs_to_the_deepest_project_that_claims_it() -> None:
    projects = [ScopedAdapter(Go()), ScopedAdapter(Go(), "svc"), ScopedAdapter(Go(), "svc/inner")]

    assert owner_of(projects, "svc/inner/a.go") is projects[2]
    assert owner_of(projects, "svc/a.go") is projects[1]
    assert owner_of(projects, "a.go") is projects[0]
    assert owner_of(projects, "svc/a.txt") is None


def test_in_a_tie_the_first_project_wins() -> None:
    first, second = ScopedAdapter(Go(), "x"), ScopedAdapter(FakeLanguageAdapter(), "x")

    assert owner_of([first, second], "x/a.go") is first


def test_the_registry_makes_one_instance_per_project(tmp_path: Path) -> None:
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "go.mod").write_text("")
    registry = AdapterRegistry()
    registry.register_language(Go(), Go)

    projects = registry.projects(tmp_path)

    assert [p.base for p in projects] == ["a", "b"]
    assert projects[0].inner is not projects[1].inner


def test_each_instance_the_registry_makes_has_seen_the_manifest_of_its_own_project(
    tmp_path: Path,
) -> None:
    """An adapter may learn from the manifest what it needs later (Java: Maven or Gradle). The
    instance that finds the projects is not the one each project works with."""

    class Remembering(Go):
        def __init__(self) -> None:
            self.seen: list[Path] = []

        def detect(self, root: Path) -> Detection | None:
            self.seen.append(root)
            return super().detect(root)

    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "go.mod").write_text("")
    registry = AdapterRegistry()
    registry.register_language(Remembering(), Remembering)

    projects = registry.projects(tmp_path)

    seen = [p.inner.seen for p in projects]  # type: ignore[attr-defined]
    assert seen == [[tmp_path / "a"], [tmp_path / "b"]]


def test_a_project_below_the_root_hands_back_what_it_could_not_read_as_it_came(
    tmp_path: Path,
) -> None:
    class Unreadable(Recording):
        def parse(self, operation: Operation, output: ToolOutput) -> Unparseable:
            return Unparseable("no")

        def report(self, operation: Operation) -> NotSupported:
            return NotSupported(operation.value, "none")

    scoped = ScopedAdapter(Unreadable(), "web")

    assert scoped.parse(Operation.LINT, ToolOutput(0, tmp_path / "log")) == Unparseable("no")
    assert scoped.report(Operation.COVERAGE) == NotSupported(Operation.COVERAGE.value, "none")


def test_a_language_whose_test_ids_have_no_path_is_asked_with_the_id_as_it_is() -> None:
    class Owner(RecordingGo):
        def owns_test(self, test_id: str, path: str, text: str) -> bool:
            return (test_id, path) == ("example.com/calc/TestAdd", "calc_test.go")

    scoped = ScopedAdapter(Owner(), "web")

    assert scoped.owns_test("example.com/calc/TestAdd", "web/calc_test.go", "")


@pytest.mark.parametrize("first", ["forja.core.reports", "forja.core.adapters", "forja.mutation"])
def test_each_package_can_be_imported_first(first: str) -> None:
    done = subprocess.run(
        [sys.executable, "-I", "-c", f"import {first}"], capture_output=True, text=True, check=False
    )

    assert done.returncode == 0, done.stderr[-400:]
