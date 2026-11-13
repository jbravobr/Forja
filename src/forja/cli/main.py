"""``forja``: the door. It detects, asks what it must, proves the agent, confirms once and runs.

Exit codes are the ones of ``ExitCode``: 0 delivered, 1 failed, 2 could not start (something to
fix), 3 stopped with everything saved, 130 stopped by the person.
"""

import asyncio
import os
import sys
from collections.abc import Awaitable, Mapping
from pathlib import Path

from forja.cli.args import Arguments, parse
from forja.cli.awake import keep_awake
from forja.cli.console import (
    Console,
    TerminalConsole,
    Unattended,
    choose,
    confirm,
    lenient_output,
)
from forja.cli.detect import Detection, detect
from forja.cli.doctor import doctor
from forja.cli.facade import run_facade
from forja.cli.install import (
    instalar,
    install_renderer_for,
    missing_tools,
    offer_line,
    refresh_environment,
    run_install,
)
from forja.cli.keys import terminal_keys
from forja.cli.prepare import Prepared, Refused, confirmed, prepare, probe_cache
from forja.cli.resume import Unfinished, discard, find_unfinished, removal_commands
from forja.cli.review import revisao
from forja.cli.run import Ended, ForcedExit, Renderer, closing_lines, execute, exit_code
from forja.cli.scaffold import ScaffoldError, scaffold
from forja.cli.session import PolicyChanged, Session, open_session
from forja.cli.settings import (
    Execucao,
    Perfil,
    SettingsError,
    load_execucao,
    load_perfil,
    profile_dir,
    save_execucao,
    save_perfil,
)
from forja.core.adapters.executor import ExecutorNotFound
from forja.core.locking.repository import RepositoryBusy
from forja.core.paths import forja_home
from forja.core.policy.config import PolicyConfigError
from forja.orchestrator.outcome import ExitCode
from forja.toolchain.manifest import Tool
from forja.toolchain.resolve import with_installed_tools
from forja.ui import terminal
from forja.ui.full import FullRenderer
from forja.ui.live import LiveRenderer
from forja.ui.plain import PlainRenderer
from forja.ui.terminal import Tier
from forja.ui.theme import theme_named, with_ascii


def renderer_for(profile: Perfil, saved: Execucao | None, console: Console) -> Renderer:
    """The screen for this terminal: the full screen when there is room, then the live line,
    then plain text for a pipe or CI."""
    caps = terminal.detect()
    style = saved.interface if saved else profile.interface
    forced = None if style.modo == "auto" else Tier(style.modo)
    tier, why = terminal.choose_tier(caps, forced, full_available=True)
    if why:
        console.say(why)
    if tier is Tier.TEXT:
        return PlainRenderer(sys.stdout)
    theme = theme_named(style.tema or None, dark_background=caps.dark_background)
    if style.ascii or not caps.unicode:
        theme = with_ascii(theme)
    if tier is Tier.FULL:
        return FullRenderer(theme, caps, resize=style.redimensionar)
    return LiveRenderer(theme, caps)


async def _pending(pending: list[Unfinished], console: Console, *, automatic: bool) -> str:
    """``resume``, ``discard`` or ``new`` for the run left unfinished in this repository."""
    latest = pending[0]
    console.say(
        f"Há uma execução incompleta neste repositório ({latest.run_id}, em {latest.state}"
        + (f": {latest.reason}" if latest.reason else "")
        + ")."
    )
    if automatic:
        raise Refused("Resolva a execução incompleta (rode `forja` sem --config) antes.")
    answer = choose(console, "O que fazer?", ["retomar de onde parou", "descartar e recomeçar"])
    return "resume" if answer == 0 else "discard"


def _remember(prepared: Prepared, profile: Perfil, console: Console, home: Path) -> None:
    if prepared.remember is None:
        return
    name, path = prepared.remember
    if confirm(console, f"Lembrar de usar sempre {path} para {name}?"):
        profile.cli = name
        profile.executaveis[name] = path
        save_perfil(home, profile)


def _finish(session: Session, ended: Ended, console: Console) -> int:
    for line in closing_lines(session, ended):
        console.say(line)
    return exit_code(session, ended)


async def _run(session: Session, renderer: Renderer, console: Console) -> int:
    keys = renderer.keys_source if isinstance(renderer, FullRenderer) else terminal_keys()
    try:
        with keep_awake():
            ended = await execute(session, renderer, keys)
    except ForcedExit:
        if isinstance(renderer, FullRenderer):
            renderer.abort()
            await _screen(renderer.closed(), console)
        console.say(
            "Saiu sem esperar. O que já estava gravado está salvo; rode `forja` para retomar."
        )
        return int(ExitCode.INTERRUPTED)
    finally:
        if keys is not None:
            keys.close()
        session.close()
    lines = closing_lines(session, ended)
    if isinstance(renderer, FullRenderer):
        await _screen(renderer.finish(lines), console)
    return _finish(session, ended, console)


async def _screen(waiting: Awaitable[None], console: Console) -> None:
    """Wait for the full screen to close; if it broke, the run is unaffected and this says so."""
    try:
        await waiting
    except Exception as error:  # noqa: BLE001 - a broken screen must never lose the result
        console.say(f"A tela completa falhou ({error}); o resultado da execução continua valendo.")


