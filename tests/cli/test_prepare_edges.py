"""Edges of the preparation nothing else pinned: what it reads, refuses and takes for granted."""

import asyncio
import dataclasses
from pathlib import Path

import pytest

from forja.cli import prepare as preparation
from forja.cli.args import parse
from forja.cli.detect import Detection, detect
from forja.cli.prepare import Prepared, Refused, check_repository
from forja.cli.security_notes import NO_DIFFERENTIAL
from forja.cli.session import LOW_DISK_GIB
from forja.cli.settings import load_perfil
from tests.cli.test_main import CRITERIA, Talk, environ, go, repo

__all__ = ["environ", "repo"]  # the fixtures of the door, used here too

pytestmark = pytest.mark.integration


def prepared(argv: list[str], environ: dict[str, str], *, disk: float | None = None) -> Prepared:
    async def run() -> Prepared:
        detection = await detect(Path.cwd(), environ)
        if disk is not None:
            detection = dataclasses.replace(detection, disk_free_gib=disk)
        home = Path(environ["FORJA_CONFIG_HOME"])
        return await preparation.prepare(
            parse(argv), detection, load_perfil(home), home, Talk(), environ
        )

    return asyncio.run(run())


def test_only_a_refactoring_is_told_the_differential_test_did_not_run(
    repo: Path, environ: dict[str, str]
) -> None:
    refactoring, fixing, asking = Talk(), Talk(), Talk()

    assert go(["refatorar", "--plano"], refactoring, environ) == 0
    assert go(["corrigir", "page 1 is wrong", "--plano"], fixing, environ) == 0
    assert go(["investigar", "why is page 1 wrong?", "--plano"], asking, environ) == 0

    assert NO_DIFFERENTIAL in refactoring.text
    assert NO_DIFFERENTIAL not in fixing.text
    assert NO_DIFFERENTIAL not in asking.text


def test_with_no_file_to_say_otherwise_the_agent_runs_the_tests(
    repo: Path, environ: dict[str, str]
) -> None:
    found = prepared(["refatorar"], environ)

    assert found.execucao.agente_roda_testes is True
    assert found.config.agent_runs_tests is True


def test_a_refactoring_has_no_test_author_even_with_a_model_for_the_tests(
    repo: Path, environ: dict[str, str]
) -> None:
    found = prepared(["refatorar", "--modelo-testes", "m2"], environ)

    assert found.config.test_author is None
    assert found.execucao.cli_testes == ""


def test_a_fix_with_no_cli_and_no_model_for_the_tests_has_no_test_author_of_its_own(
    repo: Path, tmp_path: Path, environ: dict[str, str]
) -> None:
    criteria = tmp_path / "c.json"
    criteria.write_text(CRITERIA, encoding="utf-8")

    found = prepared(["corrigir", "x", "--criterios", str(criteria)], environ)

    assert found.config.test_author is None


def test_a_fix_with_only_a_model_for_the_tests_has_the_same_cli_as_test_author(
    repo: Path, tmp_path: Path, environ: dict[str, str]
) -> None:
    criteria = tmp_path / "c.json"
    criteria.write_text(CRITERIA, encoding="utf-8")

    found = prepared(
        ["corrigir", "x", "--criterios", str(criteria), "--modelo-testes", "m2"], environ
    )

    assert found.config.test_author is not None
    assert found.config.test_author.name == found.config.implementer.name
    assert found.config.test_author.model == "m2"


def test_exactly_the_least_disk_is_enough_and_a_little_less_is_not(
    repo: Path, environ: dict[str, str]
) -> None:
    assert prepared(["refatorar"], environ, disk=float(LOW_DISK_GIB)).config.repo

    with pytest.raises(Refused, match="GiB livres"):
        prepared(["refatorar"], environ, disk=LOW_DISK_GIB - 0.01)


def detection_with(tmp_path: Path, **languages: bool) -> Detection:
    return Detection(
        cwd=tmp_path,
        repo=tmp_path,
        has_commits=True,
        branch="main",
        head="a" * 40,
        changed=(),
        gitleaks=True,
        git=True,
        disk_free_gib=100.0,
        **{"python": False, **languages},
    )


@pytest.mark.parametrize("language", ["python", "go", "typescript", "csharp", "java"])
def test_one_language_alone_is_enough_for_a_repository(tmp_path: Path, language: str) -> None:
    assert check_repository(detection_with(tmp_path, **{language: True})) == tmp_path


def test_a_repository_with_no_language_at_all_is_refused(tmp_path: Path) -> None:
    with pytest.raises(Refused, match="nenhuma linguagem"):
        check_repository(detection_with(tmp_path))


@pytest.mark.parametrize(
    ("directory", "name"),
    [
        ("Meu Projeto", "meu-projeto"),
        ("api_v2", "api-v2"),
        ("a" * 40, "a" * 40),
        ("a" * 41, ""),
        ("9lives", ""),
        ("---", ""),
    ],
)
def test_the_name_of_the_directory_is_a_project_name_only_when_it_can_be_one(
    directory: str, name: str
) -> None:
    assert preparation._project_name_of(Path(directory)) == name  # noqa: SLF001


def test_a_file_that_is_not_utf8_is_refused_saying_so(tmp_path: Path) -> None:
    file = tmp_path / "erro.txt"
    file.write_bytes(b"\xff\xfe\xfa")

    with pytest.raises(Refused, match="a pergunta .*: não é UTF-8"):
        preparation._read_text(file, "a pergunta")  # noqa: SLF001


def test_a_file_that_is_not_there_is_refused_with_what_the_system_said(tmp_path: Path) -> None:
    with pytest.raises(Refused) as refusal:
        preparation._read_text(tmp_path / "nao-existe.txt", "a pergunta")  # noqa: SLF001

    assert "Não consegui ler a pergunta" in refusal.value.message
    assert "não é UTF-8" not in refusal.value.message
