"""The agent runner with a real process standing in for the CLI: copy, call and way back."""

import asyncio
import json
import sys
import time
from collections.abc import Awaitable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from forja.core.adapters.common import CommandPlan
from forja.core.adapters.executor import (
    AgentRequest,
    ErrorKind,
    PermissionSetup,
    RolePermissions,
    UsageActivity,
)
from forja.core.artifacts.models import Producer
from forja.core.events import EventLog, OpenOperation
from forja.core.idempotency import Decision
from forja.core.manifest import EntryKind, Manifest, ManifestDiff, ManifestEntry
from forja.core.states import ExecutionState, Level
from forja.executors import ClaudeCodeAdapter
from forja.orchestrator.agent_runner import (
    PACKAGE,
    ExecutorAgentRunner,
    _payload,
    _refuse_links_on_the_way,
    _wipe,
    touches_git,
    workcopy_of,
)
from forja.orchestrator.budget import read_consumption
from forja.orchestrator.operations import Step
from forja.orchestrator.ports import AgentResult, AttemptContext, RejectedWork, TaskInfo
from tests.checks.support import RecordingSink
from tests.core.events.support import FakeClock
from tests.orchestrator.harness import MemoryWorld

pytestmark = pytest.mark.integration

FAKE = str(Path(__file__).with_name("fake_claude.py"))
FIXTURE = Path(__file__).with_name("fixtures") / "claude_auth_failed.jsonl"
BEFORE = "def f(a, b):\n    return a + b\n"
AFTER = "def add(a, b):\n    return a + b\n"
DECLARED = {
    "actions": [
        {
            "summary": "rename f",
            "reason": "the name says what it does",
            "files": [{"path": "src/a.py", "change": "modified"}],
        }
    ]
}
USAGE = json.dumps(
    {"type": "result", "is_error": False, "usage": {"input_tokens": 100, "output_tokens": 20}}
)


class FakeCli(ClaudeCodeAdapter):
    """The real adapter, with the program swapped for the stand-in."""

    def __init__(self, script: Path) -> None:
        self._script = script

    def plan_invocation(
        self, request: AgentRequest, setup: PermissionSetup, executable: Path
    ) -> CommandPlan:
        plan = super().plan_invocation(request, setup, executable)
        env = {**plan.env_set, "FAKE_CLAUDE_SCRIPT": str(self._script)}
        return replace(plan, argv=(sys.executable, FAKE, *plan.argv[1:]), env_set=env)


