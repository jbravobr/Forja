"""The real parts of a run, assembled from a ``RunConfig``: git, tools, agent, planner, meter.

A new run and a resumed one go through the same door, because everything the run needs is in its
``RunConfig`` (kept in the log when the person confirmed it). Nothing here decides anything about
the work; this only connects the pieces the orchestrator is written against.
"""

import hashlib
import os
import random
import shutil
import sys
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from forja.checks.complexity import ccn_limit
from forja.checks.duplication import MIN_LINES, MIN_TOKENS, limit_of
from forja.checks.shape import language_of
from forja.checks.specs import default_registry
from forja.checks.voice_markers import build_markers
from forja.cli.security_notes import differential_notices, e2e_notices, security_notices
from forja.cli.semgrep_rules import export_rules
from forja.core.adapters import AdapterRegistry
from forja.core.adapters.executor import CommandTemplate, RolePermissions, locate_in_path
from forja.core.artifacts.models import Producer
from forja.core.authorization import ProtectedBranches
from forja.core.confirmation import Confirmation
from forja.core.events import EventLog, LogEventSink
from forja.core.events.models import canonical_json
from forja.core.gates import CheckSettings
from forja.core.gitguard import GuardedGit
from forja.core.idempotency.git import Git
from forja.core.ids import new_id
from forja.core.isolation import IsolationLevel, IsolationRecord, SensitivePaths
from forja.core.isolation.environment import install_environment
from forja.core.locking import (
    GitMutex,
    LockOwner,
    LockPolicy,
    acquire_repository_lock,
    repository_id,
)
from forja.core.locking.repository import HeldLock
from forja.core.paths import forja_home, locks_dir, run_dir
from forja.core.policy.config import EffectivePolicy, load_policy
from forja.core.redaction import Redactor
from forja.core.states import Level
from forja.executors import adapter_for
from forja.executors.probe import run_probe
from forja.languages import (
    CSharpAdapter,
    GoAdapter,
    JavaAdapter,
    PythonAdapter,
    TypeScriptAdapter,
)
from forja.languages.go.adapter import find_golangci_lint
from forja.languages.python.environment import RunEnvironment, VirtualEnvironment
from forja.orchestrator.agent_runner import ExecutorAgentRunner
from forja.orchestrator.budget import Meter
from forja.orchestrator.config import (
    CreateRequest,
    ExecutorRef,
    FixRequest,
    Objective,
    RunConfig,
    load_config,
    redacted_config,
)
from forja.orchestrator.dry_brief import brief as dry_brief
from forja.orchestrator.facts import Fact
from forja.orchestrator.measurer import PipelineMeasurer
from forja.orchestrator.operations import Journal
from forja.orchestrator.phases import confirm_run
from forja.orchestrator.planner import (
    ComplexityPlanner,
    CreationPlanner,
    FixPlanner,
    InvestigationPlanner,
)
from forja.orchestrator.ports import AgentRunner, Planner, RunContext
from forja.orchestrator.properties_brief import brief as properties_brief
from forja.orchestrator.voice_brief import brief as voice_brief
from forja.orchestrator.workspace_git import (
    GitWorkspace,
    Layout,
    detect_identity,
    git_environment,
)
from forja.ui.view import ViewModel

GIT_ENVIRONMENT = (
    "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TEMP", "TMP", "APPDATA", "LOCALAPPDATA",
    "LANG", "LC_ALL", "PATHEXT", "COMSPEC",
)  # fmt: skip
NETWORK_PROBES_WITHOUT_A_PERSON = 60
LOW_DISK_GIB = 2.0  # free space a run needs, at the start and before every attempt


def cli_answers(executor: ExecutorRef, env: Mapping[str, str]) -> Callable[[], Awaitable[bool]]:
    """Whether the network is back, as the run that waits for it asks: the implementer's CLI
    passes its proof of life again. Only the CLI knows the endpoint it needs (a provider, a
    gateway of the company), so reaching any other address would prove nothing."""

    async def answers() -> bool:
        adapter = adapter_for(executor.name, executor.template)
        return not await run_probe(adapter, Path(executor.path), env)

    return answers


