"""Running the orchestrator with the screen and the keys: how a run is shown, stopped and ended.

Stopping is one cancellation of the run. The orchestrator saves where it was and leaves the run
paused, and the next ``forja`` offers to resume it. The first ``q`` (or ``ctrl+k``, ``ctrl+c``)
asks for that; a second one while it is saving gives up waiting and leaves at once, which is safe
because every event is already on disk and the next run settles whatever was left open.
"""

import asyncio
import contextlib
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from forja.cli.keys import KeySource
from forja.cli.session import Session
from forja.orchestrator.outcome import ExitCode, Outcome
from forja.orchestrator.phases import SUMMARY_NAME, Orchestrator
from forja.ui.theme import Theme, next_theme
from forja.ui.view import View

REFRESH_SECONDS = 0.25
INTERFACE_FAILURE_NAME = "interface.log"  # the trace of a screen or a key reader that broke


class Renderer(Protocol):
    def start(self, view: View) -> None: ...

    def update(self, view: View) -> None: ...

    def stop(self, view: View) -> None: ...


class ThemedRenderer(Renderer, Protocol):
    theme: Theme


class ForcedExit(Exception):
    """The person asked twice: leave without waiting."""


@dataclass(frozen=True)
class Ended:
    """How a run ended as far as this process is concerned."""

    outcome: Outcome | None  # None: stopped by the person
    interrupted: bool


async def execute(
    session: Session,
    renderer: Renderer,
    keys: KeySource | None = None,
    *,
    refresh: float = REFRESH_SECONDS,
) -> Ended:
    run = asyncio.create_task(Orchestrator(session.context).run())
    pressed = asyncio.Event()
    forced = asyncio.Event()
    helpers = [asyncio.create_task(_refresh(session, renderer, refresh))]
    if keys is not None:
        helpers.append(asyncio.create_task(_watch(keys, renderer, run, pressed, forced)))
    _shown(session, lambda: renderer.start(session.view.snapshot()))
    try:
        await _wait(run, forced)
    except asyncio.CancelledError:
        run.cancel()  # a signal reached the process: the same as the key
        await _settle(run)
        pressed.set()
    finally:
        for helper in helpers:
            helper.cancel()
        for helper in helpers:
            try:
                await helper
            except asyncio.CancelledError:
                pass
            except Exception as error:
                _record_interface_failure(session, error)
        _shown(session, lambda: renderer.stop(session.view.snapshot()))
    if run.cancelled() or pressed.is_set():
        return Ended(None, True)
    return Ended(run.result(), False)


def _shown(session: Session, draw: Callable[[], None]) -> None:
    """Draw on the screen; a screen that breaks is written down and the run goes on."""
    try:
        draw()
    except Exception as error:
        _record_interface_failure(session, error)


def _record_interface_failure(session: Session, error: Exception) -> None:
    """The trace of what broke the screen or the keys, beside the summary. Best effort."""
    with contextlib.suppress(Exception):
        trace = "".join(traceback.format_exception(error))
        with (session.directory / INTERFACE_FAILURE_NAME).open("a", encoding="utf-8") as log:
            log.write(trace)


async def _wait(run: "asyncio.Task[Outcome]", forced: asyncio.Event) -> None:
    gave_up = asyncio.create_task(forced.wait())
    try:
        await asyncio.wait({run, gave_up}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        gave_up.cancel()
    if forced.is_set() and not run.done():
        raise ForcedExit
    with contextlib.suppress(asyncio.CancelledError):
        await run


async def _settle(run: "asyncio.Task[Outcome]") -> None:
    with contextlib.suppress(asyncio.CancelledError):
        await run


async def _refresh(session: Session, renderer: Renderer, every: float) -> None:
    while True:
        await asyncio.sleep(every)
        renderer.update(session.view.snapshot())


async def _watch(
    keys: KeySource,
    renderer: Renderer,
    run: "asyncio.Task[Outcome]",
    pressed: asyncio.Event,
    forced: asyncio.Event,
) -> None:
    async for key in keys.keys():
        if key in ("q", "ctrl+k", "ctrl+c"):
            if pressed.is_set():
                forced.set()
                return
            pressed.set()
            run.cancel()
        elif key == "t" and hasattr(renderer, "theme"):
            themed: ThemedRenderer = renderer  # type: ignore[assignment]
            themed.theme = next_theme(themed.theme)


def exit_code(session: Session, ended: Ended) -> int:
    """The exit code of the process: the run's own, except a delivery that left tasks behind.

    A task left behind is one that was blocked or, with the tokens up, never started: zero is
    only for a delivery with every task integrated.
    """
    if ended.outcome is None:
        return int(ExitCode.INTERRUPTED)
    view = session.view.snapshot()
    if ended.outcome.exit_code is ExitCode.DELIVERED and view.state == "delivered":
        left_behind = view.tasks_total - view.tasks_integrated
        if left_behind > 0 and not view.tasks_integrated:
            return int(ExitCode.NOTHING_INTEGRATED)
        if left_behind > 0:
            return int(ExitCode.SOME_BLOCKED)
    return int(ended.outcome.exit_code)


def closing_lines(session: Session, ended: Ended) -> list[str]:
    """What is told to the person when the run is over, in the order they will want it."""
    view = session.view.snapshot()
    branch = f"forja-run/{session.run_id}"
    if ended.interrupted:
        return [
            "Interrompida. Tudo foi salvo.",
            "Rode `forja` de novo neste repositório para retomar de onde parou.",
        ]
    assert ended.outcome is not None
    lines = [ended.outcome.message]
    if view.state == "delivered":
        if view.tasks_blocked:
            lines.append(
                f"{view.tasks_integrated} de {view.tasks_total} tarefa(s) integrada(s); "
                f"{view.tasks_blocked} bloqueada(s), com o motivo no resumo."
            )
        not_started = view.tasks_total - view.tasks_integrated - view.tasks_blocked
        if not_started > 0:
            lines.append(f"{not_started} tarefa(s) não começaram: o limite de tokens acabou antes.")
        if not view.tasks_integrated:
            lines.append("Nenhuma tarefa foi integrada: a branch não tem mudanças.")
        lines += [
            f"Branch da entrega: {branch} (sua branch não foi alterada; nada foi enviado).",
            f"Resumo: {session.directory / SUMMARY_NAME}",
            f"Para ver: git log --oneline {branch}",
        ]
    elif view.state == "paused":
        lines.append("Rode `forja` de novo neste repositório para retomar.")
    if view.state == "delivered":
        lines += session.context.notices
    return lines


CloseHook = Callable[[Session, Ended], None]