@dataclass
class Rig:
    root: Path
    runner: ExecutorAgentRunner
    ctx: AttemptContext
    log: EventLog

    @property
    def workcopy(self) -> Path:
        return workcopy_of(self.ctx)

    @property
    def recorded(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads((self.root / "record.json").read_text("utf-8"))
        return data


def rig(tmp_path: Path, script: dict[str, Any], **options: Any) -> Rig:
    worktree = tmp_path / "wt" / "t1-a1"
    (worktree / "src").mkdir(parents=True)
    (worktree / "src" / "a.py").write_text(BEFORE, encoding="utf-8")
    (worktree / ".git").mkdir()
    (worktree / ".git" / "HEAD").write_text("ref: refs/heads/x", encoding="utf-8")
    path = tmp_path / "script.json"
    path.write_text(json.dumps({"record": str(tmp_path / "record.json"), **script}), "utf-8")
    log = EventLog.open(tmp_path / "events.jsonl", "r1", clock=FakeClock())
    log.append_transition(Level.EXECUTION, "r1", None, ExecutionState.PREPARING)
    task = TaskInfo("t1", "t1-s", "reduce the complexity of f", ("src/a.py",), 8, 400)
    ctx = AttemptContext(task, "t1-a1", worktree, None)
    runner = ExecutorAgentRunner(FakeCli(path), Path("claude"), _host_env(), log, **options)
    return Rig(tmp_path, runner, ctx, log)


def _host_env() -> dict[str, str]:
    import os  # noqa: PLC0415

    return dict(os.environ)


async def settle(step: Step) -> dict[str, Any]:
    payload: dict[str, Any] = dict(await step.payload())
    await step.run()
    return payload


async def work(r: Rig) -> AgentResult:
    await settle(r.runner.prepare_workcopy(r.ctx))
    return await r.runner.call(r.ctx, "call_agent_cli:t1-a1", RecordingSink())


def go[T](awaitable: Awaitable[T]) -> T:
    async def wait() -> T:
        return await awaitable

    return asyncio.run(wait())


GOOD = {"writes": {"src/a.py": AFTER}, "saida": DECLARED, "lines": [USAGE]}


# -- the copy --------------------------------------------------------------------------------------


def test_the_agent_gets_a_copy_without_git_with_the_package_and_the_permissions(
    tmp_path: Path,
) -> None:
    r = rig(tmp_path, GOOD)

    go(settle(r.runner.prepare_workcopy(r.ctx)))

    assert (r.workcopy / "src" / "a.py").read_text("utf-8") == BEFORE
    assert not (r.workcopy / ".git").exists()
    assert (r.workcopy / ".forja-agent" / "package.md").is_file()
    assert (r.workcopy / ".forja-agent" / "claude-settings.json").is_file()


def test_the_package_the_agent_gets_carries_the_voice_the_runner_was_given(tmp_path: Path) -> None:
    r = rig(tmp_path, GOOD, voice="Write plainly.")

    go(settle(r.runner.prepare_workcopy(r.ctx)))

    package = (r.workcopy / ".forja-agent" / "package.md").read_text("utf-8")
    assert "## Voice" in package
    assert "Write plainly." in package


def test_a_copy_is_made_again_from_scratch(tmp_path: Path) -> None:
    r = rig(tmp_path, GOOD)
    go(settle(r.runner.prepare_workcopy(r.ctx)))
    (r.workcopy / "src" / "leftover.py").write_text("x = 1\n", encoding="utf-8")

    go(settle(r.runner.prepare_workcopy(r.ctx)))

    assert not (r.workcopy / "src" / "leftover.py").exists()


# -- the call --------------------------------------------------------------------------------------


def test_the_cli_is_called_in_the_copy_with_the_package_on_stdin(tmp_path: Path) -> None:
    r = rig(tmp_path, GOOD)

    go(work(r))

    recorded = r.recorded
    assert Path(recorded["cwd"]).resolve() == r.workcopy.resolve()
    assert "reduce the complexity of f" in recorded["stdin"]
    assert "--tools" in recorded["argv"]
    tools = recorded["argv"][recorded["argv"].index("--tools") + 1].split(",")
    assert "Edit" in tools  # a runner built without permissions may edit files
    assert "PATH" in recorded["env"] and "NO_COLOR" in recorded["env"]


def test_a_good_call_returns_why_and_counts_the_tokens(tmp_path: Path) -> None:
    r = rig(tmp_path, GOOD)

    result = go(work(r))

    assert result == AgentResult(reason="the name says what it does")
    assert read_consumption(tmp_path / "events.jsonl").tokens == 120


def test_the_raw_output_of_the_cli_is_kept_in_a_file_with_a_name_every_system_accepts(
    tmp_path: Path,
) -> None:
    r = rig(tmp_path, GOOD)

    go(work(r))

    log = tmp_path / "wt" / "call_agent_cli-t1-a1.log"
    assert log.is_file() and log.stat().st_size > 0
    assert not (tmp_path / "wt" / "call_agent_cli").exists()  # NTFS: a stream of this file


def test_what_the_cli_does_is_recorded_as_activity(tmp_path: Path) -> None:
    read = json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read",
         "input": {"file_path": "src/a.py"}}]}}
    )  # fmt: skip
    r = rig(tmp_path, {**GOOD, "lines": [read, USAGE]})

    go(work(r))

    text = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert '"file_read"' in text and '"src/a.py"' in text


def test_an_error_the_cli_reports_is_the_result_whatever_else_it_did(tmp_path: Path) -> None:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    r = rig(tmp_path, {"lines": lines, "exit": 1})

    result = go(work(r))

    assert result.error is not None and result.error.kind is ErrorKind.BLOCKING
    assert "OAuth session expired" in result.error.message


def test_a_cli_that_ends_badly_without_saying_why_is_a_transient_failure(tmp_path: Path) -> None:
    r = rig(tmp_path, {"exit": 3, "stderr": "boom"})

    result = go(work(r))

    assert result.error is not None and result.error.kind is ErrorKind.TRANSIENT
    assert "exit code 3" in result.error.message


