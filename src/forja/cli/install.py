"""``forja instalar`` and the offer inside ``forja``: installs the tools the Forja needs.

The installer only reports what happens, as the events of ``ui.install_events``; the screen for the
terminal (full, live line or plain text) draws them. A tool already on the PATH, or already
installed by the Forja, counts as present and nothing is downloaded for it. Installing is
idempotent, and one tool failing does not stop the next from being tried.
"""

import asyncio
import os
import sys
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

from forja.cli.console import Console
from forja.cli.detect import Detection
from forja.cli.settings import Execucao, Perfil
from forja.core.adapters.executor import locate_in_path
from forja.orchestrator.outcome import ExitCode
from forja.toolchain.install import (
    Fetch,
    InstallError,
    find_installed,
    https_fetch,
    install_tool,
    tools_dir,
)
from forja.toolchain.load import (
    NoArtifactForPlatform,
    UnsupportedPlatform,
    artifact_for,
    current_platform,
    load_manifest,
)
from forja.toolchain.manifest import Manifest, Tool
from forja.toolchain.resolve import with_installed_tools
from forja.ui import terminal
from forja.ui.install_events import (
    InstallEvent,
    InstallFinished,
    InstallModel,
    InstallStarted,
    InstallView,
    Stage,
    StageEntered,
    ToolFailed,
    ToolInfo,
)
from forja.ui.install_full import InstallFullRenderer
from forja.ui.install_text import InstallLiveRenderer, InstallPlainRenderer, InstallRenderer
from forja.ui.terminal import Tier
from forja.ui.theme import theme_named, with_ascii

POLL = 0.2  # seconds between two readings of how much was downloaded
STAGES = {stage.value: stage for stage in Stage}


def _needed(tool: Tool, detection: Detection | None) -> bool:
    if tool.needed_by == "always":
        return True
    if detection is None:
        return False
    if tool.name == "maven":
        return detection.maven
    if tool.name == "gradle":
        return detection.gradle
    return any(getattr(detection, language, False) for language in tool.needed_by)


def wanted(manifest: Manifest, detection: Detection | None = None) -> list[Tool]:
    """The tools every run needs, plus those of the languages found in the repository."""
    return [tool for tool in manifest.tools if _needed(tool, detection)]


def is_present(manifest: Manifest, tool: Tool, root: Path, environ: Mapping[str, str]) -> bool:
    """On the PATH, or installed whole by the Forja in its own folder."""
    if locate_in_path(tool.executable, environ.get("PATH")) is not None:
        return True
    return find_installed(manifest, tool.name, root) is not None


def missing_tools(
    environ: Mapping[str, str],
    manifest: Manifest | None = None,
    detection: Detection | None = None,
) -> list[Tool]:
    """What ``forja instalar`` would install. Empty on a platform the Forja installs nothing for."""
    manifest = load_manifest() if manifest is None else manifest
    root = tools_dir(environ=environ)
    try:
        current_platform()
    except UnsupportedPlatform:
        return []
    return [
        tool
        for tool in wanted(manifest, detection)
        if not is_present(manifest, tool, root, environ)
    ]


def offer_line(tools: list[Tool], environ: Mapping[str, str]) -> str:
    names = ", ".join(f"{tool.name} {tool.version}" for tool in tools)
    return f"Faltam ferramentas: {names}. Instalação em {tools_dir(environ=environ)}."


def _info(manifest: Manifest, tool: Tool) -> ToolInfo:
    artifact = artifact_for(manifest, tool.name)
    return ToolInfo(
        key=tool.name,
        name=tool.name,
        purpose=tool.purpose,
        version=tool.version,
        origin=urlsplit(artifact.url).netloc,
        size=artifact.size,
    )


def _failure(tool: Tool, reason: str) -> ToolFailed:
    fix = f"rode `forja instalar` de novo; se persistir, instale {tool.name} à mão"
    return ToolFailed(tool.name, reason, fix)


def _watched(fetch: Fetch, report: Callable[[int], None]) -> Fetch:
    """``fetch`` that also reports how many bytes are on disk while it runs."""

    def watched(url: str, destination: Path, limit: int) -> None:
        done = threading.Event()

        def poll() -> None:
            while not done.wait(POLL):
                with suppress(OSError):
                    report(destination.stat().st_size)

        reader = threading.Thread(target=poll, daemon=True)
        reader.start()
        try:
            fetch(url, destination, limit)
        finally:
            done.set()
            reader.join()

    return watched


