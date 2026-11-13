"""What ``open_session`` builds from a real repository, and the helpers it relies on.

No agent runs: the session is opened and looked at, with the real git and the real lock.
"""

import asyncio
import os
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from forja.cli import session as session_module
from forja.cli.session import (
    NETWORK_PROBES_WITHOUT_A_PERSON,
    PolicyChanged,
    Session,
    _test_root,
    executor_ref,
    in_scope,
    languages,
    missing_tool,
    new_config,
    open_session,
    policy_digest,
    repo_policy,
    repo_python,
)
from forja.core.artifacts.models import Producer
from forja.core.gitguard import GuardedGit
from forja.core.locking import LockOwner, MutexTimeout
from forja.core.locking.directory import release, try_acquire
from forja.core.locking.mutex import GIT_MUTEX_NAME
from forja.orchestrator.config import CreateRequest, FixRequest, RunConfig
from tests.cli.test_main import git

NL = chr(10)


def complex_function(name: str) -> str:
    branches = "".join(f"    if n == {i}:{NL}        return {i}{NL}" for i in range(40))
    return f"def {name}(n):{NL}{branches}    return -1{NL}"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "proj"
    (path / "src").mkdir(parents=True)
    (path / "lib").mkdir()
    (path / "src" / "a.py").write_text(complex_function("inside"), encoding="utf-8", newline="\n")
    (path / "lib" / "b.py").write_text(complex_function("outside"), encoding="utf-8", newline="\n")
    (path / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "0"\n', encoding="utf-8")
    git(path, "init", "-q", "-b", "main")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "init")
    return path


def head(repo: Path) -> str:
    done = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def config_for(repo: Path, **more: Any) -> RunConfig:
    return new_config(
        repo=repo,
        base_commit=head(repo),
        executor_name="claude-code",
        executor_path=Path("claude"),
        executor_version="2.0.0",
        tokens=more.pop("tokens", 1000),
        **more,
    )


async def _open(
    config: RunConfig | None, run_id: str | None, home: Path, interactive: bool = True
) -> Session:
    return await open_session(
        config, run_id=run_id, environ=dict(os.environ), interactive=interactive, home=home
    )


def close(session: Session) -> None:
    session.lock.release()
    session.log.close()


def test_a_new_run_lives_under_the_home_with_its_log_and_its_lock(
    repo: Path, tmp_path: Path
) -> None:
    home = tmp_path / "home" / "deep"

    session = asyncio.run(_open(config_for(repo), None, home))
    try:
        assert session.directory == home / "runs" / session.run_id
        assert (session.directory / "events.jsonl").is_file()
        assert session.lock.directory.parent.parent == home / "locks"
        assert session.view.branch == f"forja-run/{session.run_id}"
    finally:
        close(session)


def test_a_run_without_an_id_gets_a_new_one(repo: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"

    session = asyncio.run(_open(config_for(repo), None, home))
    try:
        assert session.run_id
        assert session.directory == home / "runs" / session.run_id
        assert session.resumed is False
    finally:
        close(session)


def test_a_new_run_needs_the_configuration_the_person_confirmed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="needs the configuration"):
        asyncio.run(_open(None, None, tmp_path / "home"))


def test_the_agent_gets_the_interpreter_of_the_repository_and_may_edit_files(
    repo: Path, tmp_path: Path
) -> None:
    session = asyncio.run(_open(config_for(repo), None, tmp_path / "home"))
    try:
        agent: Any = session.context.agent
        assert agent._env["FORJA_AGENT_PYTHON"] == repo_python(repo)[0]
        assert agent._permissions.can_edit_files is True
        assert session.context.run_dir == session.directory
    finally:
        close(session)


def test_the_environment_of_the_run_and_its_logs_live_in_the_run_directory(
    repo: Path, tmp_path: Path
) -> None:
    session = asyncio.run(_open(config_for(repo), None, tmp_path / "home"))
    try:
        measurer: Any = session.context.measurer
        assert measurer._workdir == session.directory / "measure"
    finally:
        close(session)


@pytest.mark.parametrize("interactive", [True, False])
def test_the_limit_stops_the_run_only_when_nobody_is_watching(
    repo: Path, tmp_path: Path, interactive: bool
) -> None:
    session = asyncio.run(_open(config_for(repo), None, tmp_path / "home", interactive))
    try:
        meter: Any = session.context.meter
        assert meter._enforce is (not interactive)
        expected = None if interactive else NETWORK_PROBES_WITHOUT_A_PERSON
        assert session.context.max_network_probes == expected
    finally:
        close(session)