def test_a_cli_that_goes_quiet_is_ended_and_reported(tmp_path: Path) -> None:
    r = rig(tmp_path, {"sleep": 60}, idle_limit=0.4, poll=0.05)

    started = time.monotonic()
    result = go(work(r))

    assert time.monotonic() - started < 30
    assert result.error is not None and result.error.kind is ErrorKind.TRANSIENT
    assert "printed nothing" in result.error.message


def consumed(tmp_path: Path) -> tuple[int, int]:
    found = read_consumption(tmp_path / "events.jsonl")
    return found.tokens, found.unmeasured


def test_a_call_that_said_its_tokens_is_measured_and_said_once(tmp_path: Path) -> None:
    r = rig(tmp_path, GOOD)

    go(work(r))

    assert consumed(tmp_path) == (120, 0)
    assert (tmp_path / "events.jsonl").read_text(encoding="utf-8").count('"usage"') == 1


def test_a_call_that_ended_well_without_a_word_on_tokens_is_use_nobody_knows(
    tmp_path: Path,
) -> None:
    r = rig(tmp_path, {**GOOD, "lines": []})

    result = go(work(r))

    assert result.error is None
    assert consumed(tmp_path) == (0, 1)


def test_a_call_that_ended_badly_is_use_nobody_knows_too(tmp_path: Path) -> None:
    r = rig(tmp_path, {"exit": 3, "stderr": "boom"})

    go(work(r))

    assert consumed(tmp_path) == (0, 1)


def test_a_call_ended_for_going_quiet_is_use_nobody_knows_too(tmp_path: Path) -> None:
    r = rig(tmp_path, {"sleep": 60}, idle_limit=0.4, poll=0.05)

    go(work(r))

    assert consumed(tmp_path) == (0, 1)


def test_a_usage_report_without_counts_is_kept_as_unknown_not_as_zero() -> None:
    assert _payload(UsageActivity(None, None)) == {"type": "usage", "input": None, "output": None}
    assert _payload(UsageActivity(7, None)) == {"type": "usage", "input": 7, "output": None}
    assert _payload(UsageActivity(0, 0)) == {"type": "usage", "input": 0, "output": 0}
    assert _payload(UsageActivity(7, 3)) == {"type": "usage", "input": 7, "output": 3}


def test_a_cli_that_prints_something_is_never_called_quiet_however_late_the_clock_is(
    tmp_path: Path,
) -> None:
    ticks = iter(range(0, 10**9, 1000))
    r = rig(tmp_path, GOOD, idle_limit=5.0, poll=30.0, clock=lambda: float(next(ticks)))

    result = go(work(r))

    assert result == AgentResult(reason="the name says what it does")


def test_a_cli_is_quiet_only_when_the_silence_is_longer_than_the_limit(tmp_path: Path) -> None:
    # the clock does not move: the silence is 0 seconds, which is not longer than 0
    r = rig(tmp_path, {**GOOD, "sleep": 1}, idle_limit=0.0, poll=0.05, clock=lambda: 100.0)

    assert go(work(r)) == AgentResult(reason="the name says what it does")


def test_a_cli_that_goes_quiet_is_ended_and_nothing_is_left_running(tmp_path: Path) -> None:
    r = rig(tmp_path, {"sleep": 60}, idle_limit=0.4, poll=0.05)

    async def scenario() -> list[asyncio.Task[Any]]:
        await work(r)
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    assert go(scenario()) == []


# -- the way back ----------------------------------------------------------------------------------


def test_the_work_comes_back_when_it_was_declared_and_nothing_else_does(tmp_path: Path) -> None:
    r = rig(tmp_path, GOOD)
    go(work(r))

    go(settle(r.runner.apply_diff(r.ctx)))

    assert (r.ctx.worktree / "src" / "a.py").read_text("utf-8") == AFTER
    assert not (r.ctx.worktree / ".forja-agent").exists()
    assert (r.ctx.worktree / ".git" / "HEAD").is_file()


