"""A run shown on the real full screen (headless): started, kept up to date, stopped by its keys."""

import asyncio
import io
from pathlib import Path
from typing import cast

import pytest

from forja.cli.run import Ended, ForcedExit, closing_lines, execute
from forja.cli.session import Session
from forja.ui.full import FullRenderer
from forja.ui.terminal import Capabilities
from forja.ui.theme import THEMES
from tests.cli.test_run import Waiting, make_session
from tests.orchestrator.fakes import ScriptedAgent
from tests.orchestrator.harness import MemoryWorld

CAPS = Capabilities(
    interactive=True, colors="truecolor", unicode=True, columns=120, rows=36,
    program="xterm", dark_background=True, can_resize=True, reduced_motion=False,
)  # fmt: skip


def renderer(out: io.StringIO) -> FullRenderer:
    return FullRenderer(THEMES["escuro"], CAPS, out=out, headless=True)


async def leave_soon(screen: FullRenderer) -> None:
    """Press the last key once the screen shows the report and waits for it."""
    while not screen.keys_source.finished:
        await asyncio.sleep(0.01)
    screen.keys_source.exit()


def test_a_run_is_shown_and_the_report_waits_for_a_key(tmp_path: Path) -> None:
    world = MemoryWorld(tmp_path)
    session, _ = make_session(world, ScriptedAgent())
    out = io.StringIO()
    screen = renderer(out)

    async def go() -> Ended:
        ended = await execute(cast(Session, session), screen, screen.keys_source, refresh=0.01)
        lines = closing_lines(cast(Session, session), ended)
        asyncio.get_running_loop().create_task(leave_soon(screen))
        await screen.finish(lines)
        return ended

    ended = asyncio.run(go())

    assert ended.outcome is not None
    assert screen.keys_source.finished
    assert screen.keys_source.view is not None and screen.keys_source.view.state == "delivered"
    assert out.getvalue().startswith("\x1b[8;36;120t") is False  # can_resize: no change needed
    assert out.getvalue() == ""


def test_ctrl_k_on_the_screen_stops_the_run_and_leaves_it_resumable(tmp_path: Path) -> None:
    world = MemoryWorld(tmp_path)
    began = asyncio.Event()
    session, _ = make_session(world, Waiting(began=began))
    screen = renderer(io.StringIO())

    async def go() -> Ended:
        async def press() -> None:
            await began.wait()
            await screen.keys_source.run_action("stop")

        asyncio.get_running_loop().create_task(press())
        ended = await execute(cast(Session, session), screen, screen.keys_source, refresh=0.01)
        asyncio.get_running_loop().create_task(leave_soon(screen))
        await screen.finish(closing_lines(cast(Session, session), ended))
        return ended

    ended = asyncio.run(go())

    assert ended == Ended(None, True)


def test_a_second_press_while_saving_leaves_at_once(tmp_path: Path) -> None:
    world = MemoryWorld(tmp_path)
    began = asyncio.Event()
    session, _ = make_session(world, Waiting(slow_exit=True, began=began))
    screen = renderer(io.StringIO())

    async def go() -> None:
        async def press() -> None:
            await began.wait()
            await screen.keys_source.run_action("stop")
            await screen.keys_source.run_action("stop")

        asyncio.get_running_loop().create_task(press())
        try:
            await execute(cast(Session, session), screen, screen.keys_source, refresh=0.01)
        finally:
            screen.abort()
            await screen.closed()

    with pytest.raises(ForcedExit):
        asyncio.run(go())


def test_the_window_is_enlarged_and_put_back_where_the_terminal_allows(tmp_path: Path) -> None:
    small = Capabilities(
        interactive=True, colors="truecolor", unicode=True, columns=80, rows=24,
        program="xterm", dark_background=True, can_resize=True, reduced_motion=False,
    )  # fmt: skip
    out = io.StringIO()
    screen = FullRenderer(THEMES["escuro"], small, out=out, headless=True)
    world = MemoryWorld(tmp_path)
    session, _ = make_session(world, ScriptedAgent())

    async def go() -> None:
        await execute(cast(Session, session), screen, screen.keys_source, refresh=0.01)
        asyncio.get_running_loop().create_task(leave_soon(screen))
        await screen.finish(["fim"])

    asyncio.run(go())

    assert out.getvalue() == "\x1b[8;36;120t\x1b[8;24;80t"