def test_a_resumed_run_comes_back_with_its_configuration(repo: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    first = asyncio.run(_open(config_for(repo, scope=("src",)), None, home))
    close(first)

    again = asyncio.run(_open(None, first.run_id, home))
    try:
        assert again.resumed is True
        assert again.context.config.scope == ("src",)
        assert again.directory == first.directory
    finally:
        close(again)


def test_a_run_is_resumed_while_the_policy_is_the_one_it_was_confirmed_with(
    repo: Path, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    digest = policy_digest(repo_policy(repo))
    first = asyncio.run(_open(config_for(repo, policy_version=digest), None, home))
    close(first)

    again = asyncio.run(_open(None, first.run_id, home))

    close(again)


def test_a_run_is_not_resumed_when_the_policy_changed(repo: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    digest = policy_digest(repo_policy(repo))
    first = asyncio.run(_open(config_for(repo, policy_version=digest), None, home))
    close(first)
    (repo / "forja.toml").write_text(
        "[checks.tests.commands]" + NL + 'test = ["python", "-m", "pytest"]' + NL,
        encoding="utf-8",
    )

    with pytest.raises(PolicyChanged):
        asyncio.run(_open(None, first.run_id, home))

    # the lock was not left behind: the same run can be tried again
    (repo / "forja.toml").unlink()
    close(asyncio.run(_open(None, first.run_id, home)))


def test_a_run_whose_version_is_not_a_digest_is_resumed_whatever_the_policy(
    repo: Path, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    first = asyncio.run(_open(config_for(repo), None, home))
    close(first)
    (repo / "forja.toml").write_text(
        "[checks.tests.commands]" + NL + 'test = ["python", "-m", "pytest"]' + NL,
        encoding="utf-8",
    )

    close(asyncio.run(_open(None, first.run_id, home)))


def plan_intents(session: Session, repo: Path) -> str:
    planner: Any = session.context.planner
    tasks = asyncio.run(planner.plan(repo, None, session.context.config.budget))
    return NL.join(task.intent for task in tasks)


def test_the_plan_looks_at_the_whole_repository_when_no_scope_was_given(
    repo: Path, tmp_path: Path
) -> None:
    session = asyncio.run(_open(config_for(repo), None, tmp_path / "home"))
    try:
        intents = plan_intents(session, repo)
        assert "inside" in intents
        assert "outside" in intents
    finally:
        close(session)


def test_the_plan_looks_only_under_the_scope_when_one_was_given(repo: Path, tmp_path: Path) -> None:
    session = asyncio.run(_open(config_for(repo, scope=("src",)), None, tmp_path / "home"))
    try:
        intents = plan_intents(session, repo)
        assert "inside" in intents
        assert "outside" not in intents
    finally:
        close(session)


def test_nothing_is_in_scope_of_nothing_but_everything_is_when_there_is_no_scope() -> None:
    assert in_scope("src/a.py", ()) is True
    assert in_scope("src/a.py", ("lib",)) is False


@pytest.mark.parametrize(
    ("path", "scope", "expected"),
    [
        ("src/a.py", ("src",), True),
        ("src/a.py", ("src/",), True),
        ("src/a.py", ("src/a.py",), True),
        ("src/a.py", ("lib", "src"), True),
        ("src/a.py", ("sr",), False),
        ("src/ab.py", ("src/a",), False),
        ("srcx/a.py", ("src",), False),
        ("src", ("src",), True),
    ],
)
def test_a_file_is_in_scope_when_it_is_the_path_or_under_it(
    path: str, scope: tuple[str, ...], expected: bool
) -> None:
    assert in_scope(path, scope) is expected


def test_the_interpreter_of_the_repository_is_its_own_virtual_environment(
    tmp_path: Path,
) -> None:
    interpreter = tmp_path / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")

    assert repo_python(tmp_path) == (str(interpreter),)


def test_the_windows_layout_of_the_environment_is_found_too(tmp_path: Path) -> None:
    interpreter = tmp_path / "venv" / "Scripts" / "python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")

    assert repo_python(tmp_path) == (str(interpreter),)


def test_without_an_environment_nor_python_on_the_path_the_running_one_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_module, "locate_in_path", lambda name: None)

    assert repo_python(tmp_path) == (sys.executable,)


def test_a_tool_that_is_missing_is_an_absolute_path_that_is_not_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_module, "locate_in_path", lambda name: None)

    missing = Path(missing_tool("semgrep"))

    assert missing == Path(sys.prefix) / "semgrep-is-not-installed"
    assert missing.is_absolute()
    assert not missing.exists()


def test_a_tool_that_is_found_is_where_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = tmp_path / "gitleaks"
    monkeypatch.setattr(session_module, "locate_in_path", lambda name: found)

    assert missing_tool("gitleaks") == str(found)


def test_the_policy_names_the_test_root_when_it_has_one(tmp_path: Path) -> None:
    assert _test_root("spec", languages(tmp_path), tmp_path) == "spec"


def test_a_repository_that_is_one_python_project_keeps_its_tests_in_tests(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "p"\n', encoding="utf-8")

    assert _test_root(None, languages(tmp_path), tmp_path) == "tests"


def test_a_repository_of_two_languages_has_no_one_directory_for_tests(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "p"\n', encoding="utf-8")
    (tmp_path / "go.mod").write_text("module p" + NL, encoding="utf-8")

    assert _test_root(None, languages(tmp_path), tmp_path) == ""


def test_a_repository_that_is_not_python_has_no_one_directory_for_tests(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module p" + NL, encoding="utf-8")

    assert _test_root(None, languages(tmp_path), tmp_path) == ""


def test_the_test_author_runs_the_cli_chosen_for_it_when_a_fix_has_one(
    repo: Path, tmp_path: Path
) -> None:
    author = executor_ref("claude-code", Path("author-cli"), "2.0.0")
    config = config_for(repo, fix=FixRequest(description="bug"), test_author=author)

    session = asyncio.run(_open(config, None, tmp_path / "home"))
    try:
        runner: Any = session.context.agents[Producer.TEST_AUTHOR]
        assert runner._executable == Path("author-cli")
        agent: Any = session.context.agent
        assert agent._executable == Path("claude")
    finally:
        close(session)


def test_the_test_author_runs_the_cli_chosen_for_it_when_a_project_is_created(
    repo: Path, tmp_path: Path
) -> None:
    author = executor_ref("claude-code", Path("author-cli"), "2.0.0")
    request = CreateRequest(description="idea", language="python", name="p")
    config = config_for(repo, create=request, test_author=author)

    session = asyncio.run(_open(config, None, tmp_path / "home"))
    try:
        runner: Any = session.context.agents[Producer.TEST_AUTHOR]
        assert runner._executable == Path("author-cli")
    finally:
        close(session)


def test_the_test_author_is_the_implementer_when_none_was_chosen(
    repo: Path, tmp_path: Path
) -> None:
    config = config_for(repo, fix=FixRequest(description="bug"))

    session = asyncio.run(_open(config, None, tmp_path / "home"))
    try:
        runner: Any = session.context.agents[Producer.TEST_AUTHOR]
        assert runner._executable == Path("claude")
    finally:
        close(session)


def test_a_git_write_of_the_run_waits_for_the_mutex_of_the_repository(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[GuardedGit] = []

    class Recorded(GuardedGit):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            built.append(self)

    monkeypatch.setattr(session_module, "GuardedGit", Recorded)
    monkeypatch.setattr(session_module, "GIT_MUTEX_TIMEOUT", 0.2)

    async def scenario() -> None:
        session = await _open(config_for(repo), None, tmp_path / "home")
        try:
            (git_of_the_run,) = built
            held = session.lock.directory.parent / GIT_MUTEX_NAME
            other = LockOwner.current("another-task")
            try_acquire(held, other)
            try:
                with pytest.raises(MutexTimeout):
                    await git_of_the_run.run("branch", "forja/x/t1")
                assert (await git_of_the_run.run("status", "--porcelain")).exit_code == 0
            finally:
                release(held, other)
            assert (await git_of_the_run.run("branch", "forja/x/t1")).exit_code == 0
        finally:
            close(session)

    asyncio.run(scenario())


def test_the_run_draws_the_spread_of_its_waits_and_asks_the_cli_whether_the_network_is_back(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[tuple[str, Path]] = []
    answers: list[list[str]] = [["no answer"], []]

    async def probe(adapter: Any, executable: Path, environ: Any, **more: Any) -> list[str]:
        asked.append((adapter.name, executable))
        return answers.pop(0)

    monkeypatch.setattr(session_module, "run_probe", probe)
    session = asyncio.run(_open(config_for(repo), None, tmp_path / "home"))
    try:
        context = session.context
        assert context.jitter is random.random

        async def ask() -> bool:
            return await context.network_is_up()

        assert asyncio.run(ask()) is False  # the proof of life found a problem
        assert asyncio.run(ask()) is True
        assert asked == [("claude-code", Path("claude"))] * 2
    finally:
        close(session)
