"""``forja agente``: the only commands the agent may run, each one fixed and narrow.

The agent has no shell of its own. It may call ``forja agente testar|lint|medir`` to see what the
Forja's own gates will see, on its copy of the code, with the output cut short. The arguments are
only paths inside the working directory; anything else is refused, so the permission to run
``forja agente`` cannot be turned into running something else.

``testar`` and ``lint`` run the command the language adapter of each project plans, the one the
gates run, whatever the language: nothing here knows one language from another.
"""

import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from forja.cli.session import languages
from forja.core.adapters import (
    CommandPlan,
    NotSupported,
    Operation,
    OperationRequest,
    Parsed,
    ToolOutput,
)
from forja.core.adapters.common import build_spec, read_output_lines
from forja.core.adapters.scoped import ScopedAdapter
from forja.core.process import Outcome, ProcessIdentity, ProcessRunner, UnsafeCommand
from forja.ui.glyphs import clean

PYTHON_VARIABLE = "FORJA_AGENT_PYTHON"
TIMEOUT_SECONDS = 600
TAIL_LINES = 80
NAMED = 20  # failed tests or lint violations named after the output of the tool
COMMANDS = {
    "testar": "roda os testes do projeto, parando no primeiro erro",
    "lint": "roda o lint do projeto",
    "medir": "mostra a complexidade ciclomática das funções (lizard)",
}
OPERATIONS = {"testar": Operation.TEST, "lint": Operation.LINT}
FAILED = ("failed", "error")
TIMED_OUT = 124
DID_NOT_START = 127


class Refused(Exception):
    """The call is not one the agent may make."""


class _Quiet:
    """The run of a tool for the agent is no event of the execution."""

    def intent(self, operation_id: str, details: Mapping[str, object]) -> None:
        pass

    def started(self, operation_id: str, identity: ProcessIdentity) -> None:
        pass

    def progress(self, operation_id: str, channel: str, line: str) -> None:
        pass

    def effect(self, operation_id: str, details: Mapping[str, object]) -> None:
        pass


def safe_paths(root: Path, given: Sequence[str]) -> list[str]:
    """Relative paths that exist under ``root``; nothing that looks like an option."""
    base = root.resolve()
    checked = []
    for item in given:
        if not item or item.startswith("-") or os.path.isabs(item) or ".." in Path(item).parts:
            raise Refused(f"caminho não permitido: {clean(item)!r}")
        target = (base / item).resolve()
        if not target.is_relative_to(base) or not target.exists():
            raise Refused(f"caminho não encontrado dentro do projeto: {clean(item)!r}")
        checked.append(item)
    return checked


def tail(text: str, lines: int = TAIL_LINES) -> str:
    kept = text.splitlines()
    cut = len(kept) - lines
    shown = kept[-lines:] if cut > 0 else kept
    return "\n".join(([f"[... {cut} linhas anteriores omitidas]"] if cut > 0 else []) + shown)


def projects_in(cwd: Path, environ: Mapping[str, str]) -> list[ScopedAdapter]:
    """The projects of the copy, each with the adapter the gates use. The tests of a Python
    project run with the interpreter of the repository, which has what they import."""
    python = environ.get(PYTHON_VARIABLE) or sys.executable
    return languages(cwd, lambda: (python,)).projects(cwd)


def asked_for(project: ScopedAdapter, paths: Sequence[str]) -> bool:
    """Whether the paths given call for this project: none given calls for every project; else
    one of them is inside the project, or is its directory, or one above it."""
    posix = [PurePosixPath(path.replace("\\", "/")).as_posix() for path in paths]
    return not posix or any(
        path == "." or project.holds(path) or f"{project.base}/".startswith(f"{path}/")
        for path in posix
    )


def plan_for(
    project: ScopedAdapter, operation: Operation, cwd: Path, paths: Sequence[str]
) -> CommandPlan | NotSupported:
    """What the adapter runs for the agent: the tests stop at the first that fails. With paths,
    only them where the tool can be told so; where it cannot, the whole project."""
    stop = operation is Operation.TEST
    whole = OperationRequest(root=cwd, rerun=stop)
    inside = tuple(path for path in paths if project.holds(path.replace("\\", "/")))
    if not inside:
        return project.plan(operation, whole)
    if operation is Operation.TEST:
        chosen = OperationRequest(root=cwd, rerun=stop, test_files=inside)
    else:
        chosen = OperationRequest(root=cwd, files=inside)
    plan = project.plan(operation, chosen)
    return project.plan(operation, whole) if isinstance(plan, NotSupported) else plan


