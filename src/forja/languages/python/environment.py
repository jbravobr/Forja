"""The virtual environment of one run: the interpreter of the repository, plus the packages the
agent's manifest declares on top of what that interpreter already has.

The environment is a directory the Forja creates inside the run's own working directory. The
packages of the repository's interpreter stay visible through a ``.pth`` file, behind the ones
installed here, so a dependency the agent adds never reaches the repository's environment or the
Forja's own. Installing a package runs code of the package (there is no sandbox); the caller
checks that each name exists in the registry first.
"""

import asyncio
import itertools
import sys
from collections.abc import Collection, Mapping
from pathlib import Path

from forja.core.process import Outcome, ProcessIdentity, ProcessRunner, ProcessSpec

_PURELIB = "import sysconfig; print(sysconfig.get_path('purelib'))"
_PTH = "forja-base.pth"
_READY = "forja-ready"
_TAIL = 5  # lines of the installer's output that go into the error


class EnvironmentFailed(Exception):
    """The environment could not be made or a package could not be installed."""


class _Collector:
    """Sink that keeps the output lines, both channels, and drops everything else."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def intent(self, operation_id: str, details: Mapping[str, object]) -> None:
        pass

    def started(self, operation_id: str, identity: ProcessIdentity) -> None:
        pass

    def progress(self, operation_id: str, channel: str, line: str) -> None:
        self.lines.append(line)

    def effect(self, operation_id: str, details: Mapping[str, object]) -> None:
        pass


def _scripts(directory: Path) -> Path:
    return directory / ("Scripts" if sys.platform == "win32" else "bin")


class RunEnvironment:
    """The environment of a run, made when the first dependency needs it. Until then, and for a
    repository that never adds one, the interpreter is the repository's own."""

    def __init__(self, environment: "VirtualEnvironment", base_python: tuple[str, ...]) -> None:
        self._environment = environment
        self._base = base_python
        self._lock = asyncio.Lock()  # tasks that run together share one environment

    @property
    def python(self) -> tuple[str, ...]:
        return self._environment.python if self._environment.ready else self._base

    async def install(self, requirements: Collection[str]) -> str | None:
        """Install the requirements; the error text when that failed, else ``None``."""
        async with self._lock:
            try:
                if not self._environment.ready:
                    await self._environment.create()
                await self._environment.install(requirements)
            except EnvironmentFailed as error:
                return str(error)
        return None


class VirtualEnvironment:
    def __init__(
        self,
        directory: Path,
        *,
        base_python: str,
        env: Mapping[str, str],
        timeout: float,
        log_dir: Path,
    ) -> None:
        self._directory = directory
        self._base = base_python
        self._env = env
        self._timeout = timeout
        self._log_dir = log_dir
        self._numbers = itertools.count(1)
        self._installed: set[str] = set()

    @property
    def python(self) -> tuple[str, ...]:
        """The interpreter of the environment; exists once ``create`` has returned."""
        return (
            str(
                _scripts(self._directory) / ("python.exe" if sys.platform == "win32" else "python")
            ),
        )

    @property
    def installed(self) -> frozenset[str]:
        return frozenset(self._installed)

    async def create(self) -> None:
        """Make the environment and let it see the packages of the base interpreter."""
        self._directory.parent.mkdir(parents=True, exist_ok=True)
        base_packages = await self._purelib(self._base)
        await self._run(self._base, "-m", "venv", str(self._directory))
        own_packages = await self._purelib(self.python[0])
        # addsitedir also reads the .pth files of the base packages (an editable install of the
        # repository lives in one); a plain path line in this .pth would only add the directory.
        line = f"import site; site.addsitedir({base_packages!r})\n"
        (Path(own_packages) / _PTH).write_text(line, encoding="utf-8")
        (self._directory / _READY).write_text("", encoding="utf-8")

    @property
    def ready(self) -> bool:
        """Whether ``create`` finished, here or in an earlier process."""
        return (self._directory / _READY).is_file()

    async def install(self, requirements: Collection[str]) -> None:
        """Install the requirements not installed yet; nothing runs when there are none."""
        missing = sorted(set(requirements) - self._installed)
        if not missing:
            return
        await self._run(
            self.python[0], "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
            *missing,
        )  # fmt: skip
        self._installed.update(missing)

    async def _purelib(self, python: str) -> str:
        return (await self._run(python, "-c", _PURELIB))[-1].strip()

    async def _run(self, *command: str) -> list[str]:
        number = next(self._numbers)
        collector = _Collector()
        spec = ProcessSpec(command, self._directory.parent, self._env, self._timeout)
        result = await ProcessRunner(collector).run(
            f"venv-{number}", spec, self._log_dir / f"venv-{number}.log"
        )
        if result.outcome is not Outcome.EXITED or result.exit_code != 0:
            what = result.outcome.value if result.outcome is not Outcome.EXITED else "failed"
            tail = " | ".join(collector.lines[-_TAIL:])
            raise EnvironmentFailed(f"{' '.join(command[:4])} {what} ({result.exit_code}): {tail}")
        return collector.lines
