"""The test runs of the Java adapter: the ids, the filter, the order, the stop and the lines, as
the commands say them (no tool runs here; the slow tests run the real ones)."""

from pathlib import Path

import pytest

from forja.core.adapters import (
    CommandPlan,
    NotSupported,
    Operation,
    OperationRequest,
    Parsed,
    ToolOutput,
)
from forja.core.paths import forja_home
from forja.core.reports import ReportFormat, ReportSpec
from forja.languages.java import JavaAdapter, probe, selection

POM = "<project><modelVersion>4.0.0</modelVersion></project>"
GRADLE_FILE = "plugins { id 'java' }\ntest { useJUnitPlatform() }\n"
MAVEN = ("m",)
GRADLE = ("g",)


@pytest.fixture
def probe_jars(monkeypatch: pytest.MonkeyPatch) -> str:
    """The probe's jars as if the local repository had them."""
    jars = "probe-jars.jar"
    monkeypatch.setattr(probe, "classpath", lambda repository: jars)
    return jars


@pytest.fixture
def no_probe_jars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe, "classpath", lambda repository: None)


def maven_project(root: Path) -> Path:
    (root / "pom.xml").write_text(POM, encoding="utf-8")
    return root


def gradle_project(root: Path) -> Path:
    (root / "build.gradle").write_text(GRADLE_FILE, encoding="utf-8")
    return root


def maven_plan(root: Path, operation: Operation, **options: object) -> CommandPlan | NotSupported:
    return JavaAdapter(maven=MAVEN).plan(operation, OperationRequest(root=root, **options))  # type: ignore[arg-type]


def gradle_plan(root: Path, operation: Operation, **options: object) -> CommandPlan | NotSupported:
    adapter = JavaAdapter(maven=MAVEN, gradle=GRADLE)
    adapter.detect(root)
    return adapter.plan(operation, OperationRequest(root=root, **options))  # type: ignore[arg-type]


def launch(plan: CommandPlan) -> tuple[str, ...]:
    """The argv after the launcher's own program, script, mode and cache: what Maven gets."""
    assert plan.argv[1].endswith("Lancador.java")
    return plan.argv[5:]


# -- the ids ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reported", "method"),
    [
        ("com.acme.CalcTest.soma", "com.acme.CalcTest.soma"),
        ("com.acme.CalcTest$Nested.soma", "com.acme.CalcTest$Nested.soma"),
        ("com.acme.CalcTest.soma(int, int)[2]", "com.acme.CalcTest.soma"),
        ("com.acme.CalcTest.[2] 1, 2", "com.acme.CalcTest"),
    ],
)
def test_a_reported_case_answers_to_its_method(reported: str, method: str) -> None:
    assert selection.selector(reported) == method


def test_a_method_id_says_its_class_and_method_and_nothing_else() -> None:
    assert selection.split("com.acme.CalcTest$Nested.soma") == ("com.acme.CalcTest$Nested", "soma")
    assert selection.split("CalcTest") is None
    assert selection.split("com.acme.CalcTest.soma(int)") is None
    assert selection.split("com.acme.CalcTest.a b") is None


def test_maven_filters_by_class_and_method_with_the_nested_dollar() -> None:
    ids = ("com.acme.CalcTest.soma", "com.acme.CalcTest$Nested.subtrai")
    assert selection.maven_filter(ids) == "com.acme.CalcTest#soma,com.acme.CalcTest$Nested#subtrai"


def test_gradle_takes_each_id_as_it_is() -> None:
    assert selection.gradle_filter(("com.acme.CalcTest$Nested.soma",)) == (
        "com.acme.CalcTest$Nested.soma",
    )


def test_the_order_is_the_order_of_the_ids_by_class_and_by_method() -> None:
    order = selection.order_arguments(
        ("com.acme.B.x", "com.acme.A.y", "com.acme.B.z", "com.acme.A.y")
    )
    assert order[0] == "-Dforja.ordem=com.acme.B,com.acme.A"
    assert order[1] == "-Dforja.ordemMetodos=com.acme.B.x,com.acme.A.y,com.acme.B.z"


# -- Maven --------------------------------------------------------------------------------------


