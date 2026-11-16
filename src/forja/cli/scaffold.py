"""The scaffold of ``forja criar``: an empty directory becomes a project that builds and passes.

No agent takes part. The files come from the language's template, the commit is made with git as
it is (the repository is not the run's yet, so there is nothing to guard), and what the template
needs from the environment (``setup``, such as ``npm install``) is run in the project's root.
The run that follows measures that build and those tests as its baseline; one that is not green
ends the run there, as for any other repository.
"""

import asyncio
import tempfile
from collections.abc import Mapping
from pathlib import Path

from forja.cli.session import git_base_environment, git_executable
from forja.core.adapters import LanguageAdapter, ProjectTemplate
from forja.core.adapters.executor import locate_in_path
from forja.core.idempotency.git import Git
from forja.languages import (
    CSharpAdapter,
    GoAdapter,
    JavaAdapter,
    PythonAdapter,
    TypeScriptAdapter,
)
from forja.orchestrator.config import CreateRequest
from forja.orchestrator.workspace_git import detect_identity, git_environment

FIRST_BRANCH = "main"
SETUP_SECONDS = 900.0
SCAFFOLDER = "andaime"


class ScaffoldError(Exception):
    """The scaffold cannot be made, or stopped half way; ``message`` says what exists and why."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def template_adapters() -> dict[str, LanguageAdapter]:
    """The languages a new project can be made in, by name. JavaScript is not one of them: its
    adapter is the TypeScript one, and the model that adapter has is a TypeScript project."""
    adapters: tuple[LanguageAdapter, ...] = (
        PythonAdapter(),
        GoAdapter(),
        TypeScriptAdapter(),
        CSharpAdapter(),
        JavaAdapter(),
    )
    return {adapter.language: adapter for adapter in adapters}


def project_template(language: str, name: str) -> ProjectTemplate:
    return template_adapters()[language].template(name)


def leftovers(root: Path) -> list[str]:
    """What is in ``root`` besides the ``.git`` directory; a scaffold needs there to be nothing."""
    return sorted(item.name for item in root.iterdir() if item.name != ".git")


def _write(root: Path, template: ProjectTemplate) -> None:
    for relative, text in template.files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")


async def _setup(root: Path, argv: tuple[str, ...], environ: Mapping[str, str]) -> None:
    program = locate_in_path(argv[0])
    if program is None:
        raise ScaffoldError(
            f"`{argv[0]}` não está instalado ou não está no PATH; ele prepara o ambiente do "
            f"projeto ({' '.join(argv)}). O projeto foi criado e comitado em {root}."
        )
    process = await asyncio.create_subprocess_exec(
        str(program),
        *argv[1:],
        cwd=root,
        env=dict(environ),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), SETUP_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise ScaffoldError(
            f"`{' '.join(argv)}` passou de {int(SETUP_SECONDS)} s e foi interrompido. O projeto "
            f"foi criado e comitado em {root}."
        ) from None
    if process.returncode != 0:
        tail = " | ".join(output.decode("utf-8", "replace").strip().splitlines()[-3:])
        raise ScaffoldError(
            f"`{' '.join(argv)}` falhou ({process.returncode}): {tail}. O projeto foi criado e "
            f"comitado em {root}."
        )


async def scaffold(root: Path, request: CreateRequest, environ: Mapping[str, str]) -> str:
    """Write the project of ``request`` in ``root``, commit it and set up its environment; the
    commit it made. ``root`` has nothing in it but, at most, a repository with no commit."""
    if extra := leftovers(root):
        raise ScaffoldError(
            f"{root} precisa estar vazio para criar um projeto, mas tem: {', '.join(extra[:5])}."
        )
    template = project_template(request.language, request.name)
    with tempfile.TemporaryDirectory(prefix="forja-scaffold-") as scratch:
        base = git_base_environment(environ)
        identity = await detect_identity(
            Git(root, executable=git_executable(), env=base, timeout=30, log_dir=Path(scratch)),
            SCAFFOLDER,
        )
        git = Git(
            root,
            executable=git_executable(),
            env=git_environment(base, identity),
            timeout=120,
            log_dir=Path(scratch),
        )
        _write(root, template)
        # A repository that was already there is the person's, and its hooks and fsmonitor are
        # code: none of it runs for the scaffold.
        hooks = Path(scratch) / "no-hooks"
        hooks.mkdir()
        safe = ("-c", f"core.hooksPath={hooks}", "-c", "core.fsmonitor=false")
        if not (root / ".git").exists():
            await git.lines(*safe, "init", "-q", f"--initial-branch={FIRST_BRANCH}")
        await git.lines(*safe, "add", "--all")
        await git.lines(
            *safe,
            "commit",
            "-q",
            "--no-verify",
            "-m",
            f"chore: scaffold {request.language} project {request.name}",
        )
        head = (await git.lines("rev-parse", "HEAD"))[0].strip()
    for argv in template.setup:
        await _setup(root, argv, environ)
    return head
