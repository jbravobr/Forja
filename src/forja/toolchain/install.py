"""Installs one tool of the manifest into the Forja's own folder (ADR 0037).

Nothing leaves the folder: no administrator, no change to the user's PATH. The artifact is
downloaded, its size and checksum are compared with the manifest, and only then it is unpacked.
A tool is either installed whole, marked by ``MARKER`` written last, or not installed at all: a
failure removes what it made. Installing again what is installed does nothing.
"""

import asyncio
import hashlib
import json
import os
import shutil
import sys
import tarfile
import urllib.request
import uuid
import zipfile
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from forja.core.isolation.environment import install_environment
from forja.core.paths import forja_home
from forja.core.process import Outcome, ProcessIdentity, ProcessRunner, ProcessSpec
from forja.toolchain.load import artifact_for, current_platform, find_tool
from forja.toolchain.manifest import Artifact, Manifest, Platform, Tool

MARKER = ".forja-installed"
DOWNLOAD_TIMEOUT = 60.0  # seconds without an answer from the origin
PIP_TIMEOUT = 900.0
_CHUNK = 1 << 20
_TAIL = 5  # lines of the installer's output that go into the error

Fetch = Callable[[str, Path, int], None]
"""Saves the file at an https URL to a path; raises ``OSError`` when it cannot, or when the file
is longer than the given number of bytes (the size the manifest names)."""


class InstallError(Exception):
    """The tool was not installed; says which tool, from where, and what went wrong."""

    def __init__(self, tool: str, url: str, reason: str) -> None:
        self.tool = tool
        self.url = url
        self.reason = reason
        super().__init__(f"{tool}: {reason} ({url})")


@dataclass(frozen=True)
class Installed:
    tool: str
    version: str
    directory: Path
    executable: Path
    already: bool  # it was installed before this call, and nothing ran


def tools_dir(**kwargs: object) -> Path:
    """Where the Forja keeps the tools it installs, inside its home."""
    return forja_home(**kwargs) / "tools"  # type: ignore[arg-type]


def https_fetch(url: str, destination: Path, limit: int) -> None:
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response:  # noqa: S310
        announced = response.headers.get("Content-Length")
        if announced is not None and announced.isdigit() and int(announced) > limit:
            raise OSError(f"the origin announces {announced} bytes, more than the {limit} expected")
        total = 0
        with destination.open("wb") as out:
            while chunk := response.read(_CHUNK):
                total += len(chunk)
                if total > limit:
                    raise OSError(f"the origin sent more than the {limit} bytes expected")
                out.write(chunk)


class _Collector:
    """Sink that keeps the output lines of the installer and drops everything else."""

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


def _digest(path: Path, algorithm: str) -> str:
    hasher = hashlib.new(algorithm)
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            hasher.update(chunk)
    return hasher.hexdigest()


def _verify(file: Path, artifact: Artifact) -> str | None:
    """The reason the downloaded file is not the one the manifest names, else ``None``."""
    size = file.stat().st_size
    if size != artifact.size:
        return f"the file has {size} bytes, the manifest says {artifact.size}"
    found = _digest(file, artifact.algorithm)
    if found != artifact.checksum:
        return (
            f"the {artifact.algorithm} checksum is {found}, the manifest says {artifact.checksum}"
        )
    return None


class _Escapes(OSError):
    """An archive entry would land outside the folder it is unpacked into."""


