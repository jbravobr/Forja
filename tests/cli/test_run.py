"""A run with the screen and the keys: shown while it goes, stopped by ctrl+k, left resumable."""

import asyncio
import dataclasses
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest

from forja.cli.run import Ended, ForcedExit, _wait, closing_lines, execute
from forja.cli.session import Session
from forja.core.states import ExecutionState, Level
from forja.orchestrator.outcome import ExitCode
from forja.orchestrator.phases import confirm_run
from forja.orchestrator.ports import AgentResult, AttemptContext
from forja.ui.theme import THEMES
from forja.ui.view import View
from tests.orchestrator.fakes import ScriptedAgent, ScriptedMeasurer, ScriptedPlanner
from tests.orchestrator.harness import ISOLATION, RUN, MemoryWorld
from tests.ui.support import model


@dataclass
class FakeSession:
    """What ``execute`` and ``closing_lines`` read of a session."""

    context: Any
    view: Any
    run_id: str
    directory: Path


@dataclass
class Recorder:
    started: list[View] = field(default_factory=list)
    updates: list[View] = field(default_factory=list)
    stopped: list[View] = field(default_factory=list)
    theme: Any = THEMES["escuro"]

    def start(self, view: View) -> None:
        self.started.append(view)

    def update(self, view: View) -> None:
        self.updates.append(view)

    def stop(self, view: View) -> None:
        self.stopped.append(view)


class Scripted:
    """Keys pressed one after the other, from the moment ``ready`` says the run is under way."""

    def __init__(self, *presses: str, ready: Callable[[], Awaitable[object]]) -> None:
        self._presses = presses
        self._ready = ready

    async def keys(self) -> AsyncIterator[str]:
        await self._ready()
        for key in self._presses:
            yield key
        await asyncio.sleep(3600)

    def close(self) -> None:
        pass


@dataclass
class Waiting(ScriptedAgent):
    """In the middle of its call until it is cancelled; ``slow_exit`` makes cancelling take long."""

    slow_exit: bool = False
    began: asyncio.Event | None = None

    async def call(self, ctx: AttemptContext, operation_id: str, sink: Any) -> AgentResult:
        if self.began is not None:
            self.began.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            if self.slow_exit:
                await asyncio.sleep(3600)
            raise
        raise AssertionError("unreachable")


def make_session(world: MemoryWorld, agent: ScriptedAgent) -> tuple[FakeSession, Any]:
    log = world.open()
    confirm_run(log, world.config, ISOLATION)
    view = model()
    log.subscribe(view.apply)
    ctx = world.context(log, measurer=ScriptedMeasurer(), agent=agent, planner=ScriptedPlanner())
    return FakeSession(ctx, view, RUN, world.run_dir), log


def test_a_run_that_ends_is_shown_from_start_to_stop_and_reports_the_delivery(
    tmp_path: Path,
) -> None:
    world = MemoryWorld(tmp_path)
    session, _ = make_session(world, ScriptedAgent())
    screen = Recorder()

    ended = asyncio.run(execute(cast(Session, session), screen, None, refresh=0.01))

    assert not ended.interrupted
    assert ended.outcome is not None
    assert ended.outcome.exit_code is ExitCode.DELIVERED
    assert len(screen.started) == 1
    assert len(screen.stopped) == 1
    assert screen.stopped[0].state == ExecutionState.DELIVERED
    lines = closing_lines(cast(Session, session), ended)
    assert any(line.startswith(f"Branch da entrega: forja-run/{RUN}") for line in lines)
    assert any("resumo.md" in line for line in lines)
    assert any("nada foi enviado" in line for line in lines)
    assert not any("Nenhuma tarefa foi integrada" in line for line in lines)


def run_with_keys(
    tmp_path: Path, *presses: str, slow_exit: bool = False, refreshes: int = 0
) -> tuple[Ended, FakeSession, Any, Recorder]:
    """The keys are pressed once the agent is in its call and the screen was refreshed enough."""
    world = MemoryWorld(tmp_path)
    began = asyncio.Event()
    session, log = make_session(world, Waiting(slow_exit=slow_exit, began=began))
    screen = Recorder()

    async def ready() -> None:
        await began.wait()
        while len(screen.updates) < refreshes:
            await asyncio.sleep(0)

    keys = Scripted(*presses, ready=ready)
    ended = asyncio.run(execute(cast(Session, session), screen, keys, refresh=0.01))
    return ended, session, log, screen


