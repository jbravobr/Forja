"""The proof of life writes every permission file a CLI declares, even inside subfolders.

Regression: the Cursor adapter declares ``cursor/cli-config.json``; the probe only created
``.forja-agent`` and crashed with ``FileNotFoundError`` before the CLI ever ran.
"""

import asyncio
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from forja.core.adapters.common import CommandPlan
from forja.core.adapters.executor import (
    AGENT_DIR,
    AgentRequest,
    ExecutorAdapter,
    PermissionSetup,
    RolePermissions,
)
from forja.core.artifacts.models import Producer
from forja.executors import KNOWN_CLIS
from forja.executors.probe import run_probe

pytestmark = pytest.mark.integration


class Watched:
    """The real adapter, with the program swapped for one that does nothing."""

    def __init__(self, adapter: ExecutorAdapter) -> None:
        self._adapter = adapter
        self.found: dict[str, bytes] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._adapter, name)

    def plan_invocation(
        self, request: AgentRequest, setup: PermissionSetup, executable: Path
    ) -> CommandPlan:
        plan = self._adapter.plan_invocation(request, setup, executable)
        root = request.workdir / AGENT_DIR
        self.found = {
            name: (root / name).read_bytes() for name in setup.files if (root / name).is_file()
        }
        return replace(plan, argv=(sys.executable, "-c", "pass"))


@pytest.mark.parametrize("key", sorted(KNOWN_CLIS))
def test_the_probe_writes_permission_files_in_subfolders(key: str) -> None:
    adapter = KNOWN_CLIS[key]()
    watched = Watched(adapter)
    declared = adapter.permission_setup(Producer.IMPLEMENTER, RolePermissions(can_edit_files=True))

    problems = asyncio.run(
        run_probe(cast(ExecutorAdapter, watched), Path(sys.executable), dict(os.environ))
    )

    assert watched.found == dict(declared.files)
    # the stand-in ran and wrote nothing: the probe got as far as looking for the file
    assert problems[0] == "the CLI did not create probe.txt"
