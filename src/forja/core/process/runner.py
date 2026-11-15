import asyncio
import contextlib
import hashlib
import subprocess
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO

from forja.core.process.group import ProcessGroup, new_group
from forja.core.process.identity import creation_token
from forja.core.process.model import Outcome, ProcessIdentity, ProcessResult, ProcessSpec
from forja.core.process.sink import EventSink

_CHUNK = 65536
_POLL = 0.02


async def _exited(proc: asyncio.subprocess.Process) -> None:
    """Wait for the process itself to end.

    ``Process.wait`` also waits for the pipes to close, which never happens while a descendant
    keeps them open; ``returncode`` is set as soon as the process exits.
    """
    while proc.returncode is None:
        await asyncio.sleep(_POLL)


COMMAND_LOG_BUDGET = 1800  # bytes of the command kept in an event; the limit of an event is 4096
ARGUMENT_LOG_LIMIT = 300


def command_for_log(command: Sequence[str]) -> list[str]:
    """The command as an event records it: whole when it fits, otherwise its start and a count.

    A command that names every file of a large repository does not fit in an event. The start
    says what ran, and a hash of the whole line tells two different long commands apart.
    """
    whole = [
        arg if len(arg) <= ARGUMENT_LOG_LIMIT else arg[:ARGUMENT_LOG_LIMIT] + "..."
        for arg in command
    ]
    if sum(len(arg) + 4 for arg in whole) <= COMMAND_LOG_BUDGET and whole == list(command):
        return whole
    kept: list[str] = []
    used = 0
    for arg in whole:
        if used + len(arg) + 4 > COMMAND_LOG_BUDGET:
            break
        kept.append(arg)
        used += len(arg) + 4
    digest = hashlib.sha256(chr(0).join(command).encode("utf-8", "replace")).hexdigest()[:16]
    kept.append(f"... ({len(command) - len(kept)} more arguments, sha256 {digest})")
    return kept


class ProcessRunner:
    """Single place where external processes are started, watched and ended.

    Per call: intent, process created inside its own group, output turned into progress events
    and a raw log, timeout and cancellation end the whole tree, effect recorded at the end.
    """

    def __init__(
        self,
        sink: EventSink,
        *,
        group_factory: Callable[[], ProcessGroup] = new_group,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sink = sink
        self._group_factory = group_factory
        self._clock = clock

    async def run(self, operation_id: str, spec: ProcessSpec, log_path: Path) -> ProcessResult:
        """Run ``spec`` to the end.

        Cancelling the task stops the tree, records the effect and re-raises.

        The raw log keeps stdout and stderr interleaved, one ``[out] ``/``[err] `` prefixed line
        each, in the original bytes.
        """
        self._sink.intent(
            operation_id,
            {
                "command": command_for_log(spec.command),
                "cwd": str(spec.cwd),
                "env_names": sorted(spec.env),
                "timeout": spec.timeout,
                "grace": spec.grace,
                "stdin_file": None if spec.stdin_file is None else str(spec.stdin_file),
            },
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        start = self._clock()
        group = self._group_factory()
        try:
            try:
                if spec.low_priority:
                    group.lower_priority()
                with contextlib.ExitStack() as files:
                    stdin: int | BinaryIO = subprocess.DEVNULL
                    if spec.stdin_file is not None:
                        stdin = files.enter_context(spec.stdin_file.open("rb"))
                    proc = await asyncio.create_subprocess_exec(
                        *spec.command,
                        cwd=spec.cwd,
                        env=dict(spec.env),
                        stdin=stdin,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        **group.spawn_kwargs(),
                    )
            except OSError as error:
                return self._finish(
                    operation_id, Outcome.SPAWN_FAILED, None, start, log_path, str(error)
                )

            cancelled = False
            try:
                with log_path.open("wb") as log:
                    outcome = await self._supervise(operation_id, proc, group, spec, log)
            except asyncio.CancelledError:
                outcome, cancelled = Outcome.CANCELLED, True
            except Exception:
                await self._reap(proc, group)
                raise
            await self._reap(proc, group)
            result = self._finish(
                operation_id,
                outcome,
                proc.returncode,
                start,
                log_path,
                peak_memory=group.peak_memory(),
            )
            if cancelled:
                raise asyncio.CancelledError
            return result
        finally:
            group.close()

    async def _supervise(
        self,
        operation_id: str,
        proc: asyncio.subprocess.Process,
        group: ProcessGroup,
        spec: ProcessSpec,
        log: BinaryIO,
    ) -> Outcome:
        group.attach(proc.pid)
        self._sink.started(operation_id, ProcessIdentity(proc.pid, creation_token(proc.pid)))
        assert proc.stdout is not None
        assert proc.stderr is not None
        pumps = [
            asyncio.create_task(self._pump(operation_id, proc.stdout, "out", log)),
            asyncio.create_task(self._pump(operation_id, proc.stderr, "err", log)),
        ]
        try:
            async with asyncio.timeout(spec.timeout):
                await _exited(proc)
                # Descendants left behind would hold the pipes open; end them before draining.
                group.kill()
                await self._drain(pumps, spec.grace)
            return Outcome.EXITED
        except TimeoutError:
            await self._stop(proc, group, spec.grace)
            await self._drain(pumps, spec.grace)
            return Outcome.TIMED_OUT
        except asyncio.CancelledError:
            await self._stop(proc, group, spec.grace)
            await self._drain(pumps, spec.grace)
            raise
        finally:
            await self._drain(pumps, 0)

    async def _pump(
        self, operation_id: str, stream: asyncio.StreamReader, channel: str, log: BinaryIO
    ) -> None:
        prefix = f"[{channel}] ".encode()
        pending = b""
        while chunk := await stream.read(_CHUNK):
            *lines, pending = (pending + chunk).split(b"\n")
            for raw in lines:
                self._emit(operation_id, channel, prefix, raw, log)
        if pending:
            self._emit(operation_id, channel, prefix, pending, log)

    def _emit(
        self, operation_id: str, channel: str, prefix: bytes, raw: bytes, log: BinaryIO
    ) -> None:
        raw = raw.removesuffix(b"\r")
        log.write(prefix + raw + b"\n")
        log.flush()
        self._sink.progress(operation_id, channel, raw.decode("utf-8", errors="replace"))

    @staticmethod
    async def _drain(pumps: list["asyncio.Task[None]"], timeout: float) -> None:
        _, pending = await asyncio.wait(pumps, timeout=timeout)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    @staticmethod
    async def _stop(proc: asyncio.subprocess.Process, group: ProcessGroup, grace: float) -> None:
        group.interrupt()
        with suppress(TimeoutError):
            await asyncio.wait_for(_exited(proc), grace)
        group.kill()

    @staticmethod
    async def _reap(proc: asyncio.subprocess.Process, group: ProcessGroup) -> None:
        group.kill()
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.kill()
        await _exited(proc)

    def _finish(
        self,
        operation_id: str,
        outcome: Outcome,
        exit_code: int | None,
        start: float,
        log_path: Path,
        error: str | None = None,
        peak_memory: int | None = None,
    ) -> ProcessResult:
        duration = self._clock() - start
        details: dict[str, object] = {
            "outcome": outcome.value,
            "exit_code": exit_code,
            "duration_seconds": duration,
            "log_path": str(log_path),
        }
        if error is not None:
            details["error"] = error
        self._sink.effect(operation_id, details)
        return ProcessResult(outcome, exit_code, duration, log_path, peak_memory)
