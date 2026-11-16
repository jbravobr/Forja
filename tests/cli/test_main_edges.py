"""The door of the Forja, the parts the whole-run tests do not reach: the screen it picks, what it
remembers, how a run closes, and what each error of the command becomes."""

import asyncio
import contextlib
import io
import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from forja.cli import main as door
from forja.cli.args import parse
from forja.cli.run import Ended, ForcedExit
from forja.cli.session import PolicyChanged
from forja.cli.settings import Perfil, SettingsError, load_perfil, save_perfil
from forja.core.locking.repository import RepositoryBusy
from forja.orchestrator.outcome import ExitCode
from forja.ui import terminal as ui_terminal
from forja.ui.full import FullRenderer
from forja.ui.terminal import Capabilities
from forja.ui.theme import THEMES


class Talk:
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


CAPS = Capabilities(True, "truecolor", True, 120, 36, "xterm", True, True, False)


def test_a_small_window_gets_the_live_line_and_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    small = Capabilities(True, "truecolor", True, 40, 10, "xterm", True, True, False)
    monkeypatch.setattr(ui_terminal, "detect", lambda: small)
    monkeypatch.setattr(door, "LiveRenderer", lambda theme, caps: ("live", caps))
    talk = Talk()

    screen = door.renderer_for(Perfil(), None, talk)

    assert screen == ("live", small)  # type: ignore[comparison-overlap]
    assert talk.said == ["a janela está pequena (40x10); a tela completa pede ao menos 60x16"]


def test_what_the_person_chose_to_remember_is_saved_only_when_confirmed(tmp_path: Path) -> None:
    prepared = SimpleNamespace(remember=("codex", "/bin/codex"))
    profile = Perfil()

    door._remember(prepared, profile, Talk("n"), tmp_path)  # type: ignore[arg-type]  # noqa: SLF001
    assert load_perfil(tmp_path) == Perfil()

    door._remember(prepared, profile, Talk("s"), tmp_path)  # type: ignore[arg-type]  # noqa: SLF001
    saved = load_perfil(tmp_path)
    assert (saved.cli, saved.executaveis) == ("codex", {"codex": "/bin/codex"})


def test_nothing_is_asked_when_there_is_nothing_to_remember(tmp_path: Path) -> None:
    talk = Talk("s")

    door._remember(SimpleNamespace(remember=None), Perfil(), talk, tmp_path)  # type: ignore[arg-type]  # noqa: SLF001

    assert talk._answers == ["s"]  # noqa: SLF001
    assert not (tmp_path / "config.toml").exists()


def test_the_end_of_a_run_says_the_closing_lines_and_gives_the_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(door, "closing_lines", lambda *_a: ["um", "dois"])
    monkeypatch.setattr(door, "exit_code", lambda *_a: 7)
    talk = Talk()

    assert door._finish("s", "e", talk) == 7  # type: ignore[arg-type]  # noqa: SLF001
    assert talk.said == ["um", "dois"]


class Keys:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def awake(monkeypatch: pytest.MonkeyPatch) -> Keys:
    keys = Keys()

    @contextlib.contextmanager
    def quiet() -> Iterator[bool]:
        yield False

    monkeypatch.setattr(door, "keep_awake", quiet)
    monkeypatch.setattr(door, "terminal_keys", lambda: keys)
    return keys


def execute_giving(result: object) -> Any:  # noqa: ANN401
    async def run(*_args: object) -> object:
        if isinstance(result, BaseException):
            raise result
        return result

    return run


def test_a_run_that_ends_is_closed_and_reported(
    monkeypatch: pytest.MonkeyPatch, awake: Keys
) -> None:
    monkeypatch.setattr(door, "execute", execute_giving(Ended(None, interrupted=False)))
    monkeypatch.setattr(door, "closing_lines", lambda *_a: ["fim"])
    monkeypatch.setattr(door, "exit_code", lambda *_a: 3)
    session, talk = Session(), Talk()

    code = asyncio.run(door._run(session, object(), talk))  # type: ignore[arg-type]  # noqa: SLF001

    assert code == 3
    assert talk.said == ["fim"]
    assert session.closed and awake.closed