def test_a_filtered_run_goes_through_the_launcher_in_order_and_stops(
    tmp_path: Path, probe_jars: str
) -> None:
    root = maven_project(tmp_path)
    plan = maven_plan(
        root,
        Operation.TEST,
        tests=("com.acme.CalcTest.soma", "com.acme.CalcTest$Nested.subtrai"),
        rerun=True,
    )
    assert isinstance(plan, CommandPlan)
    assert plan.argv[2] == str(probe.SOURCES)
    assert plan.argv[3] == "testar"
    assert plan.env_set["FORJA_PROBE_CLASSPATH"] == probe_jars
    argv = launch(plan)
    assert argv[:4] == ("m", "-B", "-ntp", "test")
    assert "-Dtest=com.acme.CalcTest#soma,com.acme.CalcTest$Nested#subtrai" in argv
    assert "-Dforja.parada=1" in argv
    assert "-Djunit.jupiter.testclass.order.default=com.forja.probe.OrdemDeClasses" in argv
    assert "-Dforja.ordem=com.acme.CalcTest,com.acme.CalcTest$Nested" in argv


def test_a_whole_rerun_stops_at_the_first_failure_and_a_first_run_stays_clean(
    tmp_path: Path, probe_jars: str
) -> None:
    root = maven_project(tmp_path)
    rerun = launch(maven_plan(root, Operation.TEST, rerun=True))  # type: ignore[arg-type]
    assert rerun[-2:] == (
        "-Djunit.jupiter.extensions.autodetection.enabled=true",
        "-Dforja.parada=1",
    )
    first = maven_plan(root, Operation.TEST)
    assert isinstance(first, CommandPlan)
    assert first.argv == ("m", "-B", "-ntp", "clean", "test")


def test_without_the_probe_jars_a_filter_is_a_plain_maven_command(
    tmp_path: Path, no_probe_jars: None
) -> None:
    plan = maven_plan(maven_project(tmp_path), Operation.TEST, tests=("com.acme.A.b",), rerun=True)
    assert isinstance(plan, CommandPlan)
    assert plan.argv == ("m", "-B", "-ntp", "test", "-Dtest=com.acme.A#b")


def test_a_test_id_that_is_not_a_method_is_not_offered(tmp_path: Path, probe_jars: str) -> None:
    plan = maven_plan(maven_project(tmp_path), Operation.TEST, tests=("test_a::case",))
    assert isinstance(plan, NotSupported)


def test_ids_that_do_not_fit_one_command_line_are_not_offered(
    tmp_path: Path, probe_jars: str
) -> None:
    many = tuple(f"com.acme.Class{i}.method{'x' * 60}" for i in range(400))
    plan = maven_plan(maven_project(tmp_path), Operation.TEST, tests=many, rerun=True)
    assert isinstance(plan, NotSupported)


def test_the_test_lines_run_writes_the_listener_data_and_asks_for_the_reader(
    tmp_path: Path, probe_jars: str
) -> None:
    plan = maven_plan(maven_project(tmp_path), Operation.TEST_LINES)
    assert isinstance(plan, CommandPlan)
    assert plan.argv[3] == "linhas"
    argv = launch(plan)
    assert argv[3:] == (
        "clean",
        "org.jacoco:jacoco-maven-plugin:0.8.13:prepare-agent",
        "test",
        "-Djunit.jupiter.extensions.autodetection.enabled=true",
        "-Dforja.execdir=.forja-test-lines/exec",
    )


def test_the_test_lines_need_the_probe_jars(tmp_path: Path, no_probe_jars: None) -> None:
    assert isinstance(maven_plan(maven_project(tmp_path), Operation.TEST_LINES), NotSupported)


def test_the_test_lines_are_read_as_the_forja_format_from_the_file_the_reader_writes() -> None:
    spec = JavaAdapter(maven=MAVEN).report(Operation.TEST_LINES)
    assert spec == ReportSpec(
        ReportFormat.TEST_LINES_JSON, ".forja-test-lines/linhas.json", ("src/main/java",)
    )