async def install_all(
    tools: list[Tool],
    manifest: Manifest,
    environ: Mapping[str, str],
    renderer: InstallRenderer,
    *,
    fetch: Fetch = https_fetch,
    python: str = sys.executable,
) -> InstallView:
    """Install ``tools`` one after the other, telling ``renderer`` what happens; return the end."""
    root = tools_dir(environ=environ)
    model = InstallModel()
    loop = asyncio.get_running_loop()

    def emit(event: InstallEvent) -> None:
        model.apply(event)
        renderer.update(model.snapshot())

    model.apply(InstallStarted(tuple(_info(manifest, tool) for tool in tools), str(root)))
    renderer.start(model.snapshot())
    for tool in tools:

        def on_stage(stage: str, name: str = tool.name) -> None:
            emit(StageEntered(name, STAGES[stage]))

        def on_bytes(received: int, name: str = tool.name) -> None:
            loop.call_soon_threadsafe(emit, StageEntered(name, Stage.DOWNLOADING, received))

        try:
            installed = await install_tool(
                manifest,
                tool.name,
                root,
                fetch=_watched(fetch, on_bytes),
                python=python,
                on_stage=on_stage,
            )
        except InstallError as error:
            emit(_failure(tool, error.reason))
        except NoArtifactForPlatform as error:
            emit(_failure(tool, str(error)))
        else:
            emit(StageEntered(tool.name, Stage.PRESENT if installed.already else Stage.READY))
    model.apply(InstallFinished())
    final = model.snapshot()
    renderer.stop(final)
    return final


def install_renderer_for(
    profile: Perfil, saved: Execucao | None, console: Console
) -> InstallRenderer:
    """The screen for this terminal, chosen as for a run."""
    caps = terminal.detect()
    style = saved.interface if saved else profile.interface
    forced = None if style.modo == "auto" else Tier(style.modo)
    tier, why = terminal.choose_tier(caps, forced, full_available=True)
    if why:
        console.say(why)
    if tier is Tier.TEXT:
        return InstallPlainRenderer(sys.stdout)
    theme = theme_named(style.tema or None, dark_background=caps.dark_background)
    if style.ascii or not caps.unicode:
        theme = with_ascii(theme)
    if tier is Tier.FULL:
        return InstallFullRenderer(theme)
    return InstallLiveRenderer(theme, caps)


async def run_install(
    tools: list[Tool],
    environ: Mapping[str, str],
    renderer: InstallRenderer,
    console: Console,
    *,
    manifest: Manifest | None = None,
    fetch: Fetch = https_fetch,
) -> int:
    """Install, show the report and say how it went: 0 when every tool is in place, else 1."""
    manifest = load_manifest() if manifest is None else manifest
    view = await install_all(tools, manifest, environ, renderer, fetch=fetch)
    if isinstance(renderer, InstallFullRenderer):
        try:
            await renderer.finish(view)
        except Exception as error:  # noqa: BLE001 - a broken screen must not hide the result
            console.say(f"A tela completa falhou ({error}); o resultado da instalação vale.")
    failed = view.summary.failed
    return int(ExitCode.FAILED) if failed else int(ExitCode.DELIVERED)


def refresh_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """``environ`` with the tools just installed in front of the PATH, in this process too."""
    fresh = with_installed_tools(environ)
    os.environ.update(fresh)  # this process only: the user's own PATH is not touched
    return fresh


async def instalar(
    console: Console,
    environ: Mapping[str, str],
    profile: Perfil,
    saved: Execucao | None = None,
    *,
    detection: Detection | None = None,
    manifest: Manifest | None = None,
    fetch: Fetch = https_fetch,
) -> int:
    """``forja instalar``: whatever is missing, installed now."""
    try:
        current_platform()
    except UnsupportedPlatform as error:
        console.say(f"Nesta plataforma a Forja não instala ferramentas ({error}).")
        return int(ExitCode.INVALID_CONFIG)
    manifest = load_manifest() if manifest is None else manifest
    tools = missing_tools(environ, manifest, detection)
    if not tools:
        console.say("Nada a instalar: as ferramentas da Forja já estão no lugar.")
        return int(ExitCode.DELIVERED)
    renderer = install_renderer_for(profile, saved, console)
    return await run_install(tools, environ, renderer, console, manifest=manifest, fetch=fetch)
