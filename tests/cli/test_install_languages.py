"""Which tools ``forja instalar`` wants: the always ones, plus those of the languages found."""

from pathlib import Path

import pytest

from forja.cli.detect import Detection, _languages_in
from forja.cli.install import missing_tools
from forja.toolchain.load import load_manifest

ALWAYS = ["gitleaks", "osv-scanner", "jscpd", "semgrep"]


@pytest.fixture
def environ(tmp_path: Path) -> dict[str, str]:
    return {"PATH": "", "FORJA_HOME": str(tmp_path / "home")}


def found(cwd: Path, python: bool = False, **languages: bool) -> Detection:
    return Detection(
        cwd=cwd, repo=cwd, has_commits=True, branch="main", head="h", changed=(),
        python=python, gitleaks=False, git=True, disk_free_gib=10.0, **languages,
    )  # fmt: skip


def names(environ: dict[str, str], detection: Detection | None) -> list[str]:
    return [tool.name for tool in missing_tools(environ, load_manifest(), detection)]


def test_without_a_detection_only_the_always_tools_are_wanted(environ: dict[str, str]) -> None:
    assert names(environ, None) == ALWAYS


def test_a_repository_of_no_such_language_wants_no_language_tool(
    tmp_path: Path, environ: dict[str, str]
) -> None:
    assert names(environ, found(tmp_path)) == ALWAYS


def test_python_wants_mypy(tmp_path: Path, environ: dict[str, str]) -> None:
    assert names(environ, found(tmp_path, python=True)) == [*ALWAYS, "mypy"]


def test_go_wants_go_and_golangci_lint(tmp_path: Path, environ: dict[str, str]) -> None:
    assert names(environ, found(tmp_path, go=True)) == [*ALWAYS, "go", "golangci-lint"]


def test_typescript_wants_node(tmp_path: Path, environ: dict[str, str]) -> None:
    assert names(environ, found(tmp_path, typescript=True)) == [*ALWAYS, "node"]


def test_csharp_wants_the_dotnet_sdk_and_dotnet_coverage(
    tmp_path: Path, environ: dict[str, str]
) -> None:
    wanted = [*ALWAYS, "dotnet-sdk", "dotnet-coverage"]
    assert names(environ, found(tmp_path, csharp=True)) == wanted


def test_java_with_maven_wants_the_jdk_and_maven_only(
    tmp_path: Path, environ: dict[str, str]
) -> None:
    detection = found(tmp_path, java=True, maven=True)
    assert names(environ, detection) == [*ALWAYS, "jdk", "maven"]


def test_java_with_gradle_and_no_wrapper_wants_the_jdk_and_gradle_only(
    tmp_path: Path, environ: dict[str, str]
) -> None:
    detection = found(tmp_path, java=True, gradle=True)
    assert names(environ, detection) == [*ALWAYS, "jdk", "gradle"]


def test_java_that_carries_its_own_wrapper_wants_the_jdk_only(
    tmp_path: Path, environ: dict[str, str]
) -> None:
    assert names(environ, found(tmp_path, java=True)) == [*ALWAYS, "jdk"]


def test_a_language_tool_on_the_path_is_not_wanted(tmp_path: Path) -> None:
    folder = tmp_path / "bin"
    folder.mkdir()
    for name in ("go", "go.exe"):
        (folder / name).write_text("x", encoding="utf-8")
        (folder / name).chmod(0o755)
    environ = {"PATH": str(folder), "FORJA_HOME": str(tmp_path / "home")}
    assert "go" not in names(environ, found(tmp_path, go=True))


def test_maven_is_found_by_its_pom(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    seen = _languages_in(tmp_path)
    assert (seen["java"], seen["maven"], seen["gradle"]) == (True, True, False)


@pytest.mark.parametrize("build", ["build.gradle", "build.gradle.kts"])
def test_gradle_without_a_wrapper_is_found(tmp_path: Path, build: str) -> None:
    (tmp_path / build).write_text("", encoding="utf-8")
    seen = _languages_in(tmp_path)
    assert (seen["java"], seen["maven"], seen["gradle"]) == (True, False, True)


@pytest.mark.parametrize("wrapper", ["gradlew", "gradlew.bat"])
def test_gradle_with_a_wrapper_wants_no_gradle(tmp_path: Path, wrapper: str) -> None:
    (tmp_path / "build.gradle").write_text("", encoding="utf-8")
    (tmp_path / wrapper).write_text("", encoding="utf-8")
    seen = _languages_in(tmp_path)
    assert (seen["java"], seen["gradle"]) == (True, False)


@pytest.mark.parametrize("name", ["app.sln", "app.csproj"])
def test_csharp_is_found_by_a_solution_or_a_project(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_text("", encoding="utf-8")

    assert _languages_in(tmp_path)["csharp"] is True


@pytest.mark.parametrize(
    ("files", "found"),
    [
        (("package.json", "tsconfig.json"), True),
        (("package.json",), False),
        (("tsconfig.json",), False),
        ((), False),
    ],
)
def test_typescript_needs_both_the_package_and_its_tsconfig(
    tmp_path: Path, files: tuple[str, ...], found: bool
) -> None:
    for name in files:
        (tmp_path / name).write_text("{}", encoding="utf-8")

    assert _languages_in(tmp_path)["typescript"] is found
