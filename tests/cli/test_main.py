"""The door of the Forja: what it says and returns before anything runs, in a real repository."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from forja.cli import detect as detect_module
from forja.cli import doctor as doctor_module
from forja.cli import main as door
from forja.cli import prepare as preparation
from forja.cli.args import parse
from forja.cli.detect import detect
from forja.cli.settings import Execucao, Interface, ModeloCli, Perfil, load_perfil, save_perfil
from forja.core.adapters.executor import locate_in_path
from forja.core.states import ExecutionState
from forja.executors import KNOWN_CLIS, ClaudeCodeAdapter
from forja.ui import terminal as ui_terminal
from forja.ui.full import FullRenderer
from forja.ui.glyphs import ASCII
from forja.ui.terminal import Capabilities
from tests.cli.test_resume import make_run

pytestmark = pytest.mark.integration


class Talk:
    """A console that answers from a list and remembers what was said."""

    def __init__(self, *answers: str, interactive: bool = False) -> None:
        self.interactive = interactive
        self.said: list[str] = []
        self._answers = list(answers)

    def say(self, text: str = "") -> None:
        self.said.append(text)

    def ask(self, question: str, *, default: str = "") -> str:
        return self._answers.pop(0) if self._answers else default

    @property
    def text(self) -> str:
        return "\n".join(self.said)


def git(repo: Path, *args: str) -> None:
    who = {"NAME": "t", "EMAIL": "t@e"}
    env = {**os.environ}
    for part in ("AUTHOR", "COMMITTER"):
        for key, value in who.items():
            env[f"GIT_{part}_{key}"] = value
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "proj"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    (path / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "0"\n', encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "init")
    monkeypatch.chdir(path)
    return path


@pytest.fixture
def environ(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A profile that knows where the agent is, and a Forja home of its own."""
    agent = tmp_path / ("claude.exe" if sys.platform == "win32" else "claude")
    agent.write_text("not a real agent", encoding="utf-8")
    agent.chmod(0o755)
    profile = tmp_path / "profile"
    save_perfil(profile, Perfil(cli="claude-code", executaveis={"claude-code": str(agent)}))

    async def proven(*args: object, **kwargs: object) -> str:
        return "2.1.0"

    monkeypatch.setattr(preparation, "prove", proven)
    monkeypatch.setattr(door, "prepare", preparation.prepare)
    env = {**os.environ, "FORJA_CONFIG_HOME": str(profile), "FORJA_HOME": str(tmp_path / "home")}
    monkeypatch.setenv("FORJA_HOME", env["FORJA_HOME"])
    return env


def go(argv: list[str], talk: Talk, environ: dict[str, str]) -> int:
    return asyncio.run(door.amain(parse(argv), talk, environ))


def test_the_plan_shows_what_would_happen_and_changes_nothing(
    repo: Path, environ: dict[str, str]
) -> None:
    talk = Talk()
    code = go(["refatorar", "--plano", "--escopo", "src"], talk, environ)
    assert code == 0
    assert "Vou refatorar proj" in talk.text
    assert "onde: src" in talk.text
    assert "2.000.000 tokens" in talk.text
    assert not (Path(environ["FORJA_HOME"]) / "runs").exists()