@pytest.mark.parametrize("key", ["q", "ctrl+k", "ctrl+c"])
def test_one_press_stops_the_run_and_leaves_it_paused_and_resumable(
    tmp_path: Path, key: str
) -> None:
    ended, session, log, screen = run_with_keys(tmp_path, key)

    assert ended == Ended(None, True)
    assert log.state.state_of(Level.EXECUTION, RUN) is ExecutionState.PAUSED
    assert len(screen.stopped) == 1
    lines = closing_lines(cast(Session, session), ended)
    assert lines[0] == "Interrompida. Tudo foi salvo."
    assert "retomar" in lines[1]


def test_a_second_press_while_it_is_still_saving_leaves_at_once(tmp_path: Path) -> None:
    with pytest.raises(ForcedExit):
        run_with_keys(tmp_path, "ctrl+k", "ctrl+k", slow_exit=True)


def test_a_signal_that_cancels_the_process_is_the_same_as_the_key(tmp_path: Path) -> None:
    world = MemoryWorld(tmp_path)
    began = asyncio.Event()
    session, log = make_session(world, Waiting(began=began))
    screen = Recorder()

    async def scenario() -> Ended:
        task = asyncio.create_task(execute(cast(Session, session), screen, None, refresh=0.01))
        await began.wait()
        task.cancel()
        return await task

    ended = asyncio.run(scenario())

    assert ended == Ended(None, True)
    assert log.state.state_of(Level.EXECUTION, RUN) is ExecutionState.PAUSED


def test_the_key_t_changes_the_theme_and_other_keys_do_nothing(tmp_path: Path) -> None:
    world = MemoryWorld(tmp_path)
    began = asyncio.Event()
    session, _ = make_session(world, Waiting(began=began))
    screen = Recorder()

    async def scenario() -> None:
        keys = Scripted("t", "x", "t", "ctrl+k", ready=began.wait)
        await execute(cast(Session, session), screen, keys, refresh=0.01)

    asyncio.run(scenario())

    assert screen.theme.key == "claro"


def test_the_screen_is_refreshed_while_the_run_goes_on(tmp_path: Path) -> None:
    ended, _, _, screen = run_with_keys(tmp_path, "ctrl+k", refreshes=3)

    assert ended.interrupted
    assert len(screen.updates) >= 3


@dataclass
class Held(ScriptedAgent):
    """Does its work only after ``go``: the run is still going when the interface breaks."""

    go: asyncio.Event = field(default_factory=asyncio.Event)

    async def call(self, ctx: AttemptContext, operation_id: str, sink: Any) -> AgentResult:
        await self.go.wait()
        return await super().call(ctx, operation_id, sink)


@dataclass
class Breaking(Recorder):
    """A screen that breaks in one of its three calls."""

    where: str = ""
    broke: asyncio.Event = field(default_factory=asyncio.Event)

    def _break(self, name: str) -> None:
        if name == self.where:
            self.broke.set()
            raise RuntimeError(f"the screen broke in {name}")

    def start(self, view: View) -> None:
        self._break("start")
        super().start(view)

    def update(self, view: View) -> None:
        self._break("update")
        super().update(view)

    def stop(self, view: View) -> None:
        self._break("stop")
        super().stop(view)


class BrokenKeys:
    def __init__(self, broke: asyncio.Event) -> None:
        self._broke = broke

    async def keys(self) -> AsyncIterator[str]:
        self._broke.set()
        raise RuntimeError("the keys broke")
        yield ""

    def close(self) -> None:
        pass


@pytest.mark.parametrize("where", ["start", "update", "stop"])
def test_a_screen_that_breaks_does_not_take_the_run_with_it(tmp_path: Path, where: str) -> None:
    world = MemoryWorld(tmp_path)
    screen = Breaking(where=where)
    agent = Held()
    if where == "update":
        agent.go = screen.broke
    else:
        agent.go.set()
    session, _ = make_session(world, agent)

    ended = asyncio.run(execute(cast(Session, session), screen, None, refresh=0.01))

    assert not ended.interrupted
    assert ended.outcome is not None
    assert ended.outcome.exit_code is ExitCode.DELIVERED
    trace = (world.run_dir / "interface.log").read_text(encoding="utf-8")
    assert trace.count(f"RuntimeError: the screen broke in {where}") == 1
    assert "Traceback" in trace
    if where != "stop":
        assert len(screen.stopped) == 1