def low_disk(*places: Path) -> Callable[[], bool]:
    """Whether any place the run writes to has less free space than the preparation asks for."""

    def low() -> bool:
        return any(shutil.disk_usage(place).free / 2**30 < LOW_DISK_GIB for place in places)

    return low


INSTALL_TIMEOUT = 600.0  # seconds for one venv or pip command
GIT_MUTEX_TIMEOUT = 300.0  # seconds a git write waits for another task's git write
FORJA_VERSION = "0.0.0"
POLICY_VERSION = "0.0.0+default"
IN_USER_ENVIRONMENT = Confirmation(
    IsolationRecord(agent=IsolationLevel.ENVIRONMENT, repo_code=IsolationLevel.ENVIRONMENT)
)


def git_base_environment(environ: Mapping[str, str]) -> dict[str, str]:
    wanted = {name.upper() for name in GIT_ENVIRONMENT}
    return {name: value for name, value in environ.items() if name.upper() in wanted}


def languages(
    repo: Path, python_of: Callable[[], tuple[str, ...]] | None = None
) -> AdapterRegistry:
    """The language adapters of a run. ``python_of`` gives the interpreter each Python project
    uses when it is made, which changes once the run has an environment of its own."""
    registry = AdapterRegistry()
    python = repo_python(repo)
    mypy = (missing_tool("mypy"),)
    lint = find_golangci_lint()
    registry.register_language(
        PythonAdapter(python=python, mypy=mypy),
        lambda: PythonAdapter(python=(python_of or (lambda: python))(), mypy=mypy),
    )
    registry.register_language(GoAdapter(golangci_lint=lint), lambda: GoAdapter(golangci_lint=lint))
    registry.register_language(TypeScriptAdapter(), TypeScriptAdapter)
    registry.register_language(CSharpAdapter(), CSharpAdapter)
    registry.register_language(JavaAdapter(), JavaAdapter)
    return registry


def _test_root(configured: str | None, registry: AdapterRegistry, repo: Path) -> str:
    """Where the test author writes: the policy's, else Python's ``tests`` for a repository
    that is one Python project. The other languages keep tests beside the code, so no directory
    holds them."""
    if configured is not None:
        return configured
    found = [adapter.language for adapter, _ in registry.detect_all(repo)]
    return PythonAdapter.test_root if found == [PythonAdapter.language] else ""


def repo_python(repo: Path) -> tuple[str, ...]:
    """The interpreter of the repository itself, which has what its tests import."""
    for relative in (".venv/Scripts/python.exe", ".venv/bin/python", "venv/Scripts/python.exe",
                     "venv/bin/python"):  # fmt: skip
        if (repo / relative).is_file():
            return (str(repo / relative),)
    found = locate_in_path("python") or locate_in_path("python3")
    return (str(found) if found else sys.executable,)


class PolicyChanged(Exception):
    """The repository's ``forja.toml`` is not the one the run was confirmed with."""


def policy_digest(policy: EffectivePolicy) -> str:
    """A short name for exactly this policy, kept in the confirmation of the run."""
    return "sha256:" + hashlib.sha256(canonical_json(policy.to_dict())).hexdigest()[:16]


def e2e_commands(policy: EffectivePolicy) -> dict[str, tuple[str, ...]]:
    """The end-to-end command each language's ``[adapters.<language>.commands]`` declares."""
    return {
        language: commands["e2e"]
        for language, commands in policy.adapter_commands.items()
        if "e2e" in commands
    }


def repo_policy(repo: Path) -> EffectivePolicy:
    """The Forja's defaults with what the repository's own ``forja.toml`` says, if it has one."""
    return load_policy(default_registry(), repo=repo / "forja.toml")


def standard_settings() -> dict[str, CheckSettings]:
    """The mode and the policy for inconclusive of every check, from the defaults of the spec."""
    return {spec.id: spec.default_settings for spec in default_registry()}


def in_scope(path: str, scope: tuple[str, ...]) -> bool:
    """Whether a file is under one of the paths the person asked for (all, when none)."""
    if not scope:
        return True
    return any(
        path == item.rstrip("/") or path.startswith(item.rstrip("/") + "/") for item in scope
    )


