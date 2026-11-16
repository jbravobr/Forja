"""The manifest refuses what is incomplete, and the one that ships is complete."""

from importlib import resources
from pathlib import Path

import pytest
from pydantic import ValidationError

from forja.toolchain.load import ManifestError, load_manifest, parse_manifest

SHA256 = "ab" * 32
SHA512 = "cd" * 64

VALID = f"""
schema_version = 1

[[tools]]
name = "gitleaks"
executable = "gitleaks"
purpose = "Procura segredos no código."
license = "MIT"
version = "8.30.1"
needed_by = "always"

[tools.artifacts.windows-x64]
url = "https://example.org/gitleaks.zip"
kind = "zip"
size = 10
algorithm = "sha256"
checksum = "{SHA256}"
checksum_source = "https://example.org/checksums.txt"
executable_path = "gitleaks.exe"
"""


def problems(text: str) -> str:
    with pytest.raises(ManifestError) as raised:
        parse_manifest(text)
    return str(raised.value)


def test_a_complete_manifest_is_read() -> None:
    tool = parse_manifest(VALID).tools[0]
    assert (tool.name, tool.needed_by, tool.artifacts["windows-x64"].size) == (
        "gitleaks",
        "always",
        10,
    )


@pytest.mark.parametrize(
    "line",
    [
        'name = "gitleaks"',
        'executable = "gitleaks"',
        'purpose = "Procura segredos no código."',
        'license = "MIT"',
        'version = "8.30.1"',
        'needed_by = "always"',
        'url = "https://example.org/gitleaks.zip"',
        'kind = "zip"',
        "size = 10",
        'algorithm = "sha256"',
        f'checksum = "{SHA256}"',
        'checksum_source = "https://example.org/checksums.txt"',
        'executable_path = "gitleaks.exe"',
        "schema_version = 1",
    ],
)
def test_a_missing_field_is_refused_by_name(line: str) -> None:
    key = line.split(" = ")[0]
    assert key in problems(VALID.replace(line, ""))


def test_a_tool_without_artifacts_is_refused() -> None:
    head = VALID.split("[tools.artifacts")[0]
    assert "artifacts" in problems(head)


def test_a_manifest_without_tools_is_refused() -> None:
    assert "tools" in problems("schema_version = 1")


@pytest.mark.parametrize("bad", ["http://example.org/a.zip", "ftp://example.org/a.zip", "https://"])
def test_the_url_must_be_https(bad: str) -> None:
    assert "https" in problems(VALID.replace("https://example.org/gitleaks.zip", bad))


@pytest.mark.parametrize("bad", ["http://example.org/c.txt", "file:///c.txt", "c.txt"])
def test_the_checksum_source_must_be_https(bad: str) -> None:
    assert "https" in problems(VALID.replace("https://example.org/checksums.txt", bad))


@pytest.mark.parametrize(
    "algorithm, checksum",
    [
        ("sha256", SHA256[:-1]),
        ("sha256", SHA256 + "a"),
        ("sha256", SHA512),
        ("sha512", SHA256),
        ("sha256", "AB" * 32),
        ("sha256", "zz" * 32),
        ("sha256", ""),
    ],
)
def test_a_checksum_must_fit_its_algorithm(algorithm: str, checksum: str) -> None:
    text = VALID.replace('algorithm = "sha256"', f'algorithm = "{algorithm}"').replace(
        SHA256, checksum
    )
    assert "lowercase hexadecimal" in problems(text)


def test_each_algorithm_accepts_its_own_length() -> None:
    text = VALID.replace('algorithm = "sha256"', 'algorithm = "sha512"').replace(SHA256, SHA512)
    assert parse_manifest(text).tools[0].artifacts["windows-x64"].algorithm == "sha512"


def test_an_unknown_algorithm_is_refused() -> None:
    assert "algorithm" in problems(VALID.replace('"sha256"', '"md5"'))


@pytest.mark.parametrize("size", ["0", "-1", '"10"', "1.5"])
def test_the_size_is_a_positive_integer(size: str) -> None:
    assert "size" in problems(VALID.replace("size = 10", f"size = {size}"))


@pytest.mark.parametrize(
    "kind, url",
    [
        ("tar.gz", "https://example.org/a.zip"),
        ("zip", "https://example.org/a.tar.gz"),
        ("tar.xz", "https://example.org/a.tar.gz"),
        ("wheel", "https://example.org/a.zip"),
        ("nupkg", "https://example.org/a.zip"),
    ],
)
def test_the_kind_must_match_the_file(kind: str, url: str) -> None:
    text = VALID.replace('kind = "zip"', f'kind = "{kind}"').replace(
        "https://example.org/gitleaks.zip", url
    )
    assert f"is not a {kind} file" in problems(text)