def test_a_file_the_agent_removed_is_removed_from_the_worktree_too(tmp_path: Path) -> None:
    r = rig(tmp_path, GOOD)
    (r.ctx.worktree / "src" / "sub").mkdir()
    (r.ctx.worktree / "src" / "sub" / "b.py").write_text("x = 1\n", encoding="utf-8")
    go(work(r))
    (r.workcopy / "src" / "sub" / "b.py").unlink()
    (r.workcopy / "src" / "a.py").unlink()
    files = [
        {"path": "src/a.py", "change": "removed"},
        {"path": "src/sub/b.py", "change": "removed"},
    ]
    declared = {"actions": [{"summary": "s", "reason": "r", "files": files}]}
    (r.workcopy / ".forja-agent" / "saida.json").write_text(json.dumps(declared), encoding="utf-8")

    go(settle(r.runner.apply_diff(r.ctx)))

    assert not (r.ctx.worktree / "src" / "a.py").exists()
    assert not (r.ctx.worktree / "src" / "sub" / "b.py").exists()
    assert (r.ctx.worktree / ".git" / "HEAD").is_file()


def test_clearing_a_directory_does_not_go_into_a_link_to_another(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("not ours\n", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need a privilege here")

    _wipe(root, frozenset())

    assert list(root.iterdir()) == []
    assert (outside / "x.txt").is_file()


def test_work_that_was_not_declared_is_rejected_with_both_sides(tmp_path: Path) -> None:
    other = {"actions": [{"summary": "s", "reason": "r",
                          "files": [{"path": "src/b.py", "change": "created"}]}]}  # fmt: skip
    r = rig(tmp_path, {**GOOD, "saida": other})
    go(work(r))

    with pytest.raises(RejectedWork) as caught:
        go(settle(r.runner.apply_diff(r.ctx)))

    message = str(caught.value)
    assert "src/a.py declared nothing, changed modified" in message
    assert "src/b.py declared created, changed nothing" in message


def test_no_declaration_rejects_the_work_and_says_where_it_should_have_been(
    tmp_path: Path,
) -> None:
    script = {k: v for k, v in GOOD.items() if k != "saida"}
    r = rig(tmp_path, script)
    result = go(work(r))

    assert result.reason == ""
    with pytest.raises(RejectedWork, match=r"\.forja-agent/saida\.json was not written"):
        go(settle(r.runner.apply_diff(r.ctx)))


def test_a_declaration_that_breaks_the_contract_is_sent_back_with_its_problems(
    tmp_path: Path,
) -> None:
    r = rig(tmp_path, {**GOOD, "saida": {"actions": [{"summary": "only this"}]}})
    go(work(r))

    with pytest.raises(RejectedWork, match=r"is not valid: .*reason"):
        go(settle(r.runner.apply_diff(r.ctx)))


def test_a_symlink_in_the_work_is_rejected(tmp_path: Path) -> None:
    r = rig(tmp_path, GOOD)
    go(work(r))
    try:
        (r.workcopy / "src" / "link.py").symlink_to(r.workcopy / "src" / "a.py")
    except OSError:
        pytest.skip("symlinks need a privilege here")

    with pytest.raises(RejectedWork, match="symlink"):
        go(settle(r.runner.apply_diff(r.ctx)))


# -- after a crash ---------------------------------------------------------------------------------


def operation(name: str, payload: dict[str, Any]) -> OpenOperation:
    return OpenOperation(name, f"{name}:t1-a1", Level.ATTEMPT, "t1-a1", 1, payload=payload)


def test_a_copy_that_was_made_is_known_to_be_made_even_with_the_package_in_it(
    tmp_path: Path,
) -> None:
    r = rig(tmp_path, GOOD)
    payload = go(settle(r.runner.prepare_workcopy(r.ctx)))

    verdict = go(r.runner.reconcile(operation("prepare_workcopy", payload)))
    shutil_target = r.workcopy / "src" / "a.py"
    shutil_target.write_text("changed\n", encoding="utf-8")
    after_damage = go(r.runner.reconcile(operation("prepare_workcopy", payload)))

    assert verdict.decision is Decision.ALREADY_DONE
    assert after_damage.decision is Decision.REDO


def test_the_work_that_came_back_is_known_to_have_come_back(tmp_path: Path) -> None:
    r = rig(tmp_path, GOOD)
    go(work(r))
    step = r.runner.apply_diff(r.ctx)
    payload = go(step.payload())

    before = go(r.runner.reconcile(operation("apply_diff", payload)))
    go(step.run())
    after = go(r.runner.reconcile(operation("apply_diff", payload)))

    assert (before.decision, after.decision) == (Decision.REDO, Decision.ALREADY_DONE)


# -- the whole run ---------------------------------------------------------------------------------


def test_a_whole_run_with_the_real_runner_delivers_the_declared_work(tmp_path: Path) -> None:
    world = MemoryWorld(tmp_path, {"src/a.py": BEFORE})
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"record": str(tmp_path / "record.json"), **GOOD}), "utf-8")
    log = world.open()
    runner = ExecutorAgentRunner(FakeCli(script), Path("claude"), _host_env(), log)

    outcome, log = world.run(agent=runner, log=log)  # type: ignore[arg-type]

    assert outcome.state is ExecutionState.DELIVERED
    tip = world.memory.tip(world.workspace.run_branch)
    assert tip.tree["src/a.py"] == AFTER
    memory = world.memory
    made = [memory.commits[sha] for sha in memory.ancestors(tip.sha)]
    commit = next(c for c in made if c.subject.startswith("refactor"))
    assert commit.subject == "refactor: reduce the complexity of f"
    assert "the name says what it does" in commit.body
    assert read_consumption(log.path).tokens == 120