def missing_tool(name: str) -> str:
    """Where a tool is, or an absolute path that is not there: a bare name could be run from the
    current directory, which belongs to the repository and not to the person."""
    found = locate_in_path(name)
    return str(found) if found else str(Path(sys.prefix) / f"{name}-is-not-installed")


def git_executable() -> str:
    found = locate_in_path("git")
    if found is None:
        raise FileNotFoundError("git is not installed or not on the PATH; run `forja doctor`")
    return str(found)


@dataclass
class Session:
    run_id: str
    log: EventLog
    context: RunContext
    view: ViewModel
    lock: HeldLock
    directory: Path
    resumed: bool

    def close(self) -> None:
        self.lock.release()
        self.log.close()


def executor_ref(
    name: str,
    path: Path,
    version: str,
    model: str = "",
    template: CommandTemplate | None = None,
) -> ExecutorRef:
    return ExecutorRef(
        name=name,
        path=str(path),
        version=version,
        model=model,
        enforces_permissions=adapter_for(name, template).enforces_permissions,
        template=template,
    )


def new_config(
    *,
    repo: Path,
    base_commit: str,
    executor_name: str,
    executor_path: Path,
    executor_version: str,
    tokens: int,
    executor_model: str = "",
    scope: tuple[str, ...] = (),
    agent_runs_tests: bool = True,
    tasks: int = 5,
    parallel_tasks: int = 1,
    policy_version: str = POLICY_VERSION,
    fix: FixRequest | None = None,
    test_author: ExecutorRef | None = None,
    semgrep_config: str = "",
    facts: tuple[Fact, ...] = (),
    question: str = "",
    create: CreateRequest | None = None,
    executor_template: CommandTemplate | None = None,
) -> RunConfig:
    from forja.orchestrator.config import Budget  # noqa: PLC0415

    return RunConfig(
        repo=str(repo),
        base_commit=base_commit,
        objective=(
            Objective.INVESTIGATE
            if question
            else Objective.CREATE
            if create is not None
            else Objective.REFACTOR
            if fix is None
            else Objective.FIX
        ),
        fix=fix,
        create=create,
        question=question,
        implementer=executor_ref(
            executor_name, executor_path, executor_version, executor_model, executor_template
        ),
        test_author=test_author,
        budget=Budget(tokens=tokens, tasks=tasks),
        parallel_tasks=parallel_tasks,
        forja_version=FORJA_VERSION,
        policy_version=policy_version,
        scope=scope,
        agent_runs_tests=agent_runs_tests,
        semgrep_config=semgrep_config,
        facts=facts,
    )


OBJECTIVE_NAMES = {
    Objective.REFACTOR: "refatorar",
    Objective.FIX: "corrigir",
    Objective.INVESTIGATE: "investigar",
    Objective.CREATE: "criar",
}


async def open_session(
    config: RunConfig | None,
    *,
    run_id: str | None = None,
    environ: Mapping[str, str] | None = None,
    interactive: bool = True,
    isolation: Confirmation = IN_USER_ENVIRONMENT,
    home: Path | None = None,
) -> Session:
    """Take the repository's lock and build everything a run needs.

    With a ``run_id`` of an existing run the config comes from its log (a resume); without one
    a new run is created and ``config`` is what the person confirmed.
    """
    env = os.environ if environ is None else environ
    resumed = run_id is not None
    run_id = run_id or new_id()
    base = home or forja_home(environ=env)
    directory = base / "runs" / run_id if home else run_dir(run_id, environ=env)
    directory.mkdir(parents=True, exist_ok=True)
    log = EventLog.open(directory / "events.jsonl", run_id)
    if resumed:
        config = load_config(log.state)
        recorded = config.policy_version
        if recorded.startswith("sha256:") and recorded != policy_digest(
            repo_policy(Path(config.repo))
        ):
            log.close()
            raise PolicyChanged(
                "O forja.toml mudou desde que esta execução foi confirmada. Restaure o arquivo "
                "como estava ou descarte a execução; a Forja não retoma com outra política."
            )
    if config is None:
        raise ValueError("a new run needs the configuration the person confirmed")
    repo = Path(config.repo)
    plain = Git(
        repo, executable=git_executable(), env=git_base_environment(env), timeout=60,
        log_dir=directory / "logs" / "git",
    )  # fmt: skip
    locks = (
        (base / "locks" / await repository_id(plain))
        if home
        else locks_dir(await repository_id(plain), environ=env)
    )
    lock = await acquire_repository_lock(locks, LockOwner.current(run_id), LockPolicy.refuse())
    try:
        if not resumed:
            config = redacted_config(config, Redactor(repo_policy(repo).redaction))
            confirm_run(log, config, isolation)
        context = await _context(
            config, log, directory, base, env, plain, run_id, interactive, locks
        )
    except BaseException:
        lock.release()
        log.close()
        raise
    view = ViewModel(
        objective=OBJECTIVE_NAMES[config.objective],
        repo=repo.name,
        branch=f"forja-run/{run_id}",
        isolation="ambiente do usuário",
        token_limit=config.budget.tokens,
        attempts_max=config.budget.attempts_per_task,
    )
    view.replay(log.path)
    log.subscribe(view.apply)
    return Session(run_id, log, context, view, lock, directory, resumed)