def _unzip(file: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(file) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if target != root and root not in target.parents:
                raise _Escapes(f"the archive writes outside its folder: {member.filename}")
        archive.extractall(destination)


def _untar(file: Path, destination: Path) -> None:
    with tarfile.open(file) as archive:
        archive.extractall(destination, filter="data")  # refuses paths and links that escape


def _unpack(file: Path, artifact: Artifact, destination: Path) -> None:
    if artifact.kind == "zip":
        _unzip(file, destination)
    elif artifact.kind in ("tar.gz", "tar.xz"):
        _untar(file, destination)
    elif artifact.kind == "binary":
        shutil.copyfile(file, destination / artifact.executable_path)


async def _install_wheel(tool: Tool, url: str, wheel: Path, destination: Path, python: str) -> None:
    """A virtual environment of its own, with the wheel in it. The environment is made where it
    stays, because the scripts it holds name its own path."""
    # Limit: pip resolves the dependencies of the wheel with no
    # checksum from the manifest.
    steps = (
        (python, "-m", "venv", str(destination)),
        (
            str(_venv_python(destination)),
            *("-m", "pip", "install", "--disable-pip-version-check", "--no-input", str(wheel)),
        ),
    )
    for command in steps:
        collector = _Collector()
        # pip runs the code of the packages it installs: it gets no key and no token
        spec = ProcessSpec(
            command, destination.parent, install_environment(os.environ), PIP_TIMEOUT
        )
        log = destination.parent / f".{tool.name}-install.log"
        result = await ProcessRunner(collector).run(f"install-{tool.name}", spec, log)
        with suppress(OSError):
            log.unlink()
        if result.outcome is not Outcome.EXITED or result.exit_code != 0:
            tail = last_lines(collector.lines)
            raise InstallError(tool.name, url, f"{' '.join(command[:4])} failed: {tail}")


def last_lines(lines: list[str]) -> str:
    return " | ".join(lines[-_TAIL:])


def _venv_python(directory: Path) -> Path:
    if sys.platform == "win32":
        return directory / "Scripts" / "python.exe"
    return directory / "bin" / "python"


def _installed(directory: Path, tool: Tool, artifact: Artifact) -> Path | None:
    """The executable, when ``directory`` holds this exact version of the tool, complete."""
    try:
        marker = json.loads((directory / MARKER).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    executable = directory / artifact.executable_path
    same = marker == {"version": tool.version, "checksum": artifact.checksum}
    return executable if same and executable.is_file() else None


def find_installed(
    manifest: Manifest, name: str, root: Path, *, target: Platform | None = None
) -> Installed | None:
    """Where tool ``name`` is, when its pinned version is installed whole under ``root``."""
    tool = find_tool(manifest, name)
    artifact = artifact_for(manifest, name, current_platform() if target is None else target)
    directory = root / name / tool.version
    executable = _installed(directory, tool, artifact)
    if executable is None:
        return None
    return Installed(name, tool.version, directory, executable, already=True)


async def install_tool(
    manifest: Manifest,
    name: str,
    root: Path,
    *,
    target: Platform | None = None,
    fetch: Fetch = https_fetch,
    python: str = sys.executable,
    on_stage: Callable[[str], None] = lambda _stage: None,
) -> Installed:
    """Install tool ``name`` under ``root`` (``<root>/<name>/<version>``) and return where it is.
    ``on_stage`` hears each stage as it begins: downloading, checksum, extracting, verifying."""
    tool = find_tool(manifest, name)
    target = current_platform() if target is None else target
    artifact = artifact_for(manifest, name, target)
    directory = root / name / tool.version
    if (executable := _installed(directory, tool, artifact)) is not None:
        return Installed(name, tool.version, directory, executable, already=True)

    staging = root / name / f".download-{uuid.uuid4().hex[:8]}"
    download = staging / PurePosixPath(urlsplit(artifact.url).path).name  # pip needs the wheel name
    try:
        _clear(directory)
        staging.mkdir(parents=True)
        on_stage("downloading")
        try:
            await asyncio.to_thread(fetch, artifact.url, download, artifact.size)
        except OSError as error:
            raise InstallError(name, artifact.url, f"the download failed: {error}") from error
        on_stage("checksum")
        if (reason := _verify(download, artifact)) is not None:
            raise InstallError(name, artifact.url, reason)
        on_stage("extracting")
        if artifact.kind == "wheel":
            await _install_wheel(tool, artifact.url, download, directory, python)
        else:
            directory.mkdir()
            try:
                _unpack(download, artifact, directory)
            except (OSError, zipfile.BadZipFile, tarfile.TarError) as error:
                raise InstallError(name, artifact.url, f"cannot unpack it: {error}") from error
        on_stage("verifying")
        executable = directory / artifact.executable_path
        if not executable.is_file():
            raise InstallError(name, artifact.url, f"{artifact.executable_path} is not in it")
        if sys.platform != "win32":
            executable.chmod(executable.stat().st_mode | 0o111)
        body = {"version": tool.version, "checksum": artifact.checksum}
        (directory / MARKER).write_text(json.dumps(body), encoding="utf-8")
    except BaseException:
        _clear(directory)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return Installed(name, tool.version, directory, executable, already=False)


def _clear(directory: Path) -> None:
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