# -- what the agent may not see or touch -----------------------------------------------------------


def declare(path: str, change: str = "created") -> dict[str, Any]:
    return {
        "actions": [{"summary": "s", "reason": "r", "files": [{"path": path, "change": change}]}]
    }


def test_work_that_reaches_into_gits_own_directory_is_rejected_even_if_declared(
    tmp_path: Path,
) -> None:
    r = rig(tmp_path, {"writes": {".git/config": "[core]\n"}, "saida": declare(".git/config")})
    go(work(r))

    with pytest.raises(RejectedWork, match=r"\.git/config: the work reaches into git's own"):
        go(settle(r.runner.apply_diff(r.ctx)))
    assert not (r.ctx.worktree / ".git" / "config").exists()


@pytest.mark.parametrize(
    ("path", "reaches"),
    [
        (".git/config", True),
        ("sub/.git/hooks/post-commit", True),
        (".GIT/config", True),
        (".Git", True),
        (".git./config", True),  # Windows drops trailing dots and spaces
        (".git /config", True),
        ("GIT~1/config", True),  # and has short names
        (".github/workflows/ci.yml", False),
        (".gitignore", False),
        ("src/digit.py", False),
        ("src/git_tools.py", False),
    ],
)
def test_git_s_directory_is_recognized_by_every_spelling_the_system_accepts(
    path: str, reaches: bool
) -> None:
    assert touches_git(path) is reaches