def test_a_run_left_without_waiting_says_so_and_exits_interrupted(
    monkeypatch: pytest.MonkeyPatch, awake: Keys
) -> None:
    monkeypatch.setattr(door, "execute", execute_giving(ForcedExit()))
    session, talk = Session(), Talk()

    code = asyncio.run(door._run(session, object(), talk))  # type: ignore[arg-type]  # noqa: SLF001

    assert code == int(ExitCode.INTERRUPTED)
    assert talk.said == [
        "Saiu sem esperar. O que já estava gravado está salvo; rode `forja` para retomar."
    ]
    assert session.closed and awake.closed


def full_screen(monkeypatch: pytest.MonkeyPatch, seen: list[str]) -> FullRenderer:
    screen = FullRenderer(THEMES["escuro"], CAPS, out=io.StringIO(), headless=True)

    async def closed() -> None:
        seen.append("closed")

    async def finish(lines: list[str]) -> None:
        seen.append("finish " + "|".join(lines))

    monkeypatch.setattr(screen, "abort", lambda: seen.append("abort"))
    monkeypatch.setattr(screen, "closed", closed)
    monkeypatch.setattr(screen, "finish", finish)
    return screen


def test_a_forced_exit_aborts_the_full_screen_before_saying_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    screen = full_screen(monkeypatch, seen)
    monkeypatch.setattr(door, "keep_awake", contextlib.nullcontext)
    monkeypatch.setattr(door, "execute", execute_giving(ForcedExit()))
    monkeypatch.setattr(screen.keys_source, "close", lambda: seen.append("keys"))
    session, talk = Session(), Talk()

    code = asyncio.run(door._run(session, screen, talk))  # type: ignore[arg-type]  # noqa: SLF001

    assert code == int(ExitCode.INTERRUPTED)
    assert seen == ["abort", "closed", "keys"]
    assert talk.said and talk.said[0].startswith("Saiu sem esperar")


def test_a_run_on_the_full_screen_shows_the_report_there_and_then_says_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    screen = full_screen(monkeypatch, seen)
    monkeypatch.setattr(door, "keep_awake", contextlib.nullcontext)
    monkeypatch.setattr(door, "execute", execute_giving(Ended(None, interrupted=False)))
    monkeypatch.setattr(door, "closing_lines", lambda *_a: ["a", "b"])
    monkeypatch.setattr(door, "exit_code", lambda *_a: 0)
    monkeypatch.setattr(screen.keys_source, "close", lambda: seen.append("keys"))
    talk = Talk()

    assert asyncio.run(door._run(Session(), screen, talk)) == 0  # type: ignore[arg-type]  # noqa: SLF001

    assert seen == ["keys", "finish a|b"]
    assert talk.said == ["a", "b"]


def test_a_full_screen_that_breaks_is_told_and_does_not_lose_the_result() -> None:
    async def broken() -> None:
        raise RuntimeError("boom")

    talk = Talk()

    asyncio.run(door._screen(broken(), talk))  # noqa: SLF001

    assert talk.said == ["A tela completa falhou (boom); o resultado da execução continua valendo."]


def test_a_discarded_run_says_which_and_how_to_clean_its_branches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dropped: list[Path] = []
    monkeypatch.setattr(door, "discard", dropped.append)
    monkeypatch.setattr(door, "removal_commands", lambda run_id: [f"git branch -D {run_id}"])
    talk = Talk()

    asyncio.run(door._discard(SimpleNamespace(directory=tmp_path, run_id="r9"), talk))  # type: ignore[arg-type]  # noqa: SLF001

    assert dropped == [tmp_path]
    assert talk.said == [
        "Execução r9 descartada. Nada foi apagado. Para limpar as branches:",
        "  git branch -D r9",
    ]


