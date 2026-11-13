"""``forja doctor``: everything the Forja needs, checked, and what to do for each thing missing.

The configuration of the agent CLI (login, model, gateway) is the person's and must be ready
before; the doctor proves it works with a small task and says so when it does not. Nothing is
installed here: a missing tool the Forja installs points to ``forja instalar``, the others to
their own instructions, and a tool found in the Forja's own folder is said to be the one the Forja
installed.
"""

import asyncio
import importlib.util
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from forja.cli.console import Console
from forja.cli.detect import Detection, installed_clis
from forja.cli.prepare import Refused, prove
from forja.cli.semgrep_rules import RULE_DIR, RULE_FILE
from forja.cli.session import LOW_DISK_GIB, repo_python
from forja.cli.settings import Perfil
from forja.core.adapters.executor import locate_in_path
from forja.executors import DEFAULT_CLI, KNOWN_CLIS, adapter_for
from forja.executors.probe import executable_version, version_problem
from forja.toolchain.install import tools_dir
from forja.toolchain.resolve import installed_by_forja

SEMGREP_HELP = "https://semgrep.dev/docs/getting-started/cli"
INSTALL_HINT = "rode `forja instalar`"
OSV_HELP = "https://github.com/google/osv-scanner"
GITLEAKS_HELP = "https://github.com/gitleaks/gitleaks"
JSCPD_HELP = "https://github.com/kucherenko/jscpd"
MYPY_HELP = "https://mypy.readthedocs.io/en/stable/getting_started.html"
GIT_HELP = "https://git-scm.com/downloads"
GO_HELP = "https://go.dev/doc/install"
GOLANGCI_HELP = "https://golangci-lint.run/welcome/install/"
NODE_HELP = "https://nodejs.org/en/download"
DOTNET_HELP = "https://dotnet.microsoft.com/download"
JAVA_HELP = "https://adoptium.net/installation/"
MAVEN_HELP = "https://maven.apache.org/install.html"
GRADLE_HELP = "https://gradle.org/install/"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    required: bool = True


def _install_fix(help_url: str) -> str:
    return f"{INSTALL_HINT} ou instale à mão, conforme {help_url}"


def _module(name: str, purpose: str) -> Check:
    found = importlib.util.find_spec(name) is not None
    return Check(
        name,
        found,
        f"{purpose} (vem com a Forja)" if found else f"{purpose}: não encontrado",
        "" if found else "reinstale a Forja",
    )


