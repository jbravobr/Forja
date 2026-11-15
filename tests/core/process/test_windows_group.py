"""The Windows side of the process group, asked directly: what a kill leaves, and the handles."""

import importlib
import os
import subprocess
import sys
from typing import Any

import pytest

from tests.core.process.support import minimal_env, python

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the job object is of Windows")

SLEEP = "import time; time.sleep(60)"


def windows() -> Any:
    return importlib.import_module("forja.core.process._windows")


def sleeping(**options: Any) -> "subprocess.Popen[bytes]":
    return subprocess.Popen(python(SLEEP), env=minimal_env(), **options)  # noqa: S603


def test_a_tree_the_group_kills_ends_with_a_code_that_is_not_the_one_of_success() -> None:
    group = windows().WindowsJobGroup()
    child = sleeping(**group.spawn_kwargs())
    try:
        group.attach(child.pid)

        group.kill()

        assert child.wait(timeout=10) == 1
    finally:
        group.close()
        child.kill()


def test_a_leftover_process_killed_alone_ends_with_a_code_that_is_not_the_one_of_success() -> None:
    child = sleeping()
    try:
        windows().kill_process(child.pid)

        assert child.wait(timeout=10) == 1
    finally:
        child.kill()


def test_no_handle_opened_on_a_process_can_be_inherited_by_what_starts_meanwhile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = windows()
    opened: list[tuple[str, bool]] = []
    real = module._k32.OpenProcess  # noqa: SLF001
    asking = ["nobody"]

    def noted(access: int, inherit: bool, pid: int) -> Any:
        opened.append((asking[0], inherit))
        return real(access, inherit, pid)

    monkeypatch.setattr(module._k32, "OpenProcess", noted)  # noqa: SLF001
    group = module.WindowsJobGroup()
    child = sleeping(**group.spawn_kwargs())
    try:
        asking[0] = "attach"
        group.attach(child.pid)
        asking[0] = "creation_token"
        assert module.creation_token(os.getpid()) is not None
        asking[0] = "kill_process"
        module.kill_process(child.pid)
        child.wait(timeout=10)
    finally:
        group.close()
        child.kill()

    assert opened == [("attach", False), ("creation_token", False), ("kill_process", False)]
