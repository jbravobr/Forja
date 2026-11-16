from pathlib import Path

import pytest

from forja.cli import session
from forja.cli.session import languages, repo_python
from forja.core.adapters import LanguageCapability


def interpreter_of(registry: object) -> tuple[str, ...]:
    project = registry.project("python")  # type: ignore[attr-defined]
    return project._adapter._python  # type: ignore[no-any-return]


def test_a_python_project_uses_the_interpreter_the_run_gives_it_when_it_is_made(
    tmp_path: Path,
) -> None:
    current = [("one",)]
    registry = languages(tmp_path, lambda: current[0])

    assert interpreter_of(registry) == ("one",)
    current[0] = ("two",)
    assert interpreter_of(registry) == ("two",)


def test_without_an_interpreter_from_the_run_a_python_project_uses_the_repositorys(
    tmp_path: Path,
) -> None:
    registry = languages(tmp_path)

    assert interpreter_of(registry) == repo_python(tmp_path)


@pytest.mark.parametrize("found", [("C:/tools/dotnet-coverage.exe",), None])
def test_a_csharp_project_finds_the_tests_of_each_line_only_with_the_users_dotnet_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, found: tuple[str, ...] | None
) -> None:
    monkeypatch.setattr(session, "find_dotnet_coverage", lambda: found)

    registry = languages(tmp_path)

    for adapter in (registry.project("csharp")._adapter, registry.language("csharp")):
        offered = LanguageCapability.TEST_LINES in adapter.capabilities
        assert offered is (found is not None)