def test_the_suffix_is_read_from_the_path_not_the_query() -> None:
    text = VALID.replace("gitleaks.zip", "gitleaks.bin?x=.zip")
    assert "is not a zip file" in problems(text)


def test_a_nuget_package_is_named_as_one() -> None:
    text = VALID.replace('kind = "zip"', 'kind = "nupkg"').replace("gitleaks.zip", "tool.1.0.nupkg")
    assert parse_manifest(text).tools[0].artifacts["windows-x64"].kind == "nupkg"


def test_a_binary_may_have_any_name() -> None:
    text = VALID.replace('kind = "zip"', 'kind = "binary"').replace("gitleaks.zip", "gitleaks_win")
    assert parse_manifest(text).tools[0].artifacts["windows-x64"].kind == "binary"


@pytest.mark.parametrize("kind, name", [("tar.xz", "a.tar.xz"), ("wheel", "a.whl")])
def test_every_archive_kind_matches_its_own_suffix(kind: str, name: str) -> None:
    text = VALID.replace('kind = "zip"', f'kind = "{kind}"').replace("gitleaks.zip", name)
    assert parse_manifest(text).tools[0].artifacts["windows-x64"].kind == kind


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/bin/tool",
        "../tool",
        "bin/../tool",
        "bin\\\\tool",
        "C:/tool",
        "c:tool",
        "./tool",
        "bin//tool",
        "bin/",
    ],
)
def test_the_executable_path_stays_inside_the_artifact(path: str) -> None:
    text = VALID.replace('executable_path = "gitleaks.exe"', f'executable_path = "{path}"')
    assert "relative path" in problems(text)


def test_a_nested_executable_path_is_accepted() -> None:
    text = VALID.replace("gitleaks.exe", "go/bin/go.exe")
    assert parse_manifest(text).tools[0].artifacts["windows-x64"].executable_path == "go/bin/go.exe"


@pytest.mark.parametrize("value", ['"sometimes"', "[]", '["go", "go"]', '["rust"]', "3"])
def test_needed_by_is_always_or_a_list_of_known_languages(value: str) -> None:
    assert "needed_by" in problems(VALID.replace('needed_by = "always"', f"needed_by = {value}"))


def test_needed_by_accepts_a_list_of_languages() -> None:
    text = VALID.replace('needed_by = "always"', 'needed_by = ["javascript", "typescript"]')
    assert parse_manifest(text).tools[0].needed_by == ("javascript", "typescript")


@pytest.mark.parametrize("name", ["Gitleaks", "git leaks", "1go", "", "go_lint"])
def test_names_are_lowercase_identifiers(name: str) -> None:
    assert "identifier" in problems(VALID.replace('name = "gitleaks"', f'name = "{name}"'))


@pytest.mark.parametrize("line", ['purpose = "Procura segredos no código."', 'version = "8.30.1"'])
def test_text_fields_must_not_be_blank(line: str) -> None:
    key = line.split(" = ")[0]
    assert "must not be empty" in problems(VALID.replace(line, f'{key} = "  "'))


@pytest.mark.parametrize("value", ["", "MIT; rm", "$(x)"])
def test_the_license_must_look_like_an_spdx_expression(value: str) -> None:
    assert "SPDX" in problems(VALID.replace('license = "MIT"', f'license = "{value}"'))


def test_compound_spdx_expressions_are_accepted() -> None:
    text = VALID.replace("MIT", "GPL-2.0-only WITH Classpath-exception-2.0")
    assert parse_manifest(text).tools[0].license.endswith("exception-2.0")


def test_a_tool_listed_twice_is_refused() -> None:
    assert "tools listed twice: gitleaks" in problems(VALID + VALID.split("\n", 2)[2])


def test_platforms_the_forja_does_not_serve_are_refused() -> None:
    assert "macos-arm64" in problems(VALID.replace("windows-x64", "macos-arm64"))


def test_unknown_fields_are_refused() -> None:
    assert "surprise" in problems(VALID.replace('kind = "zip"', 'kind = "zip"\nsurprise = 1'))


def test_the_schema_version_must_be_known() -> None:
    assert "schema_version" in problems(VALID.replace("schema_version = 1", "schema_version = 2"))


def test_every_problem_is_listed_at_once() -> None:
    text = VALID.replace("size = 10", "size = 0").replace(
        "https://example.org/gitleaks", "http://x/g"
    )
    assert len(problems(text).splitlines()) >= 3