async def _discard(latest: Unfinished, console: Console) -> None:
    discard(latest.directory)
    console.say(f"Execução {latest.run_id} descartada. Nada foi apagado. Para limpar as branches:")
    for command in removal_commands(latest.run_id):
        console.say(f"  {command}")


async def _install_offered(
    tools: list[Tool],
    saved: Execucao | None,
    profile: Perfil,
    console: Console,
    environ: Mapping[str, str],
    *,
    automatic: bool,
) -> Mapping[str, str]:
    """Install what the person just confirmed (ADR 0037). Without a terminal, only when the
    ``execucao.toml`` asks; the Forja never installs on its own."""
    if not tools:
        return environ
    if automatic and not (saved and saved.instalar_ferramentas):
        console.say(
            "Sem terminal a Forja só instala com `instalar_ferramentas = true` no execucao.toml; "
            "ou rode `forja instalar`. Seguindo sem instalar."
        )
        return environ
    renderer = install_renderer_for(profile, saved, console)
    if await run_install(tools, environ, renderer, console) != 0:
        raise Refused(
            "A instalação das ferramentas falhou. Nada foi começado; resolva e rode de novo."
        )
    return refresh_environment(environ)


async def start(
    args: Arguments, console: Console, environ: Mapping[str, str], detection: Detection
) -> int:
    home = profile_dir(environ)
    profile = load_perfil(home)
    saved = load_execucao(args.config) if args.config else None
    automatic = args.config is not None or not console.interactive
    if automatic:
        console = Unattended(console)
    if detection.repo is not None and not args.plan_only:
        pending = find_unfinished(forja_home(environ=environ), detection.repo)
        if pending:
            if await _pending(pending, console, automatic=automatic) == "resume":
                session = await open_session(
                    None, run_id=pending[0].run_id, environ=environ, interactive=not automatic
                )
                return await _run(session, renderer_for(profile, saved, console), console)
            await _discard(pending[0], console)
    prepared = await prepare(args, detection, profile, home, console, environ, saved=saved)
    to_install = missing_tools(environ, detection=detection)
    if to_install:
        console.say(offer_line(to_install, environ))
    if args.plan_only:
        for line in prepared.summary:
            console.say(line)
        return 0
    if args.config is None and automatic:
        raise Refused(
            "Sem terminal não há quem confirme. Rode `forja --config execucao.toml`, "
            "ou num terminal. Nada foi alterado."
        )
    if not confirmed(console, prepared, automatic=automatic):
        console.say("Cancelado. Nada foi alterado.")
        return 0
    _remember(prepared, profile, console, home)
    environ = await _install_offered(
        to_install, saved, profile, console, environ, automatic=automatic
    )
    config = prepared.config
    if config.create is not None:
        try:
            head = await scaffold(Path(config.repo), config.create, environ)
        except ScaffoldError as error:
            raise Refused(error.message) from None
        console.say(f"Projeto {config.create.name} criado e comitado ({head[:7]}).")
        config = config.model_copy(update={"base_commit": head})
    session = await open_session(config, environ=environ, interactive=not automatic)
    save_execucao(session.directory / "execucao.toml", prepared.execucao)
    return await _run(session, renderer_for(profile, saved, console), console)


async def amain(args: Arguments, console: Console, environ: Mapping[str, str]) -> int:
    try:
        detection = await detect(Path.cwd(), environ)
        if args.command == "doctor":
            profile = load_perfil(profile_dir(environ))
            cache = probe_cache(profile_dir(environ))
            return await doctor(
                detection,
                profile,
                environ,
                console,
                cache,
                cli=args.cli,
                semgrep_config=args.semgrep_config,
            )
        if args.command == "instalar":
            return await instalar(
                console, environ, load_perfil(profile_dir(environ)), detection=detection
            )
        if args.command == "agente":
            return run_facade(args.agent, Path.cwd(), environ)
        if args.command == "revisao":
            return await revisao(args, console, environ, detection)
        return await start(args, console, environ, detection)
    except Refused as refused:
        console.say(refused.message)
        return refused.code
    except (PolicyChanged, PolicyConfigError) as error:
        console.say(str(error))
        return int(ExitCode.INVALID_CONFIG)
    except SettingsError as error:
        console.say(str(error))
        return int(ExitCode.INVALID_CONFIG)
    except ExecutorNotFound as error:
        console.say(
            f"O executável indicado ({error.source.value}) não existe: {error.path}. "
            "Corrija o caminho ou retire a indicação para a Forja procurar sozinha."
        )
        return int(ExitCode.INVALID_CONFIG)
    except RepositoryBusy:
        console.say("Já há uma execução da Forja em andamento neste repositório.")
        return int(ExitCode.INVALID_CONFIG)


def main(argv: list[str] | None = None) -> int:
    lenient_output()
    args = parse(argv)
    environ = with_installed_tools(os.environ)
    os.environ.update(environ)  # this process only: the user's own PATH is not touched
    try:
        return asyncio.run(amain(args, TerminalConsole(), environ))
    except KeyboardInterrupt:
        return int(ExitCode.INTERRUPTED)


if __name__ == "__main__":
    sys.exit(main())
