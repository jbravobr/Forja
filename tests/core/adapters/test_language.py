from pathlib import Path

import pytest

from forja.core.adapters import (
    CAPABILITY_OPERATION,
    REQUIRED_OPERATIONS,
    CommandPlan,
    FileClass,
    LanguageCapability,
    NotSupported,
    Operation,
    OperationRequest,
    Parsed,
    ToolOutput,
    Unparseable,
    plan_with_override,
)
from forja.core.policy.config import ADAPTER_OPERATIONS
from tests.core.adapters.fakes import FULL_CAPABILITIES, FakeLanguageAdapter

ADAPTER = FakeLanguageAdapter()


class LimitedAdapter(FakeLanguageAdapter):
    capabilities = frozenset({LanguageCapability.PROGRESS_REALTIME})


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    return tmp_path


def log_with(tmp_path: Path, *lines: str) -> Path:
    log = tmp_path / "op.log"
    log.write_text("\n".join(f"[out] {line}" for line in lines), encoding="utf-8")
    return log


def test_the_operations_match_the_ones_the_policy_accepts_for_overrides() -> None:
    own = {Operation.MUTATION, Operation.TEST_LINES}  # no repository has a command for these
    assert {op.value for op in Operation if op not in own} == ADAPTER_OPERATIONS


def test_building_and_testing_are_the_required_operations() -> None:
    assert {Operation.BUILD, Operation.TEST} == REQUIRED_OPERATIONS
    assert set(CAPABILITY_OPERATION.values()) == {
        Operation.COVERAGE,
        Operation.TEST_LINES,
        Operation.MUTATION,
        Operation.E2E,
    }


# -- detection and classification --------------------------------------------------------------


def test_a_language_is_detected_from_its_manifest(project: Path, tmp_path: Path) -> None:
    detection = ADAPTER.detect(project)

    assert detection is not None
    assert (detection.language, detection.roots, detection.manifests) == (
        "python",
        (".",),
        ("pyproject.toml",),
    )
    assert detection.toolchain_probe is not None
    assert ADAPTER.detect(tmp_path / "empty") is None


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/app.py", FileClass.PRODUCTION),
        ("tests/test_app.py", FileClass.TEST),
        ("pkg/test_inner.py", FileClass.TEST),
        ("tests/fixtures/data.json", FileClass.FIXTURE),
        ("README.md", FileClass.DOCUMENTATION),
        ("docs/guide.rst", FileClass.DOCUMENTATION),
        ("package-lock.json", None),
        (".github/workflows/ci.yml", None),
    ],
)
def test_files_are_classified_or_left_unclaimed(path: str, expected: FileClass | None) -> None:
    assert ADAPTER.classify(path) is expected


def test_windows_separators_classify_like_slashes() -> None:
    assert ADAPTER.classify("tests\\test_app.py") is FileClass.TEST
    assert ADAPTER.classify("src\\app.py") is FileClass.PRODUCTION


# -- planning ----------------------------------------------------------------------------------


def test_every_required_operation_can_be_planned(project: Path) -> None:
    for operation in REQUIRED_OPERATIONS:
        assert isinstance(ADAPTER.plan(operation, OperationRequest(root=project)), CommandPlan)


def test_an_operation_the_adapter_does_not_have_is_not_supported_never_an_exception(
    project: Path,
) -> None:
    answer = ADAPTER.plan(Operation.E2E, OperationRequest(root=project))

    assert isinstance(answer, NotSupported)
    assert answer.what == "e2e" and answer.reason


def test_a_plan_declares_its_environment_and_config_files(project: Path) -> None:
    plan = ADAPTER.plan(Operation.BUILD, OperationRequest(root=project))

    assert isinstance(plan, CommandPlan)
    assert plan.env_set == {"NO_COLOR": "1"} and plan.config_files == ("pyproject.toml",)
    assert plan.cwd == project


def test_test_filters_change_the_command(project: Path) -> None:
    plain = ADAPTER.plan(Operation.TEST, OperationRequest(root=project))
    by_file = ADAPTER.plan(
        Operation.TEST, OperationRequest(root=project, test_files=("tests/test_a.py",))
    )
    by_test = ADAPTER.plan(Operation.TEST, OperationRequest(root=project, tests=("a::case",)))

    assert isinstance(plain, CommandPlan) and isinstance(by_file, CommandPlan)
    assert isinstance(by_test, CommandPlan)
    assert by_file.argv == ("fake-test", "--file", "tests/test_a.py")
    assert by_test.argv == ("fake-test", "--test", "a::case")
    assert plain.argv == ("fake-test",)


def test_a_filter_the_adapter_cannot_apply_is_refused_not_ignored(project: Path) -> None:
    limited = LimitedAdapter()

    for request in (
        OperationRequest(root=project, test_files=("tests/test_a.py",)),
        OperationRequest(root=project, tests=("a::case",)),
    ):
        assert isinstance(limited.plan(Operation.TEST, request), NotSupported)
    assert isinstance(
        limited.plan(Operation.MUTATION, OperationRequest(root=project, incremental=True)),
        NotSupported,
    )


def test_incremental_mutation_carries_the_changed_files(project: Path) -> None:
    plan = ADAPTER.plan(
        Operation.MUTATION,
        OperationRequest(root=project, incremental=True, changed_files=("src/a.py", "src/b.py")),
    )

    assert isinstance(plan, CommandPlan)
    assert plan.argv == ("fake-mutation", "--incremental", "src/a.py", "src/b.py")