def test_invalid_toml_is_reported_with_its_source() -> None:
    with pytest.raises(ManifestError) as raised:
        parse_manifest("tools = [", "meu.toml")
    assert raised.value.problems[0].startswith("meu.toml: not valid TOML")


def test_a_manifest_file_can_be_loaded_from_a_path(tmp_path: Path) -> None:
    path = tmp_path / "m.toml"
    path.write_text(VALID, "utf-8")
    assert load_manifest(path).tools[0].name == "gitleaks"


def test_an_unreadable_manifest_path_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ManifestError) as raised:
        load_manifest(tmp_path / "missing.toml")
    assert "missing.toml: cannot be read" in str(raised.value)


def test_a_problem_names_where_it_is(tmp_path: Path) -> None:
    path = tmp_path / "m.toml"
    path.write_text(VALID.replace("size = 10", "size = 0"), "utf-8")
    with pytest.raises(ManifestError) as raised:
        load_manifest(path)
    assert raised.value.problems == [
        f"{path}: tools.0.artifacts.windows-x64.size: Input should be greater than 0"
    ]


# --- the manifest that ships ---------------------------------------------------------------

LANGUAGE_TOOLS = {
    "python": {"mypy"},
    "go": {"go", "golangci-lint"},
    "javascript": {"node"},
    "typescript": {"node"},
    "csharp": {"dotnet-sdk", "dotnet-coverage"},
    "java": {"jdk", "maven", "gradle"},
}


def test_the_shipped_manifest_lists_the_thirteen_tools() -> None:
    assert sorted(tool.name for tool in load_manifest().tools) == sorted(
        [
            "gitleaks",
            "osv-scanner",
            "jscpd",
            "semgrep",
            "mypy",
            "go",
            "golangci-lint",
            "node",
            "dotnet-sdk",
            "dotnet-coverage",
            "jdk",
            "maven",
            "gradle",
        ]
    )


def test_git_is_not_installed_by_the_forja() -> None:
    assert "git" not in {tool.name for tool in load_manifest().tools}


def test_the_transversal_tools_are_always_needed() -> None:
    always = {t.name for t in load_manifest().tools if t.needed_by == "always"}
    assert always == {"gitleaks", "osv-scanner", "jscpd", "semgrep"}


def test_the_language_tools_are_needed_by_their_languages() -> None:
    needed: dict[str, set[str]] = {}
    for tool in load_manifest().tools:
        if tool.needed_by != "always":
            for language in tool.needed_by:
                needed.setdefault(language, set()).add(tool.name)
    assert needed == LANGUAGE_TOOLS


def test_every_tool_has_both_platforms_and_every_artifact_a_checksum_and_a_source() -> None:
    for tool in load_manifest().tools:
        assert set(tool.artifacts) == {"windows-x64", "linux-x64"}, tool.name
        for target, artifact in tool.artifacts.items():
            where = f"{tool.name} {target}"
            assert artifact.checksum_source.startswith("https://"), where
            assert artifact.size > 0, where


def test_a_shipped_tool_without_a_checksum_is_refused() -> None:
    text = resources.files("forja.toolchain").joinpath("manifest.toml").read_text("utf-8")
    first = next(line for line in text.splitlines() if line.startswith("checksum = "))
    assert "checksum" in problems(text.replace(first, "", 1))


def test_the_jdk_is_the_21_lts() -> None:
    jdk = next(t for t in load_manifest().tools if t.name == "jdk")
    assert jdk.version.startswith("21.")
    assert all(f"jdk-{jdk.version}/" in a.executable_path for a in jdk.artifacts.values())


def test_the_manifest_cannot_be_changed_after_it_is_read() -> None:
    manifest = parse_manifest(VALID)
    artifact = manifest.tools[0].artifacts["windows-x64"]
    for target, field in (
        (manifest, "schema_version"),
        (manifest.tools[0], "name"),
        (artifact, "size"),
    ):
        with pytest.raises(ValidationError):
            setattr(target, field, 2)


def test_dotnet_coverage_is_one_package_for_both_platforms_run_by_dotnet() -> None:
    (tool,) = [tool for tool in load_manifest().tools if tool.name == "dotnet-coverage"]
    windows, linux = tool.artifacts["windows-x64"], tool.artifacts["linux-x64"]

    assert windows == linux
    assert (windows.kind, windows.algorithm) == ("nupkg", "sha512")
    assert windows.url.endswith(f"/dotnet-coverage.{tool.version}.nupkg")
    assert windows.executable_path.endswith("/dotnet-coverage.dll")  # an assembly: dotnet runs it
    assert tool.license == "MIT"  # what GitHub gives for the repository the package names