def test_a_sensitive_file_is_not_in_the_copy_and_is_back_untouched(tmp_path: Path) -> None:
    r = rig(tmp_path, GOOD)
    (r.ctx.worktree / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (r.ctx.worktree / "config").mkdir()
    (r.ctx.worktree / "config" / ".env.local").write_text("A=1\n", encoding="utf-8")
    (r.ctx.worktree / "secrets").mkdir()
    (r.ctx.worktree / "secrets" / "k.txt").write_text("k\n", encoding="utf-8")
    go(work(r))

    assert not (r.workcopy / ".env").exists()
    assert not (r.workcopy / "config" / ".env.local").exists()
    assert not (r.workcopy / "secrets" / "k.txt").exists()

    step = r.runner.apply_diff(r.ctx)
    payload = go(step.payload())
    go(step.run())

    assert (r.ctx.worktree / ".env").read_text("utf-8") == "TOKEN=secret\n"
    assert (r.ctx.worktree / "config" / ".env.local").read_text("utf-8") == "A=1\n"
    assert (r.ctx.worktree / "secrets" / "k.txt").read_text("utf-8") == "k\n"
    assert (r.ctx.worktree / "src" / "a.py").read_text("utf-8") == AFTER
    verdict = go(r.runner.reconcile(operation("apply_diff", payload)))
    assert verdict.decision is Decision.ALREADY_DONE


def test_a_copy_without_the_sensitive_files_is_still_known_to_be_made(tmp_path: Path) -> None:
    r = rig(tmp_path, GOOD)
    (r.ctx.worktree / ".env").write_text("TOKEN=secret\n", encoding="utf-8")

    payload = go(settle(r.runner.prepare_workcopy(r.ctx)))

    assert go(r.runner.reconcile(operation("prepare_workcopy", payload))).decision is (
        Decision.ALREADY_DONE
    )


def test_work_that_writes_a_sensitive_file_is_rejected_even_if_declared(tmp_path: Path) -> None:
    r = rig(tmp_path, {"writes": {".env": "TOKEN=planted\n"}, "saida": declare(".env")})
    (r.ctx.worktree / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    go(work(r))

    with pytest.raises(RejectedWork, match=r"\.env: the work creates or changes a sensitive"):
        go(settle(r.runner.apply_diff(r.ctx)))
    assert (r.ctx.worktree / ".env").read_text("utf-8") == "TOKEN=secret\n"


def listed(*paths: str) -> Manifest:
    return Manifest({p: ManifestEntry(p, EntryKind.FILE, "0" * 64, False) for p in paths})


@pytest.mark.parametrize("name", [".env ", ".env.", "config/.env.", "secrets./k.txt", ".env.local"])
def test_a_sensitive_name_is_recognized_the_way_windows_reads_it(tmp_path: Path, name: str) -> None:
    runner = rig(tmp_path, GOOD).runner
    created = ManifestDiff((name,), (), ())

    with pytest.raises(RejectedWork, match="sensitive file"):
        runner._refuse_off_limits(listed(name), created)  # noqa: SLF001


def test_a_new_name_that_could_be_a_short_name_is_refused_but_an_old_one_is_not(
    tmp_path: Path,
) -> None:
    runner = rig(tmp_path, GOOD).runner

    with pytest.raises(RejectedWork, match="short-name marker"):
        runner._refuse_off_limits(listed("ENV~1"), ManifestDiff(("ENV~1",), (), ()))  # noqa: SLF001
    runner._refuse_off_limits(listed("report~1.txt"), ManifestDiff((), (), ()))  # noqa: SLF001


def test_a_link_is_not_followed_into_the_copy_and_stays_where_it_was(tmp_path: Path) -> None:
    r = rig(tmp_path, GOOD)
    outside = tmp_path / "outside.txt"
    outside.write_text("not for the agent\n", encoding="utf-8")
    try:
        (r.ctx.worktree / "src" / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks need a privilege here")
    go(work(r))

    assert not (r.workcopy / "src" / "link.txt").exists()
    go(settle(r.runner.apply_diff(r.ctx)))
    assert (r.ctx.worktree / "src" / "link.txt").is_symlink()


def test_an_agent_that_rewrites_its_own_permissions_has_its_work_rejected(
    tmp_path: Path,
) -> None:
    changed = {"writes": {".forja-agent/claude-settings.json": "{}"}}
    r = rig(tmp_path, {**GOOD, **changed})
    go(work(r))
    (r.workcopy / ".forja-agent" / "claude-settings.json").write_text("{}", encoding="utf-8")

    with pytest.raises(RejectedWork, match="changed its own permissions"):
        go(settle(r.runner.apply_diff(r.ctx)))
    assert (r.ctx.worktree / "src" / "a.py").read_text("utf-8") == BEFORE


def test_the_diary_is_written_whole_in_the_agents_directory_when_there_is_one(
    tmp_path: Path,
) -> None:
    r = rig(tmp_path, GOOD)
    diary = "### Attempt `t1-a0`: rejected\nYou said: " + "w" * 4000 + "\n"

    go(settle(r.runner.prepare_workcopy(replace(r.ctx, diary=diary))))

    assert (r.workcopy / ".forja-agent" / "diary.md").read_text("utf-8") == diary
    assert not (rig(tmp_path / "b", GOOD).workcopy / ".forja-agent" / "diary.md").exists()


def test_the_diary_the_branch_keeps_is_redacted_like_the_agents_copy_and_otherwise_whole(
    tmp_path: Path,
) -> None:
    r = rig(tmp_path, GOOD)
    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    diary = f"# Diário de bordo: t1\n\n> key {token}\n" + "w" * 9000 + "\n"

    kept = r.runner.redact_diary(diary)

    assert token not in kept
    assert kept.startswith("# Diário de bordo: t1\n\n> key ")
    assert kept.endswith("w" * 9000 + "\n")
    assert r.runner.redact_diary("") == ""


def test_an_agent_that_changes_the_diary_has_its_work_rejected(tmp_path: Path) -> None:
    r = rig(tmp_path, GOOD)
    ctx = replace(r.ctx, diary="### Attempt `t1-a0`: rejected\n")
    go(settle(r.runner.prepare_workcopy(ctx)))
    go(r.runner.call(ctx, "call_agent_cli:t1-a1", RecordingSink()))
    (r.workcopy / ".forja-agent" / "diary.md").write_text("nothing happened", encoding="utf-8")

    with pytest.raises(RejectedWork, match="changed the diary"):
        go(settle(r.runner.apply_diff(ctx)))


def test_an_agent_that_deletes_the_diary_has_its_work_rejected(tmp_path: Path) -> None:
    r = rig(tmp_path, GOOD)
    ctx = replace(r.ctx, diary="### Attempt `t1-a0`: rejected\n")
    go(settle(r.runner.prepare_workcopy(ctx)))
    go(r.runner.call(ctx, "call_agent_cli:t1-a1", RecordingSink()))
    (r.workcopy / ".forja-agent" / "diary.md").unlink()

    with pytest.raises(RejectedWork, match="changed the diary"):
        go(settle(r.runner.apply_diff(ctx)))


def test_a_diary_left_alone_lets_the_work_through(tmp_path: Path) -> None:
    r = rig(tmp_path, GOOD)
    ctx = replace(r.ctx, diary="### Attempt `t1-a0`: rejected\n")
    go(settle(r.runner.prepare_workcopy(ctx)))
    go(r.runner.call(ctx, "call_agent_cli:t1-a1", RecordingSink()))

    go(settle(r.runner.apply_diff(ctx)))

    assert (r.ctx.worktree / "src" / "a.py").read_text("utf-8") == AFTER


def test_the_adapter_also_denies_the_agent_its_own_package_and_settings() -> None:
    deny = json.loads(
        ClaudeCodeAdapter()
        .permission_setup(Producer.IMPLEMENTER, RolePermissions(True))
        .files["claude-settings.json"]
    )["permissions"]["deny"]

    assert f"Edit(./.forja-agent/{PACKAGE})" in deny
    assert "Write(./.forja-agent/claude-settings.json)" in deny
    assert "Edit(./.forja-agent/instructions.md)" in deny
    assert "Edit(./.forja-agent/diary.md)" in deny and "Write(./.forja-agent/diary.md)" in deny


def test_the_voice_also_goes_to_the_cli_as_a_file_the_system_prompt_is_extended_with(
    tmp_path: Path,
) -> None:
    r = rig(tmp_path, GOOD, voice="Write plainly.")

    go(work(r))

    assert (r.workcopy / ".forja-agent" / "instructions.md").read_text("utf-8") == "Write plainly."
    argv = r.recorded["argv"]
    assert argv[argv.index("--append-system-prompt-file") + 1] == ".forja-agent/instructions.md"
    assert "--system-prompt" not in argv


def test_without_a_voice_the_cli_gets_no_instruction_file(tmp_path: Path) -> None:
    r = rig(tmp_path, GOOD)

    go(work(r))

    assert not (r.workcopy / ".forja-agent" / "instructions.md").exists()
    assert "--append-system-prompt-file" not in r.recorded["argv"]


def test_an_agent_that_rewrites_the_voice_instruction_has_its_work_rejected(
    tmp_path: Path,
) -> None:
    r = rig(tmp_path, GOOD, voice="Write plainly.")
    go(work(r))
    (r.workcopy / ".forja-agent" / "instructions.md").write_text("Write anything.", "utf-8")

    with pytest.raises(RejectedWork, match="changed its own permissions"):
        go(settle(r.runner.apply_diff(r.ctx)))


def kept(*paths: str) -> dict[str, ManifestEntry]:
    return dict(listed(*paths).entries)


@pytest.mark.parametrize(
    "name", ["src/link", "src/link/x.txt", "SRC/Link/deep/x.txt", "src/link./x"]
)
def test_nothing_is_written_at_a_hidden_path_or_below_it(tmp_path: Path, name: str) -> None:
    runner = rig(tmp_path, GOOD).runner

    with pytest.raises(RejectedWork, match="where it may not write"):
        runner._refuse_collisions(listed(name), kept("src/link"))  # noqa: SLF001


def test_a_path_that_only_looks_like_a_hidden_one_is_not_refused(tmp_path: Path) -> None:
    runner = rig(tmp_path, GOOD).runner

    runner._refuse_collisions(listed("src/linked.txt", "src/link2/x"), kept("src/link"))  # noqa: SLF001


def test_work_written_below_a_link_never_goes_through_it(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    through = {"writes": {"src/link/x.txt": "planted\n"}, "saida": declare("src/link/x.txt")}
    r = rig(tmp_path, through)
    try:
        (r.ctx.worktree / "src" / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need a privilege here")
    go(work(r))

    with pytest.raises(RejectedWork, match="where it may not write"):
        go(settle(r.runner.apply_diff(r.ctx)))
    assert list(outside.iterdir()) == []


def test_a_link_on_the_way_stops_the_copy_even_if_everything_before_missed_it(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (tmp_path / "wt" / "link").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "wt" / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need a privilege here")

    with pytest.raises(RuntimeError, match="through a link"):
        _refuse_links_on_the_way(tmp_path / "wt", "link/x.txt")
    _refuse_links_on_the_way(tmp_path / "wt", "other/x.txt")
    with pytest.raises(RuntimeError, match="through a link"):
        _refuse_links_on_the_way(tmp_path / "wt", "link")  # the last part counts too


@pytest.mark.skipif(sys.platform != "win32", reason="junctions exist on Windows")
def test_a_junction_in_the_worktree_is_neither_followed_nor_written_through(
    tmp_path: Path,
) -> None:
    import _winapi  # noqa: PLC0415

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not ours\n", encoding="utf-8")
    through = {"writes": {"src/jn/x.txt": "planted\n"}, "saida": declare("src/jn/x.txt")}
    r = rig(tmp_path, through)
    _winapi.CreateJunction(str(outside), str(r.ctx.worktree / "src" / "jn"))  # type: ignore[attr-defined,unused-ignore]
    go(work(r))

    assert not (r.workcopy / "src" / "jn" / "secret.txt").exists()
    with pytest.raises(RejectedWork, match="where it may not write"):
        go(settle(r.runner.apply_diff(r.ctx)))
    assert sorted(p.name for p in outside.iterdir()) == ["secret.txt"]
    with pytest.raises(RuntimeError, match="through a link"):
        _refuse_links_on_the_way(r.ctx.worktree, "src/jn/x.txt")


# -- the test author ---------------------------------------------------------------------------


def test_what_the_test_author_declares_comes_back_with_the_work(tmp_path: Path) -> None:
    declared = {
        "actions": [
            {
                "summary": "a test for the criterion",
                "reason": "it shows the defect",
                "files": [{"path": "tests/test_a.py", "change": "created"}],
            }
        ],
        "tests": [
            {"criterion_id": 1, "test_id": "tests/test_a.py::test_add", "path": "tests/test_a.py"}
        ],
    }
    script = {
        "writes": {"tests/test_a.py": "def test_add():\n    assert False\n"},
        "saida": declared,
        "lines": [USAGE],
    }
    r = rig(tmp_path, script, role=Producer.TEST_AUTHOR)
    author = replace(r.ctx.task, role=Producer.TEST_AUTHOR, test_root="tests")
    r = replace(r, ctx=replace(r.ctx, task=author))

    go(work(r))
    go(settle(r.runner.apply_diff(r.ctx)))

    assert [(t.criterion_id, t.test_id, t.path) for t in r.runner.declared_tests(r.ctx)] == [
        (1, "tests/test_a.py::test_add", "tests/test_a.py")
    ]
    assert "must FAIL" in (r.workcopy / ".forja-agent" / PACKAGE).read_text(encoding="utf-8")
    assert (r.ctx.worktree / "tests" / "test_a.py").is_file()