async def _pytest_in(repo: Path) -> Check:
    python = repo_python(repo)
    process = await asyncio.create_subprocess_exec(
        *python,
        "-c",
        "import pytest",
        cwd=repo,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    ok = await process.wait() == 0
    return Check(
        "pytest no repositório",
        ok,
        f"{python[0]} importa pytest" if ok else f"{python[0]} não importa pytest",
        "" if ok else "crie o ambiente do projeto e instale as dependências de teste nele",
    )


def _where(command: str, environ: Mapping[str, str]) -> str:
    """Where a tool that was found is: in the Forja's own folder, or on the PATH."""
    found = locate_in_path(command, environ.get("PATH"))
    if found is not None and installed_by_forja(found, tools_dir(environ=environ)):
        return f"{command} instalado pela Forja ({found})"
    return f"{command} no PATH"


def _mypy_check(environ: Mapping[str, str]) -> Check:
    found = locate_in_path("mypy", environ.get("PATH")) is not None
    return Check(
        "mypy",
        found,
        _where("mypy", environ) if found else "mypy ausente: a análise de tipos fica inconclusiva",
        "" if found else _install_fix(MYPY_HELP),
        required=False,
    )


def _osv_check(environ: Mapping[str, str]) -> Check:
    found = locate_in_path("osv-scanner", environ.get("PATH")) is not None
    return Check(
        "osv-scanner",
        found,
        _where("osv-scanner", environ)
        if found
        else "osv-scanner ausente: a auditoria de dependências fica inconclusiva",
        "" if found else _install_fix(OSV_HELP),
        required=False,
    )


def _go_checks(environ: Mapping[str, str]) -> list[Check]:
    path = environ.get("PATH")
    found = locate_in_path("go", path) is not None
    lint = locate_in_path("golangci-lint", path) is not None
    return [
        Check(
            "go",
            found,
            _where("go", environ) if found else "go ausente",
            "" if found else _install_fix(GO_HELP),
        ),
        Check(
            "golangci-lint",
            lint,
            _where("golangci-lint", environ)
            if lint
            else "golangci-lint ausente: o lint de Go fica não suportado",
            "" if lint else _install_fix(GOLANGCI_HELP),
            required=False,
        ),
    ]


def _node_checks(environ: Mapping[str, str]) -> list[Check]:
    found = locate_in_path("node", environ.get("PATH")) is not None
    return [
        Check(
            "node",
            found,
            _where("node", environ) if found else "node ausente",
            "" if found else _install_fix(NODE_HELP),
        )
    ]


def _dotnet_checks(environ: Mapping[str, str]) -> list[Check]:
    found = locate_in_path("dotnet", environ.get("PATH")) is not None
    return [
        Check(
            "dotnet",
            found,
            _where("dotnet", environ) if found else "dotnet ausente",
            "" if found else _install_fix(DOTNET_HELP),
        )
    ]


def _java_checks(environ: Mapping[str, str], root: Path | None = None) -> list[Check]:
    """Java, and the build tool the root's manifest picks: Maven, unless only a Gradle build
    file is there (the adapter's own choice, ``pom.xml`` winning)."""
    java = locate_in_path("java", environ.get("PATH")) is not None
    checks = [
        Check(
            "java",
            java,
            _where("java", environ) if java else "java ausente",
            "" if java else _install_fix(JAVA_HELP),
        )
    ]
    if root is not None and not (root / "pom.xml").is_file():
        return [*checks, _gradle_check(environ, root)]
    maven = locate_in_path("mvn", environ.get("PATH")) is not None
    checks.append(
        Check(
            "maven",
            maven,
            _where("mvn", environ) if maven else "mvn ausente",
            "" if maven else _install_fix(MAVEN_HELP),
        )
    )
    return checks


def _gradle_check(environ: Mapping[str, str], root: Path) -> Check:
    wrapper = root / ("gradlew.bat" if sys.platform == "win32" else "gradlew")
    if wrapper.is_file():
        return Check("gradle", True, f"{wrapper.name} do repositório")
    found = locate_in_path("gradle", environ.get("PATH")) is not None
    return Check(
        "gradle",
        found,
        _where("gradle", environ) if found else "gradle ausente e o repositório não tem gradlew",
        "" if found else _install_fix(GRADLE_HELP),
    )


async def run_checks(
    detection: Detection,
    profile: Perfil,
    environ: Mapping[str, str],
    console: Console,
    cache: Path,
    *,
    cli: str = "",
    semgrep_config: str = "",
) -> list[Check]:
    name = cli or profile.cli or DEFAULT_CLI
    checks = [
        Check("git", detection.git, "git no PATH" if detection.git else "git ausente", GIT_HELP),
        _module("ruff", "lint do código do projeto"),
        _module("lizard", "complexidade ciclomática"),
        Check(
            "gitleaks",
            detection.gitleaks,
            _where("gitleaks", environ) if detection.gitleaks else "gitleaks ausente",
            "" if detection.gitleaks else _install_fix(GITLEAKS_HELP),
        ),
        Check(
            "jscpd",
            detection.jscpd,
            _where("jscpd", environ) if detection.jscpd else "jscpd ausente",
            "" if detection.jscpd else _install_fix(JSCPD_HELP),
        ),
        _osv_check(environ),
        Check(
            "disco",
            detection.disk_free_gib >= LOW_DISK_GIB,
            f"{detection.disk_free_gib:.1f} GiB livres",
            f"libere ao menos {LOW_DISK_GIB:.0f} GiB",
        ),
    ]
    if detection.repo is not None:
        checks.append(
            Check(
                "repositório limpo",
                not detection.changed,
                "sem alterações não salvas"
                if not detection.changed
                else f"{len(detection.changed)} arquivo(s) alterado(s)",
                "faça commit ou stash antes de rodar a Forja",
            )
        )
        if detection.python:
            checks.append(await _pytest_in(detection.repo))
            checks.append(_mypy_check(environ))
        if detection.go:
            checks += _go_checks(environ)
        if detection.typescript:
            checks += _node_checks(environ)
        if detection.csharp:
            checks += _dotnet_checks(environ)
        if detection.java:
            checks += _java_checks(environ, detection.repo)
    if semgrep_config or (detection.repo and _has_rules(detection.repo)):
        found = locate_in_path("semgrep", environ.get("PATH")) is not None
        checks.append(
            Check(
                "semgrep",
                found,
                _where("semgrep", environ) if found else "semgrep ausente",
                "" if found else _install_fix(SEMGREP_HELP),
            )
        )
    checks.append(await _agent_check(name, profile, environ, console, cache))
    checks += await _other_clis(name, profile, environ)
    return checks


async def _other_clis(chosen: str, profile: Perfil, environ: Mapping[str, str]) -> list[Check]:
    """The other known CLIs that are installed: a run can give one of them the tests. They are
    only listed here; the proof of life runs when a run uses one."""
    checks = []
    for name, make in KNOWN_CLIS.items():
        found = installed_clis(name, environ, profile.executaveis) if name != chosen else []
        if found:
            version = await executable_version(make(), found[0].path)
            checks.append(
                Check(
                    f"CLI {name}", True, f"{found[0].path} ({version or '?'}) instalada", "", False
                )
            )
    return checks


async def _agent_check(
    name: str, profile: Perfil, environ: Mapping[str, str], console: Console, cache: Path
) -> Check:
    label = f"CLI {name}"
    template = profile.clis[name].template() if name in profile.clis else None
    if name not in KNOWN_CLIS and template is None:
        return Check(
            label,
            False,
            "CLI desconhecida",
            f"conheço: {', '.join((*KNOWN_CLIS, *profile.clis))}",
        )
    found = installed_clis(name, environ, profile.executaveis, template=template)
    if not found:
        return Check(
            label,
            False,
            "não encontrada no PATH",
            "instale a CLI e entre na sua conta antes; a Forja não faz isso por você",
        )
    adapter = adapter_for(name, template)
    version = await executable_version(adapter, found[0].path)
    if problem := version_problem(adapter, version):
        return Check(label, False, problem, "atualize a CLI")
    try:
        await prove(name, found[0], environ, cache, console, template)
    except Refused as refused:
        return Check(label, False, refused.message, "corrija a CLI e rode `forja doctor`")
    return Check(label, True, f"{found[0].path} ({version}) responde a uma tarefa de teste")


def terminal_note(platform: str, environ: Mapping[str, str]) -> str:
    return (
        "A Forja roda neste terminal e não cria sandbox: leitura fora da cópia e rede são "
        "garantidas pelo seu ambiente."
    )


def render(checks: list[Check], note: str = "") -> list[str]:
    lines = []
    for check in checks:
        mark = "ok " if check.ok else "FALTA"
        lines.append(f"[{mark}] {check.name}: {check.detail}")
        if not check.ok and check.fix:
            lines.append(f"        -> {check.fix}")
    if note:
        lines += ["", note]
    missing = [check for check in checks if not check.ok and check.required]
    lines += ["", "Tudo pronto." if not missing else f"{len(missing)} item(ns) a resolver."]
    return lines


async def doctor(
    detection: Detection,
    profile: Perfil,
    environ: Mapping[str, str],
    console: Console,
    cache: Path,
    *,
    cli: str = "",
    semgrep_config: str = "",
) -> int:
    checks = await run_checks(
        detection, profile, environ, console, cache, cli=cli, semgrep_config=semgrep_config
    )
    for line in render(checks, terminal_note(sys.platform, environ)):
        console.say(line)
    return 0 if all(check.ok or not check.required for check in checks) else 1


def _has_rules(repo: Path) -> bool:
    """Whether the repository carries Semgrep rules, which is when the run needs ``semgrep``."""
    return (repo / RULE_FILE).is_file() or (repo / RULE_DIR).is_dir()
