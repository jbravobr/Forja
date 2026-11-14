"""The terminal keys a run understands, by the character the terminal sends."""

import asyncio
import sys
import types
from typing import Any

import pytest

from forja.cli import keys as keys_module
from forja.cli.keys import TerminalKeys, terminal_keys


@pytest.mark.parametrize(
    ("char", "name"),
    [("\x0b", "ctrl+k"), ("\x03", "ctrl+c"), ("q", "q"), ("Q", "q"), ("t", "t"), ("T", "t")],
)
def test_a_known_character_reaches_the_run_by_its_name(char: str, name: str) -> None:
    async def go() -> str:
        keys = TerminalKeys()
        keys._loop = asyncio.get_running_loop()
        keys._put(char)
        return await asyncio.wait_for(keys._queue.get(), 1)

    assert asyncio.run(go()) == name


def test_an_unknown_character_is_ignored() -> None:
    async def go() -> bool:
        keys = TerminalKeys()
        keys._loop = asyncio.get_running_loop()
        keys._put("x")
        await asyncio.sleep(0)
        return keys._queue.empty()

    assert asyncio.run(go())


def fake_msvcrt(keys: TerminalKeys, typed: str) -> types.ModuleType:
    """A console that has ``typed`` waiting, then nothing, and stops the reader when empty."""
    pending = list(typed)
    module = types.ModuleType("msvcrt")

    def kbhit() -> bool:
        if not pending:
            keys._stop.set()
        return bool(pending)

    module.kbhit = kbhit  # type: ignore[attr-defined]
    module.getwch = lambda: pending.pop(0)  # type: ignore[attr-defined]
    return module


def drain(keys: TerminalKeys) -> list[str]:
    out = []
    while not keys._queue.empty():
        out.append(keys._queue.get_nowait())
    return out


def test_on_windows_the_console_keys_are_read_until_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def go() -> list[str]:
        keys = TerminalKeys()
        keys._loop = asyncio.get_running_loop()
        monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt(keys, "qxt"))
        monkeypatch.setattr(sys, "platform", "win32")
        keys._read()
        await asyncio.sleep(0)
        return drain(keys)

    assert asyncio.run(go()) == ["q", "t"]


def test_elsewhere_the_terminal_is_read_in_cbreak_and_put_back_as_it_was(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []
    typed = list("Qk")

    termios = types.ModuleType("termios")
    termios.TCSADRAIN = 1  # type: ignore[attr-defined]

    def tcgetattr(fd: int) -> str:
        calls.append(("get", fd))
        return "saved"

    termios.tcgetattr = tcgetattr  # type: ignore[attr-defined]
    termios.tcsetattr = lambda fd, when, saved: calls.append(("set", fd, when, saved))  # type: ignore[attr-defined]
    tty = types.ModuleType("tty")
    tty.setcbreak = lambda fd: calls.append(("cbreak", fd))  # type: ignore[attr-defined]
    select = types.ModuleType("select")

    async def go() -> list[str]:
        keys = TerminalKeys()
        keys._loop = asyncio.get_running_loop()

        def ready(read: list[int], write: list[int], error: list[int], wait: float) -> Any:
            if not typed:
                keys._stop.set()
                return ([], [], [])
            return (read, [], [])

        select.select = ready  # type: ignore[attr-defined]
        stdin = types.SimpleNamespace(
            fileno=lambda: 7, read=lambda size: typed.pop(0) if size == 1 else ""
        )
        for name, module in (("termios", termios), ("tty", tty), ("select", select)):
            monkeypatch.setitem(sys.modules, name, module)
        monkeypatch.setattr(sys, "stdin", stdin)
        monkeypatch.setattr(sys, "platform", "linux")
        keys._read()
        await asyncio.sleep(0)
        return drain(keys)

    assert asyncio.run(go()) == ["q"]
    assert calls == [("get", 7), ("cbreak", 7), ("set", 7, 1, "saved")]


def test_the_reader_runs_in_a_thread_hands_the_keys_over_and_stops_on_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def go() -> str:
        keys = TerminalKeys()
        typed = ["t"]
        module = types.ModuleType("msvcrt")
        module.kbhit = lambda: bool(typed)  # type: ignore[attr-defined]
        module.getwch = lambda: typed.pop(0)  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "msvcrt", module)
        monkeypatch.setattr(sys, "platform", "win32")
        keys.start()
        assert keys._thread is not None and keys._thread.daemon
        first = await asyncio.wait_for(anext(aiter(keys.keys())), 2)
        keys.close()
        assert keys._thread is not None and not keys._thread.is_alive()
        return first

    assert asyncio.run(go()) == "t"


def test_closing_a_reader_that_never_started_does_nothing() -> None:
    reader = TerminalKeys()

    reader.close()

    assert reader._thread is None  # noqa: SLF001


def test_there_are_no_keys_when_the_input_is_not_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))

    assert terminal_keys() is None


def test_a_terminal_gets_a_started_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[TerminalKeys] = []
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(keys_module.TerminalKeys, "start", lambda self: started.append(self))

    found = terminal_keys()

    assert found is not None and started == [found]