def test_the_plan_says_which_tools_would_be_installed(
    repo: Path, environ: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Wanted:
        name, version = "gitleaks", "8.0.0"

    monkeypatch.setattr(door, "missing_tools", lambda _environ, **_: [Wanted()])
    talk = Talk()
    assert go(["refatorar", "--plano", "--escopo", "src"], talk, environ) == 0
    assert "Faltam ferramentas: gitleaks 8.0.0" in talk.text


def test_an_executable_that_does_not_exist_is_explained_not_a_traceback(
    repo: Path, environ: dict[str, str], tmp_path: Path
) -> None:
    missing = tmp_path / "nao-existe"
    save_perfil(
        Path(environ["FORJA_CONFIG_HOME"]),
        Perfil(cli="claude-code", executaveis={"claude-code": str(missing)}),
    )
    talk = Talk()
    assert go(["refatorar", "--plano", "--escopo", "src"], talk, environ) == 2
    assert "O executável indicado (user_config) não existe" in talk.text
    assert str(missing) in talk.text


def test_unsaved_changes_stop_it_before_anything(repo: Path, environ: dict[str, str]) -> None:
    (repo / "novo.py").write_text("x = 1\n", encoding="utf-8")
    talk = Talk()
    assert go(["refatorar"], talk, environ) == 2
    assert "novo.py" in talk.text
    assert "commit ou stash" in talk.text


CRITERIA = (
    '{"criteria": [{"id": 1, "text": "page 1 is the first items", "level": "unit",'
    ' "origin": "task description"}, {"id": 2, "text": "all pages are the list",'
    ' "level": "unit", "origin": "task description"}]}'
)


def test_fixing_needs_a_description(repo: Path, environ: dict[str, str]) -> None:
    talk = Talk()
    assert go(["corrigir", "--plano"], talk, environ) == 2
    assert "Descreva o erro" in talk.text


def test_fixing_without_criteria_leaves_them_to_the_specifier(
    repo: Path, environ: dict[str, str]
) -> None:
    talk = Talk()
    assert go(["corrigir", "page 1 is wrong", "--plano"], talk, environ) == 0
    assert "o agente que especifica os escreve" in talk.text
    assert "Faltam os critérios" not in talk.text


def test_criteria_that_do_not_fit_the_contract_are_named(
    repo: Path, tmp_path: Path, environ: dict[str, str]
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"criteria": [{"id": 0, "text": "x", "level": "unit", "origin": "o"}]}')
    talk = Talk()
    assert go(["corrigir", "x is wrong", "--criterios", str(bad), "--plano"], talk, environ) == 2
    assert "não servem" in talk.text
    assert "criteria.0.id" in talk.text


def test_criteria_that_are_not_json_are_named(
    repo: Path, tmp_path: Path, environ: dict[str, str]
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    talk = Talk()
    assert go(["corrigir", "x is wrong", "--criterios", str(bad), "--plano"], talk, environ) == 2
    assert "não servem" in talk.text


def test_a_missing_criteria_file_is_named(
    repo: Path, tmp_path: Path, environ: dict[str, str]
) -> None:
    missing = tmp_path / "nope.json"
    talk = Talk()
    assert go(["corrigir", "x", "--criterios", str(missing), "--plano"], talk, environ) == 2
    assert "Não consegui ler os critérios" in talk.text


def test_the_fix_plan_shows_the_defect_and_the_criteria(
    repo: Path, tmp_path: Path, environ: dict[str, str]
) -> None:
    criteria = tmp_path / "c.json"
    criteria.write_text(CRITERIA, encoding="utf-8")
    talk = Talk()
    argv = ["corrigir", "page 1 is wrong", "--criterios", str(criteria), "--plano"]
    assert go(argv, talk, environ) == 0
    assert "Vou corrigir proj" in talk.text
    assert "erro: page 1 is wrong" in talk.text
    assert "critérios: 2" in talk.text


def test_a_test_author_cli_that_is_not_known_is_refused(
    repo: Path, tmp_path: Path, environ: dict[str, str]
) -> None:
    criteria = tmp_path / "c.json"
    criteria.write_text(CRITERIA, encoding="utf-8")
    talk = Talk()
    argv = ["corrigir", "x", "--criterios", str(criteria), "--cli-testes", "nenhuma", "--plano"]
    assert go(argv, talk, environ) == 2
    assert "Não conheço a CLI `nenhuma`" in talk.text


def test_a_manifest_in_a_subfolder_counts_as_a_language(
    repo: Path, tmp_path: Path, environ: dict[str, str]
) -> None:
    git(repo, "rm", "-q", "pyproject.toml")
    (repo / "svc").mkdir()
    (repo / "svc" / "go.mod").write_text("module svc\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "go in a subfolder")
    criteria = tmp_path / "c.json"
    criteria.write_text(CRITERIA, encoding="utf-8")
    talk = Talk()
    go(["corrigir", "x", "--criterios", str(criteria), "--plano"], talk, environ)
    assert "Não encontrei nenhuma linguagem" not in talk.text


def test_a_repository_with_no_language_is_refused(
    repo: Path, tmp_path: Path, environ: dict[str, str]
) -> None:
    git(repo, "rm", "-q", "pyproject.toml")
    (repo / "notes.txt").write_text("x", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "no language")
    criteria = tmp_path / "c.json"
    criteria.write_text(CRITERIA, encoding="utf-8")
    talk = Talk()
    assert go(["corrigir", "x", "--criterios", str(criteria), "--plano"], talk, environ) == 2
    assert "Não encontrei nenhuma linguagem" in talk.text


def test_the_suspended_gemini_cli_is_not_a_known_cli(
    repo: Path, tmp_path: Path, environ: dict[str, str]
) -> None:
    criteria = tmp_path / "c.json"
    criteria.write_text(CRITERIA, encoding="utf-8")
    talk = Talk()
    argv = ["corrigir", "x", "--criterios", str(criteria), "--cli", "gemini", "--plano"]
    assert go(argv, talk, environ) == 2
    assert "Não conheço a CLI `gemini`" in talk.text


def test_each_role_of_a_fix_can_run_on_its_own_cli(
    repo: Path, tmp_path: Path, environ: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(KNOWN_CLIS, "outra-cli", ClaudeCodeAdapter)
    agent = next(iter(load_perfil(Path(environ["FORJA_CONFIG_HOME"])).executaveis.values()))
    save_perfil(
        Path(environ["FORJA_CONFIG_HOME"]),
        Perfil(cli="claude-code", executaveis={"claude-code": agent, "outra-cli": agent}),
    )
    criteria = tmp_path / "c.json"
    criteria.write_text(CRITERIA, encoding="utf-8")
    talk = Talk()
    argv = ["corrigir", "x", "--criterios", str(criteria), "--cli-testes", "outra-cli", "--plano"]
    assert go(argv, talk, environ) == 0
    assert "agente que escreve os testes: outra-cli 2.1.0" in talk.text
    assert "agente que implementa: claude-code 2.1.0" in talk.text
    assert "agente que documenta: o mesmo que implementa, sem comandos" in talk.text


def test_the_model_is_the_clis_own_unless_one_is_asked_for_per_role(
    repo: Path, tmp_path: Path, environ: dict[str, str]
) -> None:
    criteria = tmp_path / "c.json"
    criteria.write_text(CRITERIA, encoding="utf-8")
    plain = Talk()
    assert go(["corrigir", "x", "--criterios", str(criteria), "--plano"], plain, environ) == 0
    assert "modelo" not in plain.text

    chosen = Talk()
    argv = ["corrigir", "x", "--criterios", str(criteria), "--modelo", "m1"]
    argv += ["--modelo-testes", "m2", "--plano"]
    assert go(argv, chosen, environ) == 0
    assert "agente que escreve os testes: claude-code 2.1.0, modelo m2" in chosen.text
    assert "agente que implementa: claude-code 2.1.0, modelo m1" in chosen.text


def test_the_description_file_brings_its_criteria_along(
    repo: Path, tmp_path: Path, environ: dict[str, str]
) -> None:
    (tmp_path / "criterios.json").write_text(CRITERIA, encoding="utf-8")
    (tmp_path / "erro.txt").write_text("  page 1 is wrong" + chr(10), encoding="utf-8")
    talk = Talk()
    assert go(["corrigir", "-f", str(tmp_path / "erro.txt"), "--plano"], talk, environ) == 0
    assert "erro: page 1 is wrong" in talk.text


def test_a_saved_fix_is_read_back_from_execucao(tmp_path: Path) -> None:
    criteria = tmp_path / "c.json"
    criteria.write_text(CRITERIA, encoding="utf-8")
    saved = Execucao(objetivo="corrigir", descricao="page 1 is wrong", criterios=str(criteria))
    request, description, where = preparation.read_fix(parse(["corrigir"]), saved)
    assert request.description == description == "page 1 is wrong"
    assert request.criteria is not None
    assert [c.id for c in request.criteria.criteria] == [1, 2]
    assert Path(where) == criteria.resolve()


def test_outside_a_repository_it_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environ: dict[str, str]
) -> None:
    empty = tmp_path / "vazio"
    empty.mkdir()
    (empty / "arquivo.txt").write_text("x", encoding="utf-8")
    monkeypatch.chdir(empty)
    talk = Talk()
    assert go(["refatorar"], talk, environ) == 2
    assert "repositório git" in talk.text


def test_an_unfinished_run_blocks_an_unattended_start(repo: Path, environ: dict[str, str]) -> None:
    make_run(Path(environ["FORJA_HOME"]), "r1", repo, to=ExecutionState.PAUSED)
    talk = Talk()
    assert go(["refatorar"], talk, environ) == 2
    assert "execução incompleta" in talk.text


def test_the_plan_is_shown_even_with_an_unfinished_run(repo: Path, environ: dict[str, str]) -> None:
    make_run(Path(environ["FORJA_HOME"]), "r1", repo, to=ExecutionState.PAUSED)
    talk = Talk()
    assert go(["refatorar", "--plano", "--escopo", "src"], talk, environ) == 0
    assert "Vou refatorar proj" in talk.text
    assert "execução incompleta" not in talk.text


def test_choosing_to_resume_opens_the_unfinished_run(
    repo: Path, environ: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    make_run(Path(environ["FORJA_HOME"]), "r1", repo, to=ExecutionState.PAUSED)
    opened: dict[str, object] = {}

    async def fake_open(config: object, **kwargs: object) -> str:
        opened.update(config=config, **kwargs)
        return "session"

    async def fake_run(session: object, renderer: object, console: object) -> int:
        return 0

    monkeypatch.setattr(door, "open_session", fake_open)
    monkeypatch.setattr(door, "_run", fake_run)
    monkeypatch.setattr(door, "renderer_for", lambda *_args: None)
    assert go(["refatorar"], Talk("1", interactive=True), environ) == 0
    assert opened == {"config": None, "run_id": "r1", "environ": environ, "interactive": True}


def test_without_a_terminal_and_without_a_config_file_nobody_confirms_and_nothing_starts(
    repo: Path, environ: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def never(*args: object, **kwargs: object) -> None:
        raise AssertionError("the run was opened with nobody to confirm it")

    monkeypatch.setattr(door, "open_session", never)
    monkeypatch.setattr(door, "missing_tools", lambda _environ, **_: [])
    talk = Talk()
    assert go(["refatorar", "--escopo", "src"], talk, environ) == 2
    assert "Sem terminal não há quem confirme" in talk.text
    assert "--config" in talk.text
    assert not (Path(environ["FORJA_HOME"]) / "runs").exists()


def test_a_run_from_a_config_file_opens_without_questions(
    repo: Path, environ: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace  # noqa: PLC0415

    opened: dict[str, object] = {}

    async def fake_open(config: object, **kwargs: object) -> SimpleNamespace:
        opened.update(kwargs)
        return SimpleNamespace(directory=tmp_path)

    async def fake_run(session: object, renderer: object, console: object) -> int:
        return 0

    monkeypatch.setattr(door, "open_session", fake_open)
    monkeypatch.setattr(door, "_run", fake_run)
    monkeypatch.setattr(door, "renderer_for", lambda *_args: None)
    monkeypatch.setattr(door, "missing_tools", lambda _environ, **_: [])
    answers = tmp_path / "e.toml"
    answers.write_text(
        'objetivo = "refatorar"\nescopo = ["src"]\ncli = "claude-code"\n', encoding="utf-8"
    )
    assert go(["--config", str(answers), "refatorar"], Talk(interactive=True), environ) == 0
    assert opened["interactive"] is False


def test_the_full_screen_is_chosen_when_the_terminal_has_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caps = Capabilities(True, "truecolor", True, 120, 36, "windows_terminal", True, True, False)
    monkeypatch.setattr(ui_terminal, "detect", lambda: caps)
    screen = door.renderer_for(Perfil(), None, Talk())
    assert isinstance(screen, FullRenderer)


def _screen_theme(
    monkeypatch: pytest.MonkeyPatch, *, tema: str, ascii: bool, unicode: bool
) -> object:
    caps = Capabilities(True, "truecolor", unicode, 120, 36, "windows_terminal", True, True, False)
    monkeypatch.setattr(ui_terminal, "detect", lambda: caps)
    seen: list[object] = []
    monkeypatch.setattr(door, "FullRenderer", lambda theme, *_a, **_k: seen.append(theme))
    profile = Perfil(interface=Interface(tema=tema, ascii=ascii))
    door.renderer_for(profile, None, Talk())
    return seen[0]


def test_the_theme_named_in_the_profile_is_the_one_drawn(monkeypatch: pytest.MonkeyPatch) -> None:
    theme = _screen_theme(monkeypatch, tema="dos", ascii=False, unicode=True)
    assert theme.key == "dos"  # type: ignore[attr-defined]


def test_plain_symbols_are_used_when_asked_or_when_the_terminal_cannot_draw_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _screen_theme(monkeypatch, tema="", ascii=True, unicode=True)
    cannot = _screen_theme(monkeypatch, tema="", ascii=False, unicode=False)
    neither = _screen_theme(monkeypatch, tema="", ascii=False, unicode=True)
    assert asked.glyphs is ASCII  # type: ignore[attr-defined]
    assert cannot.glyphs is ASCII  # type: ignore[attr-defined]
    assert neither.glyphs is not ASCII  # type: ignore[attr-defined]


def test_the_doctor_lists_what_is_missing_and_how_to_fix_it(
    repo: Path, environ: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor_module, "installed_clis", lambda *args, **kwargs: [])
    broken = {**environ, "FORJA_CONFIG_HOME": str(repo.parent / "sem-perfil"), "PATH": ""}
    talk = Talk()
    code = go(["doctor"], talk, broken)
    assert code == 1
    assert "[FALTA] CLI claude-code" in talk.text
    assert "->" in talk.text


def test_the_doctor_requires_jscpd_and_points_to_the_install(
    repo: Path, environ: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor_module, "installed_clis", lambda *args, **kwargs: [])
    real = locate_in_path
    monkeypatch.setattr(
        detect_module, "locate_in_path", lambda name, *args: None if name == "jscpd" else real(name)
    )
    talk = Talk()

    code = go(["doctor"], talk, {**environ, "FORJA_CONFIG_HOME": str(repo.parent / "sem-perfil")})

    assert code == 1
    assert not asyncio.run(detect(repo, os.environ)).jscpd
    assert "[FALTA] jscpd" in talk.text
    assert "https://github.com/kucherenko/jscpd" in talk.text


def test_jscpd_on_the_path_is_found(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = locate_in_path
    monkeypatch.setattr(
        detect_module,
        "locate_in_path",
        lambda name, *args: Path(sys.executable) if name == "jscpd" else real(name),
    )

    assert asyncio.run(detect(repo, os.environ)).jscpd


def test_an_invalid_settings_file_is_named(repo: Path, environ: dict[str, str]) -> None:
    bad = repo.parent / "e.toml"
    bad.write_text("tokenz = 1\n", encoding="utf-8")
    talk = Talk()
    assert go(["--config", str(bad), "refatorar"], talk, environ) == 2
    assert "tokenz" in talk.text


def commit_policy(repo: Path, text: str) -> None:
    (repo / "forja.toml").write_text(text, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "policy")


def test_a_forja_toml_with_errors_is_named_before_anything_runs(
    repo: Path, environ: dict[str, str]
) -> None:
    commit_policy(repo, "[checks.tests.commands]" + chr(10) + "nope = 1" + chr(10))
    talk = Talk()

    assert go(["refatorar", "--plano"], talk, environ) == 2
    assert "forja.toml" in talk.text
    assert "nope" in talk.text


def test_the_repositorys_own_policy_is_read_when_it_is_valid(
    repo: Path, environ: dict[str, str]
) -> None:
    commit_policy(
        repo, "[checks.tests.commands]" + chr(10) + 'test = ["python", "-m", "pytest"]' + chr(10)
    )
    talk = Talk()

    assert go(["refatorar", "--plano"], talk, environ) == 0


def test_what_the_repositorys_policy_changes_is_told_before_the_confirmation(
    repo: Path, environ: dict[str, str]
) -> None:
    commit_policy(
        repo,
        "[checks.secrets]" + chr(10) + 'mode = "observation"' + chr(10) + chr(10)
        + "[checks.tests.commands]" + chr(10) + 'test = ["python", "-m", "pytest"]' + chr(10),
    )  # fmt: skip
    talk = Talk()

    assert go(["refatorar", "--plano"], talk, environ) == 0
    assert "forja.toml do repositório muda o padrão" in talk.text
    assert "secrets: modo ou política diferente do padrão (observation" in talk.text
    assert "tests: roda no lugar do padrão (test): python -m pytest" in talk.text


def test_a_repository_without_a_policy_file_says_nothing_about_one(
    repo: Path, environ: dict[str, str]
) -> None:
    talk = Talk()

    assert go(["refatorar", "--plano"], talk, environ) == 0
    assert "forja.toml" not in talk.text


def test_everything_a_policy_can_loosen_is_listed_with_its_values(
    repo: Path, environ: dict[str, str]
) -> None:
    commit_policy(
        repo,
        "[checks.complexity.thresholds.python]" + chr(10) + "max_ccn = 40" + chr(10) + chr(10)
        + "[redaction]" + chr(10) + 'pii = ["email"]' + chr(10) + "entropy_threshold = 4.0"
        + chr(10) + 'allow = ["abc"]' + chr(10),
    )  # fmt: skip
    talk = Talk()

    assert go(["refatorar", "--plano"], talk, environ) == 0
    assert "complexity: limite max_ccn de python = 40" in talk.text
    assert "detectores de dados pessoais desligados" in talk.text
    assert "limiar de entropia mais alto (4.0)" in talk.text
    assert "valores liberados pelo repositório" in talk.text


def test_the_doctor_warns_when_golangci_lint_is_missing_but_does_not_require_it(
    tmp_path: Path,
) -> None:
    only_go = tmp_path / "bin"
    only_go.mkdir()
    program = only_go / ("go.exe" if sys.platform == "win32" else "go")
    program.write_text("x", encoding="utf-8")
    program.chmod(0o755)

    found, lint = doctor_module._go_checks({"PATH": str(only_go)})  # noqa: SLF001

    assert found.ok
    assert not lint.ok
    assert not lint.required
    assert "não suportado" in lint.detail
    assert "golangci-lint.run" in lint.fix


def test_the_doctor_warns_when_mypy_is_missing_but_does_not_require_it(tmp_path: Path) -> None:
    check = doctor_module._mypy_check({"PATH": str(tmp_path)})  # noqa: SLF001

    assert not check.ok
    assert not check.required
    assert "inconclusiva" in check.detail
    assert "forja instalar" in check.fix


def test_the_doctor_finds_mypy_on_the_path(tmp_path: Path) -> None:
    program = tmp_path / ("mypy.exe" if sys.platform == "win32" else "mypy")
    program.write_text("x", encoding="utf-8")
    program.chmod(0o755)

    check = doctor_module._mypy_check({"PATH": str(tmp_path)})  # noqa: SLF001

    assert check.ok
    assert check.fix == ""


def test_the_doctor_requires_node_for_a_typescript_project(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    program = bin_dir / ("node.exe" if sys.platform == "win32" else "node")
    program.write_text("x", encoding="utf-8")
    program.chmod(0o755)

    (found,) = doctor_module._node_checks({"PATH": str(bin_dir)})  # noqa: SLF001
    (missing,) = doctor_module._node_checks({"PATH": str(tmp_path)})  # noqa: SLF001

    assert found.ok
    assert not missing.ok
    assert missing.required
    assert "nodejs.org" in missing.fix


def test_the_doctor_requires_dotnet_for_a_csharp_project(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    program = bin_dir / ("dotnet.exe" if sys.platform == "win32" else "dotnet")
    program.write_text("x", encoding="utf-8")
    program.chmod(0o755)

    (found,) = doctor_module._dotnet_checks({"PATH": str(bin_dir)})  # noqa: SLF001
    (missing,) = doctor_module._dotnet_checks({"PATH": str(tmp_path)})  # noqa: SLF001

    assert found.ok
    assert not missing.ok
    assert missing.required
    assert "dotnet.microsoft.com" in missing.fix


def test_the_doctor_requires_java_and_maven_for_a_java_project(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    suffix = ".exe" if sys.platform == "win32" else ""
    program = bin_dir / f"java{suffix}"
    program.write_text("x", encoding="utf-8")
    program.chmod(0o755)

    java, maven = doctor_module._java_checks({"PATH": str(bin_dir)})  # noqa: SLF001

    assert java.ok
    assert not maven.ok
    assert maven.required
    assert "maven.apache.org" in maven.fix


def test_the_doctor_asks_for_gradle_when_only_a_gradle_build_file_is_there(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "build.gradle").write_text("", encoding="utf-8")

    java, gradle = doctor_module._java_checks({"PATH": str(tmp_path)}, repo)  # noqa: SLF001

    assert not java.ok
    assert (gradle.name, gradle.ok, gradle.required) == ("gradle", False, True)
    assert "gradle.org" in gradle.fix


def test_the_doctor_accepts_the_wrapper_of_the_repository_as_gradle(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "build.gradle.kts").write_text("", encoding="utf-8")
    wrapper = "gradlew.bat" if sys.platform == "win32" else "gradlew"
    (repo / wrapper).write_text("x", encoding="utf-8")

    _, gradle = doctor_module._java_checks({"PATH": str(tmp_path)}, repo)  # noqa: SLF001

    assert gradle.ok


@pytest.mark.parametrize("manifest", ["pom.xml", "build.gradle", "build.gradle.kts"])
def test_a_java_manifest_at_the_root_is_a_java_project(repo: Path, manifest: str) -> None:
    assert not asyncio.run(detect(repo, os.environ)).java
    (repo / manifest).write_text("", encoding="utf-8")

    assert asyncio.run(detect(repo, os.environ)).java


def test_the_pom_wins_over_a_gradle_build_file_in_the_doctor(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pom.xml").write_text("", encoding="utf-8")
    (repo / "build.gradle").write_text("", encoding="utf-8")

    _, tool = doctor_module._java_checks({"PATH": str(tmp_path)}, repo)  # noqa: SLF001

    assert tool.name == "maven"


def test_a_facts_file_is_read_and_counted(
    repo: Path, tmp_path: Path, environ: dict[str, str]
) -> None:
    facts = tmp_path / "fatos.txt"
    facts.write_text("F1 | Ana | 2026-10-20 | the page size is 10\n", encoding="utf-8")
    talk = Talk()
    assert go(["refatorar", "--plano", "--fatos", str(facts)], talk, environ) == 0
    assert "fatos registrados por você: 1" in talk.text


def test_a_facts_file_with_a_bad_line_stops_the_preparation(
    repo: Path, tmp_path: Path, environ: dict[str, str]
) -> None:
    facts = tmp_path / "fatos.txt"
    facts.write_text("F1 | Ana | ontem | x\n", encoding="utf-8")
    talk = Talk()
    assert go(["refatorar", "--plano", "--fatos", str(facts)], talk, environ) == 2
    assert "não servem" in talk.text
    assert "linha 1" in talk.text


def test_a_missing_facts_file_is_named(repo: Path, tmp_path: Path, environ: dict[str, str]) -> None:
    talk = Talk()
    assert go(["refatorar", "--plano", "--fatos", str(tmp_path / "nada.txt")], talk, environ) == 2
    assert "Não consegui ler os fatos" in talk.text


def test_investigating_needs_a_question(repo: Path, environ: dict[str, str]) -> None:
    talk = Talk()
    assert go(["investigar", "--plano"], talk, environ) == 2
    assert "Faça a pergunta" in talk.text


def test_the_investigation_plan_shows_the_question_and_who_answers(
    repo: Path, environ: dict[str, str]
) -> None:
    talk = Talk()
    assert go(["investigar", "how does paging work?", "--plano"], talk, environ) == 0
    assert "Vou investigar proj" in talk.text
    assert "pergunta: how does paging work?" in talk.text
    assert "agente que investiga" in talk.text
    assert "erro:" not in talk.text


def test_the_question_can_come_from_a_file(
    repo: Path, tmp_path: Path, environ: dict[str, str]
) -> None:
    (tmp_path / "pergunta.txt").write_text("  how does paging work?" + chr(10), encoding="utf-8")
    talk = Talk()
    assert go(["investigar", "-f", str(tmp_path / "pergunta.txt"), "--plano"], talk, environ) == 0
    assert "pergunta: how does paging work?" in talk.text


def test_a_saved_investigation_is_read_back_from_execucao() -> None:
    saved = Execucao(objetivo="investigar", descricao="how does paging work?")
    assert preparation.read_question(parse(["investigar"]), saved) == "how does paging work?"


def test_documenting_is_an_investigation_and_has_no_verb_of_its_own() -> None:
    with pytest.raises(SystemExit):
        parse(["documentar"])


def capture_prepared(monkeypatch: pytest.MonkeyPatch) -> list[preparation.Prepared]:
    seen: list[preparation.Prepared] = []

    async def wrapped(*args: object, **kwargs: object) -> preparation.Prepared:
        prepared = await preparation.prepare(*args, **kwargs)  # type: ignore[arg-type]
        seen.append(prepared)
        return prepared

    monkeypatch.setattr(door, "prepare", wrapped)
    return seen


def install_clis(environ: dict[str, str], *names: str, **generic: ModeloCli) -> None:
    home = Path(environ["FORJA_CONFIG_HOME"])
    agent = next(iter(load_perfil(home).executaveis.values()))
    save_perfil(
        home,
        Perfil(
            cli="claude-code", executaveis={n: agent for n in ("claude-code", *names)}, clis=generic
        ),
    )


def test_a_generic_cli_as_test_author_is_remembered_for_a_resumed_run(
    repo: Path, tmp_path: Path, environ: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    install_clis(environ, "meu", meu=ModeloCli(comando=["{executable}", "{package}"]))
    seen = capture_prepared(monkeypatch)
    criteria = tmp_path / "c.json"
    criteria.write_text(CRITERIA, encoding="utf-8")
    argv = ["corrigir", "x", "--criterios", str(criteria), "--cli-testes", "meu", "--plano"]
    assert go(argv, Talk(), environ) == 0
    prepared = seen[0]
    assert prepared.config.test_author is not None
    assert prepared.config.test_author.template is not None
    assert prepared.execucao.cli_testes == "meu"
    assert set(prepared.execucao.clis) == {"meu"}


def test_another_cli_is_suggested_only_when_the_test_author_is_the_implementers(
    repo: Path, tmp_path: Path, environ: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(KNOWN_CLIS, "outra-cli", ClaudeCodeAdapter)
    install_clis(environ, "outra-cli")
    criteria = tmp_path / "c.json"
    criteria.write_text(CRITERIA, encoding="utf-8")
    base = ["corrigir", "x", "--criterios", str(criteria), "--plano"]

    alone = Talk()
    assert go(base, alone, environ) == 0
    assert "sugestão:" in alone.text

    other = Talk()
    assert go([*base, "--cli-testes", "outra-cli"], other, environ) == 0
    assert "sugestão:" not in other.text

    refactoring = Talk()
    assert go(["refatorar", "--plano"], refactoring, environ) == 0
    assert "sugestão:" not in refactoring.text


def test_another_cli_is_suggested_when_only_the_model_of_the_tests_differs(
    repo: Path, tmp_path: Path, environ: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(KNOWN_CLIS, "outra-cli", ClaudeCodeAdapter)
    install_clis(environ, "outra-cli")
    criteria = tmp_path / "c.json"
    criteria.write_text(CRITERIA, encoding="utf-8")
    talk = Talk()
    argv = ["corrigir", "x", "--criterios", str(criteria), "--modelo-testes", "m2", "--plano"]
    assert go(argv, talk, environ) == 0
    assert "sugestão:" in talk.text
