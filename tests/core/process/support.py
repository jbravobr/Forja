import asyncio
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path

from forja.core.process import (
    ProcessIdentity,
    ProcessResult,
    ProcessRunner,
    ProcessSpec,
    creation_token,
)

# What a process says of itself: the priority class on Windows, the niceness elsewhere.
OWN_PRIORITY = (
    "import os, sys\n"
    "if sys.platform == 'win32':\n"
    "    import ctypes\n"
    "    k32 = ctypes.WinDLL('kernel32')\n"
    "    k32.GetCurrentProcess.restype = ctypes.c_void_p\n"
    "    k32.GetPriorityClass.argtypes = [ctypes.c_void_p]\n"
    "    print(k32.GetPriorityClass(k32.GetCurrentProcess()))\n"
    "else:\n"
    "    print(os.getpriority(os.PRIO_PROCESS, 0))\n"
)
LOWEST_PRIORITY = 0x40 if sys.platform == "win32" else 19  # the idle class; the highest niceness


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, object]] = []

    def intent(self, operation_id: str, details: Mapping[str, object]) -> None:
        self.events.append(("intent", operation_id, dict(details)))

    def started(self, operation_id: str, identity: ProcessIdentity) -> None:
        self.events.append(("started", operation_id, identity))

    def progress(self, operation_id: str, channel: str, line: str) -> None:
        self.events.append(("progress", operation_id, (channel, line)))

    def effect(self, operation_id: str, details: Mapping[str, object]) -> None:
        self.events.append(("effect", operation_id, dict(details)))

    def kinds(self) -> list[str]:
        return [kind for kind, _, _ in self.events]

    def lines(self, channel: str) -> list[str]:
        return [
            payload[1]
            for kind, _, payload in self.events
            if kind == "progress" and isinstance(payload, tuple) and payload[0] == channel
        ]

    def of(self, kind: str) -> object:
        return next(payload for k, _, payload in self.events if k == kind)


def minimal_env(**extra: str) -> dict[str, str]:
    """The smallest environment in which a Python child can start."""
    keys = ["SYSTEMROOT"] if sys.platform == "win32" else ["PATH", "LANG"]
    env = {key: os.environ[key] for key in keys if key in os.environ}
    return env | extra


def python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def spec(code: str, *, timeout: float | None = 20, grace: float = 2.0, **env: str) -> ProcessSpec:
    return ProcessSpec(python(code), Path.cwd(), minimal_env(**env), timeout, grace)


def run_to_end(
    process_spec: ProcessSpec, log_path: Path, sink: RecordingSink | None = None
) -> tuple[ProcessResult, RecordingSink]:
    sink = sink or RecordingSink()
    result = asyncio.run(ProcessRunner(sink).run("op-1", process_spec, log_path))
    return result, sink


def wait_until_dead(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if creation_token(pid) is None:
            return True
        time.sleep(0.05)
    return False