def test_keys_that_break_do_not_take_the_run_with_them(tmp_path: Path) -> None:
    world = MemoryWorld(tmp_path)
    agent = Held()
    session, _ = make_session(world, agent)

    ended = asyncio.run(
        execute(cast(Session, session), Recorder(), cast(Any, BrokenKeys(agent.go)), refresh=0.01)
    )

    assert not ended.interrupted
    assert ended.outcome is not None
    assert ended.outcome.exit_code is ExitCode.DELIVERED
    trace = (world.run_dir / "interface.log").read_text(encoding="utf-8")
    assert "RuntimeError: the keys broke" in trace


def test_a_run_with_a_sound_interface_writes_no_interface_log(tmp_path: Path) -> None:
    world = MemoryWorld(tmp_path)
    session, _ = make_session(world, ScriptedAgent())

    asyncio.run(execute(cast(Session, session), Recorder(), None, refresh=0.01))

    assert not (world.run_dir / "interface.log").exists()


def test_a_paused_run_says_how_to_resume(tmp_path: Path) -> None:
    world = MemoryWorld(tmp_path)
    session, _ = make_session(world, ScriptedAgent())
    ended = asyncio.run(execute(cast(Session, session), Recorder(), None, refresh=0.01))
    paused = Ended(ended.outcome, False)
    session.view.state = "paused"

    lines = closing_lines(cast(Session, session), paused)

    assert "retomar" in lines[-1]


def delivered_session(tmp_path: Path, notices: tuple[str, ...]) -> tuple[FakeSession, Ended]:
    world = MemoryWorld(tmp_path)
    session, _ = make_session(world, ScriptedAgent())
    session.context = dataclasses.replace(session.context, notices=notices)
    ended = asyncio.run(execute(cast(Session, session), Recorder(), None, refresh=0.01))
    return session, ended


def test_the_notices_of_the_run_are_told_after_a_delivery(tmp_path: Path) -> None:
    session, ended = delivered_session(tmp_path, ("aviso do gate 11",))

    assert closing_lines(cast(Session, session), ended)[-1] == "aviso do gate 11"


def test_the_notices_are_not_told_when_the_run_did_not_deliver(tmp_path: Path) -> None:
    session, ended = delivered_session(tmp_path, ("aviso do gate 11",))
    session.view.state = "paused"

    assert "aviso do gate 11" not in closing_lines(cast(Session, session), ended)


def test_a_delivery_with_nothing_integrated_says_the_branch_has_no_change(tmp_path: Path) -> None:
    session, ended = delivered_session(tmp_path, ())
    session.view.tasks = dict.fromkeys(session.view.tasks, "blocked")

    lines = closing_lines(cast(Session, session), ended)

    assert "Nenhuma tarefa foi integrada: a branch não tem mudanças." in lines


@dataclass
class Swallowing(ScriptedAgent):
    """Takes the first cancellation in and goes on, as a run that ends on its own after a press."""

    began: asyncio.Event | None = None
    calls: int = 0

    async def call(self, ctx: AttemptContext, operation_id: str, sink: Any) -> AgentResult:
        self.calls += 1
        if self.calls == 1:
            if self.began is not None:
                self.began.set()
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                asyncio.current_task().uncancel()  # type: ignore[union-attr]
        return await super().call(ctx, operation_id, sink)


def test_a_press_counts_as_a_stop_even_when_the_run_still_ends_on_its_own(tmp_path: Path) -> None:
    world = MemoryWorld(tmp_path)
    began = asyncio.Event()
    session, _ = make_session(world, Swallowing(began=began))
    keys = Scripted("q", ready=began.wait)

    ended = asyncio.run(execute(cast(Session, session), Recorder(), keys, refresh=0.01))

    assert ended == Ended(None, True)


def test_a_second_press_after_the_run_is_over_does_not_force_the_exit() -> None:
    async def scenario() -> Any:
        async def done() -> Any:
            return "outcome"

        run = asyncio.create_task(done())
        await run
        forced = asyncio.Event()
        forced.set()
        await _wait(run, forced)
        return run.result()

    assert asyncio.run(scenario()) == "outcome"
