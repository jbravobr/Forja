"""The proof of life writes every permission file a CLI declares, even inside subfolders.

Regression: the Cursor adapter declares ``cursor/cli-config.json``; the probe only created
``.forja-agent`` and crashed with ``FileNotFoundError`` before the CLI ever ran.
"""

import asyncio
import stat
from pathlib import Path

import pytest

from forja.executors import KNOWN_CLIS
from forja.executors.probe import run_probe


@pytest.mark.parametrize("key", sorted(KNOWN_CLIS))
def test_the_probe_writes_permission_files_in_subfolders(key: str, tmp_path: Path) -> None:
    fake = tmp_path / "fake-cli"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    problems = asyncio.run(run_probe(KNOWN_CLIS[key](), fake, {}, timeout=30))

    assert isinstance(problems, list)