def test_capabilities_and_operations_agree(project: Path) -> None:
    for capability, operation in CAPABILITY_OPERATION.items():
        planned = isinstance(ADAPTER.plan(operation, OperationRequest(root=project)), CommandPlan)
        assert planned is (capability in FULL_CAPABILITIES)


# -- the repository's own commands -------------------------------------------------------------


def test_a_repository_command_replaces_the_command_line_and_nothing_else(project: Path) -> None:
    request = OperationRequest(root=project)
    default = ADAPTER.plan(Operation.TEST, request)
    overrides = {"python": {"test": ("uv", "run", "pytest", "-q")}}

    plan = plan_with_override(ADAPTER, Operation.TEST, request, overrides)

    assert isinstance(plan, CommandPlan) and isinstance(default, CommandPlan)
    assert plan.argv == ("uv", "run", "pytest", "-q")
    assert (plan.cwd, plan.env_set, plan.config_files) == (
        default.cwd,
        default.env_set,
        default.config_files,
    )


def test_an_override_only_applies_to_its_language_and_operation(project: Path) -> None:
    request = OperationRequest(root=project)
    default = ADAPTER.plan(Operation.TEST, request)

    for overrides in (
        {"go": {"test": ("go", "test")}},
        {"python": {"build": ("make",)}},
        {},
    ):
        assert plan_with_override(ADAPTER, Operation.TEST, request, overrides) == default


def test_an_override_cannot_add_an_operation_the_adapter_does_not_support(project: Path) -> None:
    answer = plan_with_override(
        ADAPTER,
        Operation.E2E,
        OperationRequest(root=project),
        {"python": {"e2e": ("run-e2e",)}},
    )

    assert isinstance(answer, NotSupported)


# -- tools, parsing and progress ---------------------------------------------------------------


def test_the_tool_and_toolchain_can_be_asked_their_versions(project: Path) -> None:
    info = ADAPTER.tool_info(Operation.BUILD)

    assert not isinstance(info, NotSupported)
    assert info.tool == "fake-tool" and info.toolchain == "python"
    assert info.tool_probe.plan.argv == ("fake-tool", "--version")
    assert isinstance(ADAPTER.tool_info(Operation.E2E), NotSupported)


def test_test_output_becomes_measurements(tmp_path: Path) -> None:
    log = log_with(tmp_path, "collecting", "RESULT passed=12 failed=1", "done")

    result = ADAPTER.parse(Operation.TEST, ToolOutput(1, log))

    assert isinstance(result, Parsed)
    assert [(d.measurement, d.value, d.unit) for d in result.drafts] == [
        ("tests_passed", 12, "count"),
        ("tests_failed", 1, "count"),
    ]


def test_output_that_cannot_be_understood_is_unparseable_not_zero(tmp_path: Path) -> None:
    for operation in (Operation.TEST, Operation.COVERAGE):
        result = ADAPTER.parse(operation, ToolOutput(0, log_with(tmp_path, "garbage")))
        assert isinstance(result, Unparseable) and result.reason


def test_absurd_numbers_in_the_output_do_not_crash_the_parser(tmp_path: Path) -> None:
    log = log_with(tmp_path, "RESULT passed=" + "9" * 5000 + " failed=1")

    assert isinstance(ADAPTER.parse(Operation.TEST, ToolOutput(0, log)), Unparseable)


def test_progress_is_reported_only_when_the_tool_gives_a_real_count() -> None:
    parser = ADAPTER.progress_parser(Operation.TEST)

    assert parser is not None
    assert parser.feed("[3/10] tests/test_a.py").__dict__ == {
        "done": 3,
        "total": 10,
        "label": "tests",
    }
    assert parser.feed("running...") is None
    assert parser.feed("[5/3] impossible") is None
    assert parser.feed("[0/0] empty") is None
    assert ADAPTER.progress_parser(Operation.BUILD) is None
    assert LimitedAdapter().progress_parser(Operation.TEST) is not None


def test_a_repository_command_that_starts_with_python_runs_in_the_adapters_interpreter(
    project: Path,
) -> None:
    class InVenv(FakeLanguageAdapter):
        def plan(self, operation: Operation, request: OperationRequest) -> CommandPlan:
            return CommandPlan(("/venv/bin/python", "-m", "pytest"), request.root)

    for head in ("python", "python3", "Python"):
        plan = plan_with_override(
            InVenv(),
            Operation.TEST,
            OperationRequest(root=project),
            {"python": {"test": (head, "-m", "pytest", "-n", "auto")}},
        )
        assert isinstance(plan, CommandPlan)
        assert plan.argv == ("/venv/bin/python", "-m", "pytest", "-n", "auto")


def test_other_commands_are_left_as_the_repository_wrote_them(project: Path) -> None:
    class InVenv(FakeLanguageAdapter):
        def plan(self, operation: Operation, request: OperationRequest) -> CommandPlan:
            return CommandPlan(("/venv/bin/python", "-m", "pytest"), request.root)

    plan = plan_with_override(
        InVenv(),
        Operation.TEST,
        OperationRequest(root=project),
        {"python": {"test": ("make", "test")}},
    )

    assert isinstance(plan, CommandPlan)
    assert plan.argv == ("make", "test")
