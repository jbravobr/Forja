"""Gradle as the build tool of the Java adapter: the commands, as seen in real runs of Gradle 9.8.2.

Nothing is applied to the project except JaCoCo, which an init script names in full (as the Maven
goals do). A linter or the formatter runs only when the build file declares its plugin. The init
scripts are data next to this module (``init_scripts``): they make the linters not fail the build
and make SpotBugs and JaCoCo write XML. Tasks are matched by name there because the plugin classes
do not resolve in an init script.
"""

import re
import sys
from pathlib import Path

from forja.core.adapters.executor import locate_in_path
from forja.languages.java import probe, selection

MANIFESTS = ("build.gradle", "build.gradle.kts")
CONFIG_FILES = (
    *MANIFESTS,
    "settings.gradle",
    "settings.gradle.kts",
    "gradle.properties",
    "gradle/libs.versions.toml",
    "gradle/wrapper/gradle-wrapper.properties",
    "config/checkstyle/checkstyle.xml",
    "config/pmd/ruleSet.xml",
)
VERSION = r"Gradle (\d+\.\d+(?:\.\d+)?\S*)"
COVERAGE_REPORT = "build/reports/jacoco/test/jacocoTestReport.xml"
ENVIRONMENT = frozenset({"GRADLE_USER_HOME", "GRADLE_OPTS", "GRADLE_HOME"})
_SCRIPTS = Path(__file__).parent / "init_scripts"
_BASE = ("--console", "plain")


def manifest(root: Path) -> str | None:
    """The text of the build file at the root, or ``None`` if there is none."""
    for name in MANIFESTS:
        try:
            return (root / name).read_text(encoding="utf-8")
        except OSError:
            continue
    return None


def find_gradle(root: Path) -> tuple[str, ...]:
    """The wrapper the repository carries, else ``gradle`` as the platform would start it."""
    wrapper = root / ("gradlew.bat" if sys.platform == "win32" else "gradlew")
    if wrapper.is_file():
        return (str(wrapper),)
    found = locate_in_path("gradle")
    return (str(found),) if found else ("gradle",)


def _script(name: str) -> tuple[str, str]:
    return ("-I", str(_SCRIPTS / f"{name}.init.gradle"))


def linter_tasks(build: str) -> tuple[str, ...]:
    tasks = (
        ("checkstyle", "checkstyleMain"),
        ("pmd", "pmdMain"),
        ("com.github.spotbugs", "spotbugsMain"),
    )
    return tuple(task for plugin, task in tasks if re.search(rf"\b{re.escape(plugin)}\b", build))


def runs_junit_platform(build: str) -> bool:
    """Whether the build file turns the JUnit Platform on (``useJUnitPlatform()``), which a
    Gradle build needs to run JUnit 5. One that turns it on somewhere else (a convention plugin)
    is taken as not doing it: the tests then run as they always did."""
    return "useJUnitPlatform" in build


def declares_spotless(build: str) -> bool:
    return "com.diffplug.spotless" in build


def build_argv(gradle: tuple[str, ...]) -> tuple[str, ...]:
    return (*gradle, *_BASE, "compileJava", "compileTestJava")


def test_argv(gradle: tuple[str, ...], *, stop: bool = False) -> tuple[str, ...]:
    """``stop``: the probe's condition, which disables every test that starts after a failure
    (Gradle's own fail-fast stops by class, not by test)."""
    if stop:
        return (*gradle, *_BASE, *_probe(), "-Dforja.parada=1", "cleanTest", "test")
    return (*gradle, *_BASE, "cleanTest", "test")


def selected_argv(
    gradle: tuple[str, ...], tests: tuple[str, ...], *, stop: bool
) -> tuple[str, ...]:
    """The tests by id, one ``--tests`` each, in the order given (the probe's orderers, injected
    with the probe's init script)."""
    filters = [part for test in selection.gradle_filter(tests) for part in ("--tests", test)]
    return (
        *gradle,
        *_BASE,
        *_probe(),
        *selection.order_arguments(tests),
        *(("-Dforja.parada=1",) if stop else ()),
        "cleanTest",
        "test",
        *filters,
    )


def lines_argv(gradle: tuple[str, ...]) -> tuple[str, ...]:
    """The tests of each line: the listener writes what each test ran, then the reader (a task of
    the probe's init script) writes the file the core reads. A failing test stops the build before
    the reader runs, so the run is not read."""
    return (
        *gradle,
        *_BASE,
        *_probe(),
        f"-Dforja.execdir={probe.EXECUTIONS}",
        "cleanTest",
        "test",
        "forjaTestLines",
    )


def _probe() -> tuple[str, ...]:
    return (*_script("probe"), f"-Dforja.probe={probe.SOURCES}")


def coverage_argv(gradle: tuple[str, ...]) -> tuple[str, ...]:
    """``--continue``: ``jacocoTestReport`` does not run if ``test`` fails."""
    return (
        *gradle,
        *_BASE,
        "--continue",
        *_script("coverage"),
        "cleanTest",
        "test",
        "jacocoTestReport",
    )


def lint_argv(gradle: tuple[str, ...], tasks: tuple[str, ...]) -> tuple[str, ...]:
    return (*gradle, *_BASE, "--continue", *_script("lint"), *_script("spotbugs"), *tasks)


def format_argv(
    gradle: tuple[str, ...], root: Path, files: tuple[str, ...], check_only: bool
) -> tuple[str, ...]:
    """Applying to one file goes through Spotless' IDE hook. Checking always asks the whole
    project: with the hook Spotless only prints ``IS DIRTY``, without saying which file."""
    if check_only:
        return (*gradle, *_BASE, "spotlessCheck")
    if len(files) == 1:
        return (*gradle, *_BASE, f"-PspotlessIdeHook={root / files[0]}", "spotlessApply")
    return (*gradle, *_BASE, "spotlessApply")