def test_a_failed_filtered_case_is_read_as_its_method_id(tmp_path: Path) -> None:
    reports = tmp_path / "target" / "surefire-reports"
    reports.mkdir(parents=True)
    (reports / "TEST-acme.xml").write_text(
        '<testsuite name="com.acme.CalcTest$Nested"><testcase name="soma(int, int)[2]"'
        ' classname="com.acme.CalcTest$Nested"><failure message="x"/></testcase></testsuite>',
        encoding="utf-8",
    )
    log = tmp_path / "out.log"
    log.write_text("[ERROR] Tests run: 1, Failures: 1\n", encoding="utf-8")
    adapter = JavaAdapter(maven=MAVEN)
    result = adapter.parse(Operation.TEST, ToolOutput(1, log, tmp_path))
    assert isinstance(result, Parsed)
    failed = [d.target.id for d in result.drafts if d.value == "failed"]
    assert failed == ["com.acme.CalcTest$Nested.soma(int, int)[2]"]
    assert adapter.selector(failed[0]) == "com.acme.CalcTest$Nested.soma"


# -- Gradle -------------------------------------------------------------------------------------


def test_a_gradle_filter_gives_each_id_its_tests_flag_and_the_stop_and_the_probe(
    tmp_path: Path,
) -> None:
    plan = gradle_plan(
        gradle_project(tmp_path),
        Operation.TEST,
        tests=("com.acme.CalcTest.soma", "com.acme.CalcTest$Nested.subtrai"),
        rerun=True,
    )
    assert isinstance(plan, CommandPlan)
    argv = plan.argv
    assert argv[argv.index("cleanTest") :] == (
        "cleanTest",
        "test",
        "--tests",
        "com.acme.CalcTest.soma",
        "--tests",
        "com.acme.CalcTest$Nested.subtrai",
    )
    assert f"-Dforja.probe={probe.SOURCES}" in argv
    assert "-Dforja.ordem=com.acme.CalcTest,com.acme.CalcTest$Nested" in argv


def test_a_whole_gradle_rerun_only_stops_at_the_first_failure(tmp_path: Path) -> None:
    plan = gradle_plan(gradle_project(tmp_path), Operation.TEST, rerun=True)
    assert isinstance(plan, CommandPlan)
    assert plan.argv[-2:] == ("cleanTest", "test")
    assert "-Dforja.parada=1" in plan.argv
    assert any(part.startswith("-Dforja.probe=") for part in plan.argv)


def test_a_gradle_build_that_does_not_run_the_junit_platform_gets_none_of_the_probe(
    tmp_path: Path,
) -> None:
    (tmp_path / "build.gradle").write_text("plugins { id 'java' }\n", encoding="utf-8")

    whole = gradle_plan(tmp_path, Operation.TEST, rerun=True)
    chosen = gradle_plan(tmp_path, Operation.TEST, tests=("com.acme.CalcTest.soma",), rerun=True)
    lines = gradle_plan(tmp_path, Operation.TEST_LINES)

    # the tests run as they always did: the probe is JUnit 5's and would not compile there
    assert isinstance(whole, CommandPlan)
    assert whole == gradle_plan(tmp_path, Operation.TEST)
    assert not any("forja" in part for part in whole.argv)
    refusal = "the build does not run the JUnit Platform"
    assert chosen == NotSupported("test", refusal)
    assert lines == NotSupported("test_lines", refusal)


def test_the_gradle_test_lines_run_the_reader_task_after_the_tests(tmp_path: Path) -> None:
    plan = gradle_plan(gradle_project(tmp_path), Operation.TEST_LINES)
    assert isinstance(plan, CommandPlan)
    assert plan.argv[-3:] == ("cleanTest", "test", "forjaTestLines")
    assert "-Dforja.execdir=.forja-test-lines/exec" in plan.argv


def test_gradle_ids_that_do_not_fit_one_command_line_are_not_offered(tmp_path: Path) -> None:
    many = tuple(f"com.acme.Class{i}.method{'x' * 60}" for i in range(400))
    assert isinstance(
        gradle_plan(gradle_project(tmp_path), Operation.TEST, tests=many), NotSupported
    )


def test_the_probe_is_compiled_where_the_constructor_says_not_in_a_shared_directory(
    tmp_path: Path, probe_jars: str
) -> None:
    cache = tmp_path / "cache"
    adapter = JavaAdapter(maven=MAVEN, cache_dir=cache)
    plan = adapter.plan(Operation.TEST_LINES, OperationRequest(root=maven_project(tmp_path)))
    assert isinstance(plan, CommandPlan)
    assert plan.argv[4] == str(cache)


def test_the_default_cache_is_the_forja_home_of_the_user() -> None:
    assert probe.default_cache_dir() == forja_home() / "java-probe"