def start_with_a_pending_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, choice: str
) -> tuple[Talk, list[str]]:
    from forja.cli.prepare import Refused  # noqa: PLC0415

    steps: list[str] = []
    pending = [SimpleNamespace(run_id="r1", state="paused", reason="", directory=tmp_path)]
    monkeypatch.setattr(door, "find_unfinished", lambda *_a: pending)

    async def discarded(latest: object, console: object) -> None:
        steps.append("discard")

    async def prepared(*_a: object, **_k: object) -> object:
        steps.append("prepare")
        raise Refused("parou aqui")

    monkeypatch.setattr(door, "_discard", discarded)
    monkeypatch.setattr(door, "prepare", prepared)
    monkeypatch.setattr(door, "profile_dir", lambda _environ: tmp_path)
    talk = Talk(choice, interactive=True)
    detection = SimpleNamespace(repo=tmp_path)
    args = parse(["refatorar"])
    with pytest.raises(Refused):
        asyncio.run(door.start(args, talk, {}, detection))  # type: ignore[arg-type]
    return talk, steps


def test_choosing_to_discard_the_unfinished_run_discards_it_and_goes_on_to_prepare(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    talk, steps = start_with_a_pending_run(monkeypatch, tmp_path, "2")

    assert steps == ["discard", "prepare"]
    assert "execução incompleta" in talk.text


@pytest.mark.parametrize(
    ("argv", "name", "code"),
    [(["instalar"], "instalar", 4), (["agente", "testar"], "run_facade", 5)],
)
def test_the_commands_that_do_not_run_are_handed_to_their_own_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
    name: str,
    code: int,
) -> None:
    calls: list[str] = []

    async def detected(*_a: object) -> object:
        return SimpleNamespace(repo=None)

    async def instalar(*_a: object, **_k: object) -> int:
        calls.append("instalar")
        return code

    async def facade(*_a: object) -> int:
        calls.append("run_facade")
        return code

    monkeypatch.setattr(door, "detect", detected)
    monkeypatch.setattr(door, "load_perfil", lambda _home: Perfil())
    monkeypatch.setattr(door, "profile_dir", lambda _environ: tmp_path)
    monkeypatch.setattr(door, "instalar", instalar)
    monkeypatch.setattr(door, "run_facade", facade)

    assert asyncio.run(door.amain(parse(argv), Talk(), {})) == code
    assert calls == [name]


@pytest.mark.parametrize(
    ("error", "said"),
    [
        (PolicyChanged("o forja.toml mudou"), "o forja.toml mudou"),
        (SettingsError("config.toml: tema"), "config.toml: tema"),
        (
            RepositoryBusy(None, "locked"),
            "Já há uma execução da Forja em andamento neste repositório.",
        ),
    ],
)
def test_an_error_of_the_command_is_said_and_exits_as_an_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch, error: Exception, said: str
) -> None:
    async def detected(*_a: object) -> object:
        return SimpleNamespace(repo=None)

    async def start(*_a: object) -> int:
        raise error

    monkeypatch.setattr(door, "detect", detected)
    monkeypatch.setattr(door, "start", start)
    talk = Talk()

    assert asyncio.run(door.amain(parse([]), talk, {})) == int(ExitCode.INVALID_CONFIG)
    assert talk.said == [said]


def test_main_returns_what_the_command_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def amain(*_a: object) -> int:
        return 5

    monkeypatch.setattr(door, "amain", amain)
    monkeypatch.setattr(door, "with_installed_tools", lambda environ: dict(environ))
    monkeypatch.setattr(os, "environ", {})

    assert door.main(["doctor"]) == 5


def test_main_exits_interrupted_on_a_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    async def amain(*_a: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(door, "amain", amain)
    monkeypatch.setattr(door, "with_installed_tools", lambda environ: dict(environ))
    monkeypatch.setattr(os, "environ", {})

    assert door.main(["doctor"]) == int(ExitCode.INTERRUPTED)


def test_a_profile_that_is_not_valid_is_explained_with_its_path(tmp_path: Path) -> None:
    save_perfil(tmp_path, Perfil())
    (tmp_path / "config.toml").write_text('cli = ["not", "a", "name"]\n', encoding="utf-8")

    with pytest.raises(SettingsError) as error:
        load_perfil(tmp_path)

    assert str(tmp_path / "config.toml") in str(error.value)
    assert "cli" in str(error.value)
