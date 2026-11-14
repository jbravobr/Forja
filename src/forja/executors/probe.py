"""The proof of life of an agent CLI: does it start, is it logged in, can it write a file.

The CLI is the user's own and set up beforehand; Forja only checks. The check is a trivial job in
a temporary directory, run exactly the way a real call is run, so a login that expired, a gateway
that does not answer or a flag the version no longer knows shows up here and not in the middle of
a run.
"""

import asyncio
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

from forja.core.adapters.common import build_spec
from forja.core.adapters.executor import (
    AGENT_DIR,
    AgentActivity,
    AgentRequest,
    ErrorActivity,
    ExecutorAdapter,
    RolePermissions,
    check_probe,
    meets_minimum,
)
from forja.core.artifacts.models import Producer
from forja.core.process import EventSink, ProcessIdentity, ProcessRunner

PROBE_TIMEOUT = 180.0


class Silent:
    """An event sink that keeps nothing: the probe is not part of any run."""

    def intent(self, operation_id: str, details: Mapping[str, object]) -> None:
        pass

    def started(self, operation_id: str, identity: ProcessIdentity) -> None:
        pass

    def progress(self, operation_id: str, channel: str, line: str) -> None:
        pass

    def effect(self, operation_id: str, details: Mapping[str, object]) -> None:
        pass


async def executable_version(adapter: ExecutorAdapter, path: Path) -> str | None:
    """The version the CLI says it is, or ``None`` if it does not answer."""
    spec = adapter.discovery
    try:
        process = await asyncio.create_subprocess_exec(
            str(path),
            *spec.version_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(process.communicate(), timeout=30)
    except (OSError, TimeoutError):
        return None
    found = re.search(spec.version_pattern, out.decode("utf-8", errors="replace"))
    return found.group(1) if found else None


def version_problem(adapter: ExecutorAdapter, version: str | None) -> str | None:
    if version is None:
        return "a CLI não respondeu ao pedido de versão"
    minimum = adapter.discovery.min_version
    if not meets_minimum(version, minimum):
        return f"a versão {version} é mais antiga que a mínima ({minimum})"
    return None


async def run_probe(
    adapter: ExecutorAdapter,
    executable: Path,
    environ: Mapping[str, str],
    *,
    sink: EventSink | None = None,
    timeout: float = PROBE_TIMEOUT,
) -> list[str]:
    """What the proof of life found wrong; an empty list means the CLI can be used."""
    with tempfile.TemporaryDirectory(prefix="forja-probe-") as name:
        workdir = Path(name)
        permissions = RolePermissions(can_edit_files=True)
        setup = adapter.permission_setup(Producer.IMPLEMENTER, permissions)
        (workdir / AGENT_DIR).mkdir()
        for relative, data in setup.files.items():
            (workdir / AGENT_DIR / relative).parent.mkdir(parents=True, exist_ok=True)
            (workdir / AGENT_DIR / relative).write_bytes(data)
        package = workdir / AGENT_DIR / "package.md"
        package.write_text(adapter.probe.prompt, encoding="utf-8")
        request = AgentRequest(Producer.IMPLEMENTER, package, workdir, permissions)
        plan = adapter.plan_invocation(request, setup, executable)
        log = workdir / "log.txt"
        result = await ProcessRunner(sink or Silent()).run(
            "probe", build_spec(plan, environ, timeout=timeout), log
        )
        lines = [
            line.removeprefix("[out] ")
            for line in log.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.startswith("[out] ")
        ]
        activities: list[AgentActivity] = [
            seen for seen in map(adapter.parse_event, lines) if seen is not None
        ]
        errors = [seen.message for seen in activities if isinstance(seen, ErrorActivity)]
        if errors:
            return [errors[0]]
        if result.exit_code != 0:
            return [f"a CLI terminou com {result.outcome.value}, código {result.exit_code}"]
        return check_probe(adapter.probe, workdir, activities)