async def _context(
    config: RunConfig,
    log: EventLog,
    directory: Path,
    base: Path,
    env: Mapping[str, str],
    plain: Git,
    run_id: str,
    interactive: bool,
    locks: Path,
) -> RunContext:
    repo = Path(config.repo)
    policy = repo_policy(repo)
    identity = await detect_identity(plain, config.implementer.name)
    branch = (await plain.lines("rev-parse", "--abbrev-ref", "HEAD"))[0]
    git = GuardedGit(
        repo,
        executable=git_executable(),
        env=git_environment(git_base_environment(env), identity),
        timeout=120,
        log_dir=directory / "logs" / "guard",
        protected=ProtectedBranches(
            frozenset({"main", "master", branch, *policy.protected_branches})
        ),
        # every worktree shares one `.git`: tasks that run together must not write to it at once
        mutex=GitMutex(locks),
        mutex_timeout=GIT_MUTEX_TIMEOUT,
    )
    workspace = GitWorkspace(git, run_id=run_id, layout=Layout.for_run(base, run_id), repo=repo)

    def sinks(level: Level, target: str, operation: str) -> LogEventSink:
        return LogEventSink(log, level, target, operation)

    run_python = RunEnvironment(
        VirtualEnvironment(
            directory / "venv",
            base_python=repo_python(repo)[0],
            env=install_environment(env),
            timeout=INSTALL_TIMEOUT,
            log_dir=directory / "logs" / "venv",
        ),
        repo_python(repo),
    )
    registry = languages(repo, lambda: run_python.python)
    rules = await export_rules(
        plain, config.base_commit, config.semgrep_config, directory / "semgrep-rules"
    )
    measurer = PipelineMeasurer(
        adapters=registry,
        git=git,
        log=log,
        workdir=directory / "measure",
        host_env=env,
        settings=policy.settings(),
        values=policy.values(),
        lizard=(sys.executable, "-m", "lizard"),
        gitleaks=(missing_tool("gitleaks"),),
        semgrep=(missing_tool("semgrep"),),
        semgrep_config=rules,
        osv_scanner=(missing_tool("osv-scanner"),),
        jscpd=(missing_tool("jscpd"),),
        forbidden_licenses=policy.forbidden_licenses,
        layers=policy.architecture,
        e2e_commands=e2e_commands(policy),
        markers=build_markers(policy.voice_extra, policy.voice_off),
        fact_ids=frozenset(fact.id for fact in config.facts),
        differential_cases=policy.differential if config.objective is Objective.REFACTOR else (),
        installer=run_python.install,
    )

    async def functions(worktree: Path) -> Mapping[tuple[str, str], int]:
        found = await measurer.functions(worktree, sinks)
        return {
            key: ccn
            for key, ccn in found.items()
            if not config.scope or in_scope(key[0], config.scope)
        }

    async def churn(worktree: Path) -> Mapping[str, int]:
        """How many commits changed each file, in the history the run starts from."""
        names = await git.at(worktree).lines("log", "--format=", "--name-only", config.base_commit)
        return Counter(name for name in names if name)

    async def weak_nets(
        worktree: Path, found: Sequence[tuple[str, str]]
    ) -> frozenset[tuple[str, str]]:
        return await measurer.weak_nets(worktree, sinks, found)

    commands: tuple[str, ...] = ()
    if locate_in_path("forja"):
        commands = ("forja agente lint", "forja agente medir")
        if config.agent_runs_tests:
            commands = (*commands, "forja agente testar")
    sensitive = SensitivePaths.with_defaults(policy.sensitive_paths)
    voice = voice_brief(policy.voice_off, policy.voice_extra)
    duplication = policy.values().get("duplication", {})
    dry = dry_brief(
        limit_of(duplication, "min_tokens", MIN_TOKENS),
        limit_of(duplication, "min_lines", MIN_LINES),
    )
    properties = properties_brief(adapter.language for adapter, _ in registry.detect_all(repo))

    def runner(executor: ExecutorRef, role: Producer) -> ExecutorAgentRunner:
        return ExecutorAgentRunner(
            adapter_for(executor.name, executor.template),
            Path(executor.path),
            {**env, "FORJA_AGENT_PYTHON": repo_python(repo)[0]},
            log,
            sensitive=sensitive,
            redactor=Redactor(policy.redaction, sensitive),
            role=role,
            permissions=RolePermissions(
                can_edit_files=True,
                allowed_commands=()
                if role in (Producer.DOCUMENTER, Producer.SPECIFIER, Producer.ARCHAEOLOGIST)
                else commands,
            ),
            model=executor.model,
            voice=voice,
            dry=dry,
            properties=properties,
        )

    agents: dict[Producer, AgentRunner] = {
        Producer.DOCUMENTER: runner(config.implementer, Producer.DOCUMENTER)
    }
    complexity = policy.values().get("complexity", {})
    test_root = _test_root(policy.test_root, registry, repo)
    planner: Planner = ComplexityPlanner(
        functions,
        lambda file: ccn_limit(complexity, language_of(file)),
        churn=churn,
        weak_nets=weak_nets,
        test_root=test_root,
    )
    if config.objective is Objective.REFACTOR:  # a task whose tests are weak gets them first
        agents[Producer.TEST_AUTHOR] = runner(
            config.test_author or config.implementer, Producer.TEST_AUTHOR
        )
    if config.fix is not None:
        agents[Producer.TEST_AUTHOR] = runner(
            config.test_author or config.implementer, Producer.TEST_AUTHOR
        )
        planner = FixPlanner(config.fix, config.scope, test_root)
        if config.fix.criteria is None:
            agents[Producer.SPECIFIER] = runner(config.implementer, Producer.SPECIFIER)
    if config.create is not None:
        agents[Producer.TEST_AUTHOR] = runner(
            config.test_author or config.implementer, Producer.TEST_AUTHOR
        )
        agents[Producer.SPECIFIER] = runner(config.implementer, Producer.SPECIFIER)
        planner = CreationPlanner(config.create, config.scope, test_root)
    if config.objective is Objective.INVESTIGATE:
        agents[Producer.ARCHAEOLOGIST] = runner(config.implementer, Producer.ARCHAEOLOGIST)
        planner = InvestigationPlanner(config.question, config.scope)
    return RunContext(
        journal=Journal(log),
        config=config,
        workspace=workspace,
        measurer=measurer,
        agent=runner(config.implementer, Producer.IMPLEMENTER),
        agents=agents,
        planner=planner,
        meter=Meter(config.budget.tokens, log.path, enforce=not interactive),
        run_dir=directory,
        test_files=measurer.test_files,
        test_root=test_root,
        max_network_probes=None if interactive else NETWORK_PROBES_WITHOUT_A_PERSON,
        network_is_up=cli_answers(config.implementer, env),
        disk_is_low=low_disk(repo, base),
        jitter=random.random,
        notices=(
            *security_notices(
                rules=rules is not None,
                licenses=bool(policy.forbidden_licenses),
                layers=bool(policy.architecture),
            ),
            *e2e_notices(declared=bool(e2e_commands(policy))),
            *differential_notices(
                refactoring=config.objective is Objective.REFACTOR,
                declared=bool(policy.differential),
            ),
        ),
    )
