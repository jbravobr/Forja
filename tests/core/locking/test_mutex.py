import asyncio
from pathlib import Path

import pytest

from forja.core.failures import TransientFailure
from forja.core.locking import GitMutex, LockOwner, MutexTimeout
from tests.core.locking.support import NO_SLEEP, dead_owner, write_lock


def mutex(locks: Path, **kwargs: object) -> GitMutex:
    return GitMutex(locks, LockOwner.current, settings=NO_SLEEP, poll_interval=0.01, **kwargs)  # type: ignore[arg-type]


def test_tasks_in_one_process_never_overlap_inside_the_mutex(tmp_path: Path) -> None:
    shared = mutex(tmp_path)
    inside = 0
    peak = 0
    order: list[int] = []

    async def worker(number: int) -> None:
        nonlocal inside, peak
        async with shared.hold(timeout=10):
            inside += 1
            peak = max(peak, inside)
            await asyncio.sleep(0.02)
            order.append(number)
            inside -= 1

    async def scenario() -> None:
        await asyncio.gather(*(worker(i) for i in range(6)))

    asyncio.run(scenario())

    assert peak == 1
    assert sorted(order) == list(range(6))


def test_the_lock_directory_exists_only_while_the_mutex_is_held(tmp_path: Path) -> None:
    async def scenario() -> None:
        shared = mutex(tmp_path)
        assert not (tmp_path / "git.mutex").exists()
        async with shared.hold(timeout=5):
            assert (tmp_path / "git.mutex").is_dir()
        assert not (tmp_path / "git.mutex").exists()
        assert [p.name for p in tmp_path.iterdir()] == []

    asyncio.run(scenario())


def test_two_mutex_objects_for_the_same_repository_exclude_each_other(tmp_path: Path) -> None:
    first, second = mutex(tmp_path), mutex(tmp_path)  # as two processes would be
    timeline: list[str] = []

    async def holder() -> None:
        async with first.hold(timeout=5):
            timeline.append("first in")
            await asyncio.sleep(0.2)
            timeline.append("first out")

    async def waiter() -> None:
        await asyncio.sleep(0.05)
        async with second.hold(timeout=5):
            timeline.append("second in")

    async def scenario() -> None:
        await asyncio.gather(holder(), waiter())

    asyncio.run(scenario())

    assert timeline == ["first in", "first out", "second in"]


def test_running_out_of_patience_is_a_transient_failure_and_the_mutex_stays_usable(
    tmp_path: Path,
) -> None:
    first, second = mutex(tmp_path), mutex(tmp_path)

    async def scenario() -> None:
        async with first.hold(timeout=5):
            with pytest.raises(MutexTimeout) as info:
                async with second.hold(timeout=0.2):
                    pytest.fail("should not get in")
            assert isinstance(info.value, TransientFailure)
        async with second.hold(timeout=5):  # nothing was left locked by the failed wait
            pass

    asyncio.run(scenario())


def test_the_mutex_is_released_when_the_body_fails(tmp_path: Path) -> None:
    shared = mutex(tmp_path)

    async def scenario() -> None:
        with pytest.raises(RuntimeError):
            async with shared.hold(timeout=5):
                raise RuntimeError("git crashed")
        async with shared.hold(timeout=5):
            pass

    asyncio.run(scenario())


def test_a_holder_that_died_does_not_block_anyone_forever(tmp_path: Path) -> None:
    write_lock(tmp_path / "git.mutex", dead_owner())

    async def scenario() -> None:
        async with mutex(tmp_path).hold(timeout=5):
            assert (tmp_path / "git.mutex").is_dir()

    asyncio.run(scenario())


def test_the_mutex_is_not_reentrant_so_nesting_times_out_instead_of_hanging(tmp_path: Path) -> None:
    shared = mutex(tmp_path)

    async def scenario() -> None:
        async with shared.hold(timeout=5):
            with pytest.raises(MutexTimeout):
                async with shared.hold(timeout=0.2):
                    pytest.fail("should not get in")

    asyncio.run(scenario())


def test_cancelling_a_waiting_task_leaves_nothing_locked(tmp_path: Path) -> None:
    shared = mutex(tmp_path)

    async def scenario() -> None:
        async with shared.hold(timeout=5):
            waiter = asyncio.create_task(_hold_briefly(shared))
            await asyncio.sleep(0.1)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        async with shared.hold(timeout=5):
            pass

    asyncio.run(scenario())


async def _hold_briefly(shared: GitMutex) -> None:
    async with shared.hold(timeout=10):
        await asyncio.sleep(0.01)


def test_one_mutex_object_can_be_used_from_successive_event_loops(tmp_path: Path) -> None:
    shared = mutex(tmp_path)
    held: list[bool] = []

    async def use() -> None:
        async with shared.hold(timeout=5):
            held.append((tmp_path / "git.mutex").is_dir())

    asyncio.run(use())
    asyncio.run(use())

    assert held == [True, True]
    assert not (tmp_path / "git.mutex").exists()