def named(project: ScopedAdapter, operation: Operation, output: ToolOutput) -> list[str]:
    """What the adapter reads in the output, one line each: the tests that failed, or the lint
    violations by file and rule. Some tools print for a machine (``go test -json``) or to a
    report file (the lint of Maven), and these lines are then what tells the agent what is wrong.
    Empty when the output cannot be read."""
    parsed = project.parse(operation, output)
    if not isinstance(parsed, Parsed):
        return []
    if operation is Operation.TEST:
        return [
            f"falhou: {draft.target.id}"
            for draft in parsed.drafts
            if draft.measurement == "test.outcome" and draft.value in FAILED
        ]
    return [
        f"{draft.target.id}: {draft.measurement.removeprefix('lint.')} ({draft.value})"
        for draft in parsed.drafts
        if draft.measurement.startswith("lint.")
    ]


def _print(lines: Sequence[str]) -> None:
    if lines:
        print(chr(10).join(clean(line) for line in lines))


async def run_plan(
    plan: CommandPlan,
    environ: Mapping[str, str],
    project: ScopedAdapter | None = None,
    operation: Operation | None = None,
) -> int:
    """Run ``plan`` as the gates run a tool (filtered environment, the whole tree ended at the
    time limit) and show the end of its output, then what the adapter read in it."""
    with tempfile.TemporaryDirectory(prefix="forja-agente-", ignore_cleanup_errors=True) as folder:
        log = Path(folder) / "saida.log"
        spec = build_spec(plan, environ, timeout=TIMEOUT_SECONDS)
        try:
            result = await ProcessRunner(_Quiet()).run("agente", spec, log)
        except UnsafeCommand as unsafe:
            print(f"não consegui rodar: {clean(str(unsafe))}")
            return DID_NOT_START
        _print(tail(chr(10).join(read_output_lines(log))).splitlines())
        if result.outcome is Outcome.TIMED_OUT:
            print(f"interrompido: passou de {TIMEOUT_SECONDS} s")
            return TIMED_OUT
        if result.outcome is not Outcome.EXITED:
            print(f"não consegui rodar: {result.outcome.value}")
            return DID_NOT_START
        found: list[str] = []
        if project is not None and operation is not None:
            found = named(project, operation, ToolOutput(result.exit_code, log, plan.cwd))
        _print(found[:NAMED])
        if len(found) > NAMED:
            print(f"[... e mais {len(found) - NAMED}]")
    # a tool told not to fail on what it finds (the lint of Maven) still found it
    return result.exit_code or (1 if found else 0)


async def run_projects(
    name: str, paths: Sequence[str], cwd: Path, environ: Mapping[str, str]
) -> int:
    """``testar`` or ``lint`` on every project of the copy the paths call for. The tests stop at
    the first project that fails; the lint goes through them all."""
    operation = OPERATIONS[name]
    projects = [p for p in projects_in(cwd, environ) if asked_for(p, paths)]
    if not projects:
        print("nenhum projeto de linguagem conhecida neste diretório")
        return 2
    worst = 0
    for project in projects:
        if len(projects) > 1:
            print(f"== {project.language}: {project.base} ==")
        plan = plan_for(project, operation, cwd, paths)
        if isinstance(plan, NotSupported):
            print(f"{name} não se aplica a este projeto ({project.language}): {plan.reason}")
            continue
        code = await run_plan(plan, environ, project, operation)
        worst = worst or code
        if code and operation is Operation.TEST:
            break
    return worst


async def run_facade(argv: Sequence[str], cwd: Path, environ: Mapping[str, str]) -> int:
    if not argv or argv[0] not in COMMANDS:
        print("uso: forja agente testar|lint|medir [CAMINHO...]")
        return 2
    try:
        paths = safe_paths(cwd, argv[1:])
    except Refused as refused:
        print(refused)
        return 2
    if argv[0] in OPERATIONS:
        return await run_projects(argv[0], paths, cwd, environ)
    lizard = (sys.executable, "-m", "lizard", *(paths or ["."]))
    return await run_plan(
        CommandPlan(lizard, cwd, env_set={"PYTHONDONTWRITEBYTECODE": "1"}), environ
    )
