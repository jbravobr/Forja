"""What can be found out without asking: the repository, the language, the tools, the CLIs.

The preparation asks only what cannot be detected. Each fact here has one honest answer, even
when the answer is "not here": a missing tool is a line the doctor explains, not an exception.
"""

import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from forja.cli.session import git_base_environment, git_executable
from forja.core.adapters.executor import (
    CommandTemplate,
    ExecutorChoice,
    find_executors,
    locate_in_path,
)
from forja.core.adapters.projects import project_directories
from forja.core.idempotency.git import Git
from forja.executors import adapter_for
from forja.languages.java.gradle import MANIFESTS as GRADLE_MANIFESTS


@dataclass(frozen=True)
class Detection:
    cwd: Path
    repo: Path | None  # the root of the git repository, if the directory is in one
    has_commits: bool
    branch: str
    head: str
    changed: tuple[str, ...]  # paths with uncommitted changes
    python: bool
    gitleaks: bool
    git: bool
    disk_free_gib: float
    go: bool = False
    typescript: bool = False
    csharp: bool = False
    java: bool = False
    maven: bool = False  # a pom.xml somewhere
    gradle: bool = False  # a Gradle build in a directory that carries no wrapper
    jscpd: bool = False

    @property
    def greenfield(self) -> bool:
        """An empty directory or a repository with no commit yet."""
        return (
            self.repo is None
            and not any(self.cwd.iterdir())
            or (self.repo is not None and not self.has_commits)
        )


async def detect(cwd: Path, environ: Mapping[str, str]) -> Detection:
    with tempfile.TemporaryDirectory(prefix="forja-detect-") as scratch:  # private, then gone
        return await _detect(cwd, environ, Path(scratch))


async def _detect(cwd: Path, environ: Mapping[str, str], log_dir: Path) -> Detection:
    git_found = locate_in_path("git") is not None
    free = shutil.disk_usage(cwd).free / 2**30
    base = dict(
        cwd=cwd, python=False, gitleaks=locate_in_path("gitleaks") is not None, git=git_found,
        disk_free_gib=free, jscpd=locate_in_path("jscpd") is not None,
    )  # fmt: skip
    if not git_found:
        return Detection(repo=None, has_commits=False, branch="", head="", changed=(), **base)  # type: ignore[arg-type]
    env = git_base_environment(environ)
    git = Git(cwd, executable=git_executable(), env=env, timeout=30, log_dir=log_dir)
    top = await git.run("rev-parse", "--show-toplevel")
    if top.exit_code != 0 or not top.lines:
        return Detection(repo=None, has_commits=False, branch="", head="", changed=(), **base)  # type: ignore[arg-type]
    root = Path(top.lines[0].strip())
    git = Git(root, executable=git_executable(), env=env, timeout=30, log_dir=log_dir)
    head = await git.run("rev-parse", "HEAD")
    has_commits = head.exit_code == 0
    branch = await git.run("rev-parse", "--abbrev-ref", "HEAD")
    changed = await git.lines("-c", "core.fsmonitor=false", "status", "--porcelain")
    for directory in project_directories(root):
        for language, found in _languages_in(directory).items():
            base[language] = base.get(language, False) or found
    return Detection(
        repo=root,
        has_commits=has_commits,
        branch=branch.lines[0].strip() if branch.exit_code == 0 and branch.lines else "",
        head=head.lines[0].strip() if has_commits and head.lines else "",
        changed=tuple(line[3:] for line in changed),
        **base,  # type: ignore[arg-type]
    )


def _languages_in(directory: Path) -> dict[str, bool]:
    return {
        "python": (directory / "pyproject.toml").is_file(),
        "go": (directory / "go.mod").is_file(),
        "java": any((directory / name).is_file() for name in ("pom.xml", *GRADLE_MANIFESTS)),
        "maven": (directory / "pom.xml").is_file(),
        "gradle": any((directory / name).is_file() for name in GRADLE_MANIFESTS)
        and not any((directory / name).is_file() for name in ("gradlew", "gradlew.bat")),
        "csharp": any(directory.glob("*.sln")) or any(directory.glob("*.csproj")),
        "typescript": (directory / "package.json").is_file()
        and (directory / "tsconfig.json").is_file(),
    }


def installed_clis(
    name: str,
    environ: Mapping[str, str],
    configured: Mapping[str, str],
    explicit: Path | None = None,
    template: CommandTemplate | None = None,
) -> list[ExecutorChoice]:
    """The installations of a CLI the user could mean, in order of precedence."""
    adapter = adapter_for(name, template)
    return find_executors(adapter, explicit=explicit, environ=environ, user_config=configured)
