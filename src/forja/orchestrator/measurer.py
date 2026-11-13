"""The measurer of a real run: the gate pipeline over an adapter's tools.

It formats the base, measures it (build, tests twice to find the unstable ones, lint and
complexity totals), and judges each attempt and each integration with the checks in gate order.
"""

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from pydantic import JsonValue

from forja.checks.adr_trigger import ADR_DIR, AdrCheck, Trigger, new_modules, report
from forja.checks.architecture import Unavailable, build
from forja.checks.architecture.compare import Snapshot
from forja.checks.base import Undecided, measure, overrides_from
from forja.checks.build_and_tests import FAILED, BuildCheck, TestsCheck, outcomes_of
from forja.checks.change_record import (
    CHANGES_DIR,
    RecordFacts,
    SymbolChange,
    diary_path,
    file_changes,
    record_path,
    render_changed,
    render_record,
    symbol_changes,
)
from forja.checks.change_record_gate import ChangeRecordCheck
from forja.checks.changes import ChangeKind, Changes, read_before, read_changes
from forja.checks.citations import CitationCheck
from forja.checks.comments import CommentsCheck
from forja.checks.complexity import (
    ComplexityCheck,
    lizard_plan,
    parse_functions,
    parse_spans,
)
from forja.checks.coverage import CoverageShare
from forja.checks.crap import CrapCheck
from forja.checks.dependency_audit import DependencyAuditCheck, is_artifact
from forja.checks.dependency_cycles import DependencyCyclesCheck
from forja.checks.dependency_install import (
    DependencyInstallCheck,
    Installer,
    install_findings,
    requirements_of,
)
from forja.checks.differential import DifferentialCheck
from forja.checks.differential_base import DifferentialBase
from forja.checks.differential_base import load as load_differential_base
from forja.checks.differential_base import measure_base as measure_differential_base
from forja.checks.differential_inputs import MissingInput
from forja.checks.doc_drift_gate import Base, DocDriftCheck, read_repository
from forja.checks.duplication import DuplicationCheck
from forja.checks.e2e import E2ECheck, E2ERun, run_e2e
from forja.checks.each import EachLanguage, Part
from forja.checks.freeze import AppendOnlyCheck, FreezeCheck
from forja.checks.layer_rules import LayerRulesCheck
from forja.checks.layout import (
    FormatCheck,
    LintCheck,
    TypesCheck,
    by_adapter,
    own_files,
    violations,
)
from forja.checks.module_depth import ModuleDepthCheck
from forja.checks.mutation import MutationCheck
from forja.checks.new_dependency import (
    NewDependencyCheck,
    added_dependencies,
    dependency_edit_of,
    is_manifest,
    manifests_of,
)
from forja.checks.package_metrics import PackageMetricsCheck
from forja.checks.pass_through import PassThroughCheck
from forja.checks.personal_data import PersonalDataCheck
from forja.checks.properties import PropertiesCheck
from forja.checks.readme import Signature, evaluate, signature
from forja.checks.readme_gate import ReadmeCheck, exists_inside, read_readme, readme_path
from forja.checks.red_green import RedGreenCheck
from forja.checks.registries import HttpGet, urllib_get
from forja.checks.sast import SastCheck
from forja.checks.scope import TEST_AUTHOR_CLASSES, ScopeCheck, silencers
from forja.checks.secrets import SecretsCheck
from forja.checks.structure import SUFFIXES as SHAPE_SUFFIXES
from forja.checks.structure import dialect_of
from forja.checks.tooling import Tools
from forja.checks.traceability import TraceabilityCheck
from forja.checks.voice import VoiceCheck
from forja.checks.voice_markers import MARKERS, Marker
from forja.core.adapters import (
    AdapterRegistry,
    CommandPlan,
    FileClass,
    NotSupported,
    Operation,
    OperationRequest,
)
from forja.core.adapters.common import extract_version
from forja.core.adapters.projects import owner_of
from forja.core.adapters.scoped import ScopedAdapter
from forja.core.artifacts.llm_output import Claim
from forja.core.artifacts.models import Executor, Producer
from forja.core.artifacts.schemas import RecordedFile, ToolVersion
from forja.core.atomic import atomic_write_bytes
from forja.core.events import EventLog, LogEventSink, OpenOperation
from forja.core.gates import (
    Check,
    CheckSettings,
    GateInput,
    GatePipeline,
    Inconclusive,
    PipelineOutcome,
)
from forja.core.idempotency import Decision, Reconciliation
from forja.core.policy import ArchitectureLayer, DifferentialCase
from forja.core.states import Level
from forja.orchestrator.ports import (
    NO_COMMIT,
    AttemptContext,
    Baseline,
    Expectations,
    RecordFile,
    SinkFactory,
)
from forja.orchestrator.summary import DIFFERENTIAL, differential_note

BASE_FILE = "base.json"
BASE_METRICS_FILE = "base-metrics.json"
DIFFERENTIAL_DIRECTORY = "differential"
STATIC_METRICS = (
    "lint_violations",
    "max_ccn",
    "functions",
)  # what a task is measured on afterwards
HEAD = "HEAD"
PLANNING = "planning"  # what a measurement made for the plan is filed under
NO_CHANGES = Changes((), 0, ())  # the planner measures code as it is: no attempt wrote it
UNSTABLE = "unstable"
PASSING = "passing"
E2E_UNSTABLE = "e2e_unstable"
E2E_PASSING = "e2e_passing"


class GitAt(Protocol):
    """A git that can be pointed at another worktree (``GuardedGit`` is one)."""

    async def lines(self, *args: str) -> list[str]: ...

    def at(self, repo: Path) -> "GitAt": ...


@dataclass(frozen=True)
class BaseE2E:
    """What two end-to-end runs of the base showed; ``ran`` is false when none was declared."""

    unstable: tuple[str, ...] = ()
    passing: tuple[str, ...] = ()
    failing: tuple[str, ...] = ()
    destinations: tuple[str, ...] = ()
    ran: bool = False


class PipelineMeasurer:
    def __init__(
        self,
        *,
        adapters: AdapterRegistry,
        git: GitAt,
        log: EventLog,
        workdir: Path,
        host_env: Mapping[str, str],
        settings: Mapping[str, CheckSettings],
        values: Mapping[str, Mapping[str, JsonValue]] | None = None,
        lizard: tuple[str, ...],
        gitleaks: tuple[str, ...] = ("gitleaks",),
        semgrep: tuple[str, ...] = ("semgrep",),
        semgrep_config: Path | None = None,
        osv_scanner: tuple[str, ...] = ("osv-scanner",),
        jscpd: tuple[str, ...] = ("jscpd",),
        forbidden_licenses: Iterable[str] = (),
        layers: Iterable[ArchitectureLayer] = (),
        registry_get: HttpGet = urllib_get,
        timeouts: Mapping[str, float] | None = None,
        e2e_commands: Mapping[str, tuple[str, ...]] | None = None,
        markers: Sequence[Marker] = MARKERS,
        fact_ids: frozenset[str] = frozenset(),
        differential_cases: Iterable[DifferentialCase] = (),
        installer: Installer | None = None,
    ) -> None:
        self._installer = installer
        self._adapters = adapters
        self._git = git
        self._log = log
        self._workdir = workdir
        self._env = host_env
        self._settings = settings
        self._values = values or {}
        self._lizard = lizard
        self._gitleaks = gitleaks
        self._semgrep = semgrep
        self._semgrep_config = semgrep_config
        self._osv_scanner = osv_scanner
        self._jscpd = jscpd
        self._forbidden = frozenset(name.lower() for name in forbidden_licenses)
        self._layers = tuple(layers)
        self._registry_get = registry_get
        self._timeouts = timeouts or {}
        self._e2e = e2e_commands or {}
        self._markers = markers
        self._fact_ids = fact_ids
        self._differential_cases = tuple(differential_cases)

    # -- helpers -------------------------------------------------------------------------------

    @property
    def _run_id(self) -> str:
        return self._log.state.run_id

    def _tools(self, sinks: SinkFactory, level: Level, target: str) -> Tools:
        return Tools(
            sinks=sinks,
            log=self._log,
            level=level,
            target=target,
            logs=self._workdir / "tools" / target,
            host_env=self._env,
            timeouts=self._timeouts,
        )

    def _classify(self, path: str) -> FileClass:
        return self._adapters.classify(path)

    def _active(self, worktree: Path, changed: tuple[str, ...] = ()) -> tuple[ScopedAdapter, ...]:
        """The projects the repository has, and a project at the root for any language that claims
        a file that changed and has none holding it: code is never left out of a check because
        its manifest is not where the file is. A repository none of them claims is run as the
        first language, at the root."""
        found = self._adapters.projects(worktree)
        for path in changed:
            language = self._adapters.for_file(path)
            if language is not None and owner_of(found, path) is None:
                found.append(self._adapters.project(language.language))
        return tuple(found) or (self._adapters.project(next(self._adapters.languages()).language),)

    def _run_sink(self, level: Level, target: str, operation: str) -> LogEventSink:
        return LogEventSink(self._log, level, target, operation)

    @property
    def _base_file(self) -> Path:
        return self._workdir / BASE_FILE

    def _read_base(self) -> tuple[frozenset[str], frozenset[str]]:
        """The unstable tests and the ones that passed, as the first measurement saw them."""
        try:
            data = json.loads(self._base_file.read_text(encoding="utf-8"))
            return frozenset(map(str, data[UNSTABLE])), frozenset(map(str, data[PASSING]))
        except (OSError, ValueError, KeyError, TypeError):
            return frozenset(), frozenset()

    def _read_e2e_base(self) -> tuple[frozenset[str], frozenset[str]]:
        """The end-to-end tests that were unstable and that passed, as the base showed them."""
        try:
            data = json.loads(self._base_file.read_text(encoding="utf-8"))
            return frozenset(map(str, data[E2E_UNSTABLE])), frozenset(map(str, data[E2E_PASSING]))
        except (OSError, ValueError, KeyError, TypeError):
            return frozenset(), frozenset()

    # -- normalization -------------------------------------------------------------------------

    async def format_worktree(self, worktree: Path) -> None:
        """Safe lint fixes, then the formatter, over the whole tree: the ``style:`` commit."""
        tools = self._tools(self._run_sink, Level.EXECUTION, self._run_id)
        for adapter in self._active(worktree):
            for operation, name in (
                (Operation.LINT_FIX, "normalize_lint"),
                (Operation.FORMAT, "normalize_format"),
            ):
                plan = adapter.plan(operation, OperationRequest(root=worktree))
                if isinstance(plan, CommandPlan):
                    await tools.run(name, plan)

    async def _normalized(
        self, tools: Tools, worktree: Path, git: GitAt, changes: Changes
    ) -> Changes:
        """Safe lint fixes, then the formatter, over the files the attempt changed, before any
        gate reads them: the tool does what it can, and the agent is judged on the rest."""
        files = changes.current(own_files(self._adapters))
        if not files:
            return changes
        for adapter, own in by_adapter(self._active(worktree, files), files):
            for operation, name in (
                (Operation.LINT_FIX, "normalize_lint"),
                (Operation.FORMAT, "normalize_format"),
            ):
                plan = adapter.plan(operation, OperationRequest(root=worktree, files=own))
                if isinstance(plan, CommandPlan):
                    await tools.run(name, plan)
        return await read_changes(git)

    # -- the base ------------------------------------------------------------------------------

    async def baseline(self, worktree: Path, sinks: SinkFactory) -> Baseline:
        tools = self._tools(sinks, Level.EXECUTION, self._run_id)
        problems: list[str] = []
        active = self._active(worktree)
        try:
            problems += await self._build_problems(tools, worktree, active)
            first = await self._outcomes(tools, worktree, active)
            second = await self._outcomes(tools, worktree, active)
            e2e = await self._base_e2e(tools, worktree, active)
            problems += await self._base_differential(tools, worktree)
        except Undecided as stop:
            return Baseline(False, (_describe(stop),))
        failing = {t for t, o in first.items() if o in FAILED} & {
            t for t, o in second.items() if o in FAILED
        }
        unstable = sorted(t for t in first.keys() | second.keys() if first.get(t) != second.get(t))
        problems += [f"tests: {t} fails on the base" for t in sorted(failing)]
        problems += [f"e2e: {t} fails on the base" for t in sorted(e2e.failing)]
        problems += [f"e2e: the base tried to reach {d}" for d in e2e.destinations]
        metrics = await self._metrics(tools, worktree, active, len(first), len(unstable))
        if not self._base_file.exists():
            stable = sorted(
                t for t, o in first.items() if o == "passed" and second.get(t) == "passed"
            )
            self._save_base(unstable, stable, e2e)
            atomic_write_bytes(
                self._workdir / BASE_METRICS_FILE, json.dumps(metrics, sort_keys=True).encode()
            )
        return Baseline(not problems, tuple(problems), metrics)

    def _overrides(
        self, adapter: ScopedAdapter, check_id: str
    ) -> dict[str, dict[str, tuple[str, ...]]]:
        """The repository's own command for this check, if its ``forja.toml`` names one."""
        return overrides_from(adapter, self._values.get(check_id, {}))

    async def _base_e2e(
        self, tools: Tools, worktree: Path, active: tuple[ScopedAdapter, ...]
    ) -> BaseE2E:
        """The end-to-end tests run twice on the base, like the tests, to find the unstable ones."""
        if not self._e2e:
            return BaseE2E()
        runs = [await run_e2e(tools, active, worktree, self._e2e, self._env) for _ in range(2)]
        first, second = (run or E2ERun({}, ()) for run in runs)
        return BaseE2E(
            unstable=tuple(
                sorted(
                    t
                    for t in first.outcomes.keys() | second.outcomes.keys()
                    if first.outcomes.get(t) != second.outcomes.get(t)
                )
            ),
            passing=tuple(
                sorted(
                    t
                    for t, o in first.outcomes.items()
                    if o == "passed" and second.outcomes.get(t) == "passed"
                )
            ),
            failing=tuple(
                sorted(
                    t
                    for t, o in first.outcomes.items()
                    if o in FAILED and second.outcomes.get(t) in FAILED
                )
            ),
            destinations=tuple(dict.fromkeys((*first.destinations, *second.destinations))),
            ran=True,
        )

    @property
    def differential_directory(self) -> Path:
        return self._workdir / DIFFERENTIAL_DIRECTORY

    async def _base_differential(self, tools: Tools, worktree: Path) -> list[str]:
        """The differential cases run twice on the base, once per execution: a base already
        measured (a resumed execution) is read back, so every attempt meets the same one."""
        cases = self._differential_cases
        if not cases:
            return []
        base = load_differential_base(self.differential_directory, cases)
        if base is None:
            try:
                base = await measure_differential_base(
                    tools, cases, worktree, self.differential_directory, self._env
                )
            except MissingInput as missing:
                return [f"differential: {missing}"]
        # said again on a resumed run, so the summary has it even if the first note was lost
        self._log.append_progress(
            Level.EXECUTION, self._run_id, DIFFERENTIAL, payload=differential_note(base)
        )
        return []

    def _differential_base(self) -> DifferentialBase:
        """The base the attempts are judged against; with none, each case says it has no measure."""
        cases = self._differential_cases
        found = load_differential_base(self.differential_directory, cases) if cases else None
        return DifferentialBase() if found is None else found

    def _save_base(
        self, unstable: list[str], passing: list[str], e2e: BaseE2E | None = None
    ) -> None:
        self._workdir.mkdir(parents=True, exist_ok=True)
        saved: dict[str, list[str]] = {UNSTABLE: unstable, PASSING: passing}
        if e2e is not None and e2e.ran:
            saved |= {E2E_UNSTABLE: list(e2e.unstable), E2E_PASSING: list(e2e.passing)}
        document = json.dumps(saved, sort_keys=True)
        atomic_write_bytes(self._base_file, document.encode("utf-8"))

    async def _build_problems(
        self, tools: Tools, worktree: Path, active: tuple[ScopedAdapter, ...]
    ) -> list[str]:
        problems = []
        for adapter in active:
            parsed = await measure(
                tools,
                adapter,
                Operation.BUILD,
                "build",
                OperationRequest(root=worktree),
                self._overrides(adapter, "build"),
            )
            problems += [f"build: {d.target.id} does not compile" for d in parsed.drafts]
        return problems

    async def _outcomes(
        self, tools: Tools, worktree: Path, active: tuple[ScopedAdapter, ...]
    ) -> dict[str, str]:
        return await outcomes_of(tools, active, worktree, self._values.get("tests", {}))

    async def _metrics(
        self,
        tools: Tools,
        worktree: Path,
        active: tuple[ScopedAdapter, ...],
        tests: int,
        unstable: int,
    ) -> dict[str, float]:
        counts = {"tests": float(tests), "unstable_tests": float(unstable)}
        return counts | await self._static_metrics(tools, worktree, active)

    def _read_base_metrics(self) -> dict[str, float]:
        """The numbers the base showed, as the first measurement saw them; none if unknown."""
        try:
            data = json.loads((self._workdir / BASE_METRICS_FILE).read_text(encoding="utf-8"))
            return {str(k): float(v) for k, v in data.items()}
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    async def stage_metrics(
        self, ctx: AttemptContext, sinks: SinkFactory
    ) -> tuple[tuple[str, float | None, float | None], ...]:
        """Each number as the base of the run had it and as the attempt's worktree has it, for
        the trailers and the note of the commit. Only the ones that need no test run are taken
        again; ``None`` stands for a number one of the two sides does not have."""
        before = self._read_base_metrics()
        tools = self._tools(sinks, Level.ATTEMPT, ctx.attempt_id)
        after = await self._static_metrics(tools, ctx.worktree, self._active(ctx.worktree))
        return tuple(
            (name, before.get(name), after.get(name)) for name in sorted(before.keys() | after)
        )

    async def change_record(
        self,
        ctx: AttemptContext,
        sinks: SinkFactory,
        claims: Sequence[Claim],
        gates_passed: Sequence[str],
        executors: Sequence[Executor],
    ) -> RecordFile:
        """The record of the task whose work this worktree holds, read against the commit the task
        branch started from. Documentation changes are the documenter's own and are left out."""
        git = self._git.at(ctx.worktree)
        files, found, unreadable = await self._task_changes(ctx)
        tools = self._tools(sinks, Level.ATTEMPT, ctx.attempt_id)
        facts = RecordFacts(
            task_id=ctx.task.task_id,
            intent=ctx.task.intent,
            files=files,
            symbols=found,
            metrics_before=self._read_base_metrics(),
            metrics_after=await self._static_metrics(
                tools, ctx.worktree, self._active(ctx.worktree)
            ),
            gates_passed=tuple(gates_passed),
            toolchains=await self._toolchains(tools, ctx.worktree),
            executors=tuple(executors),
            revert_commit=(await git.lines("rev-parse", "HEAD"))[0],
            unreadable=unreadable,
        )
        return RecordFile(record_path(ctx.task.task_id), render_record(facts, claims))

    async def readme_report(self, ctx: AttemptContext) -> str:
        """What is wrong with the README of the worktree, one line each; empty when nothing is."""
        _, text = read_readme(ctx.worktree)
        build, test = self._readme_commands(ctx.worktree)

        def exists(relative: str) -> bool:
            return exists_inside(ctx.worktree, relative)

        return "\n".join(f"- {p.message}" for p in evaluate(text, exists, build, test))

    async def adr_report(self, ctx: AttemptContext) -> str:
        """The decision record the task owes, said to the documenter; empty when it owes none."""
        triggers = await self._adr_triggers(ctx)
        paths = await self._git.at(ctx.worktree).lines("ls-tree", "-r", "--name-only", HEAD)
        return report(triggers, paths)

    async def documents(self, ctx: AttemptContext) -> list[str]:
        """The documents the task has changed besides its own change record, for the summary."""
        git = self._git.at(ctx.worktree)
        read = await read_changes(git, await self._task_base(git, ctx.task.task_id))
        own = {record_path(ctx.task.task_id), diary_path(ctx.task.task_id)}
        return sorted(
            c.path
            for c in read.files
            if c.exists_now
            and c.path not in own
            and self._classify(c.path) is FileClass.DOCUMENTATION
        )

    async def _adr_triggers(self, ctx: AttemptContext) -> list[Trigger]:
        """The rules that call for a record: a dependency or a module the task brought in."""
        git = self._git.at(ctx.worktree)
        base = await self._task_base(git, ctx.task.task_id)
        changes = await read_changes(git, base)
        manifests = manifests_of(self._adapters.languages())
        before = await read_before(git, changes, lambda path: is_manifest(manifests, path), base)
        added, _ = added_dependencies(ctx.worktree, changes, before, manifests)
        found = [
            Trigger("dependency", name, path)
            for (path, _), names in sorted(added.items(), key=lambda item: item[0][0])
            for name in sorted(names)
        ]
        production = {p for p in changes.current() if self._classify(p) is FileClass.PRODUCTION}
        old = [
            p
            for p in await git.lines("ls-tree", "-r", "--name-only", base)
            if self._classify(p) is FileClass.PRODUCTION
        ]
        brought = [
            c.path for c in changes.files if c.kind in (ChangeKind.ADDED, ChangeKind.RENAMED)
        ]
        return found + new_modules(brought, old, production)

    def _readme_commands(self, worktree: Path) -> tuple[frozenset[Signature], frozenset[Signature]]:
        """The commands the adapters run to build and to test, as the README would name them."""
        found: dict[Operation, set[Signature]] = {Operation.BUILD: set(), Operation.TEST: set()}
        for adapter in self._active(worktree):
            for operation, signatures in found.items():
                plan = adapter.plan(operation, OperationRequest(root=worktree))
                known = signature(plan.argv) if isinstance(plan, CommandPlan) else None
                if known is not None:
                    signatures.add(known)
        return frozenset(found[Operation.BUILD]), frozenset(found[Operation.TEST])

    async def _task_changes(
        self, ctx: AttemptContext
    ) -> tuple[tuple[RecordedFile, ...], tuple[SymbolChange, ...], tuple[str, ...]]:
        """What the task changed in files and symbols, against the commit its branch started from.
        The documents are left out: they are the documenter's own."""
        git = self._git.at(ctx.worktree)
        base = await self._task_base(git, ctx.task.task_id)
        read = await read_changes(git, base)
        changes = replace(
            read,
            files=tuple(
                c for c in read.files if self._classify(c.path) is not FileClass.DOCUMENTATION
            ),
        )
        before = await read_before(git, changes, (), base)
        tracked = await git.lines("ls-files")
        others = {
            path: text
            for path in tracked
            if dialect_of(path) is not None
            if (text := _text_of(ctx.worktree / path)) is not None
        }
        found, unreadable = symbol_changes(changes, before, ctx.worktree, others)
        return file_changes(changes), found, unreadable

    async def _task_base(self, git: GitAt, task_id: str) -> str:
        """The commit the task branch started from: the parent of the task's first commit."""
        found = await git.lines("log", "--reverse", "--format=%H", f"--grep=^Task-Id: {task_id}$")
        return f"{found[0]}^" if found else HEAD

    async def _toolchains(self, tools: Tools, worktree: Path) -> tuple[ToolVersion, ...]:
        """The toolchain version of each language the repository has, as its adapter probes it."""
        found: dict[str, str] = {}
        for adapter in self._active(worktree):
            info = adapter.tool_info(Operation.TEST)
            if isinstance(info, NotSupported) or info.toolchain in found:
                continue
            execution = await tools.run("toolchain", info.toolchain_probe.plan)
            version = (
                extract_version(info.toolchain_probe, execution.lines)
                if execution.finished
                else None
            )
            if version:
                found[info.toolchain] = version
        return tuple(ToolVersion(name=n, version=v) for n, v in sorted(found.items()))

    async def metrics(self, worktree: Path, sinks: SinkFactory) -> dict[str, float]:
        """The numbers that need no test run (lint, complexity), for the change record."""
        tools = self._tools(sinks, Level.EXECUTION, self._run_id)
        return await self._static_metrics(tools, worktree, self._active(worktree))

    async def _static_metrics(
        self, tools: Tools, worktree: Path, active: tuple[ScopedAdapter, ...]
    ) -> dict[str, float]:
        metrics: dict[str, float] = {}
        found = 0
        try:
            for adapter in active:
                lint = await measure(
                    tools,
                    adapter,
                    Operation.LINT,
                    "lint",
                    OperationRequest(root=worktree),
                    self._overrides(adapter, "lint"),
                )
                found += sum(violations(lint.drafts).values())
            metrics["lint_violations"] = float(found)
        except Undecided:
            pass  # a metric that could not be taken is left out, not made up
        metrics |= await self._architecture_metrics(worktree, active)
        files = await self._production_files(worktree)
        if files:
            try:
                complexity = await self._complexity(tools, worktree, files)
            except Undecided:
                return metrics
            metrics["max_ccn"] = float(max(complexity.values(), default=0))
            metrics["functions"] = float(len(complexity))
        return metrics

    def is_test(self, path: str) -> bool:
        return self._classify(path) in TEST_AUTHOR_CLASSES

    async def test_files(self, worktree: Path) -> tuple[str, ...]:
        """The tracked files the language adapters class as tests or fixtures."""
        listed = await self._git.at(worktree).lines("ls-files")
        return tuple(p for p in listed if self.is_test(p))

    async def _production_files(self, worktree: Path) -> tuple[str, ...]:
        listed = await self._git.at(worktree).lines("ls-files")
        return tuple(p for p in listed if self._classify(p) is FileClass.PRODUCTION)

    async def _lizard_lines(
        self, tools: Tools, worktree: Path, files: tuple[str, ...]
    ) -> list[str]:
        plan = lizard_plan(self._lizard, worktree, files)
        execution = await tools.run("complexity", plan)
        if not execution.finished or execution.result.exit_code != 0:
            raise Undecided(execution.inconclusive("complexity"))
        return execution.lines

    async def _complexity(
        self, tools: Tools, worktree: Path, files: tuple[str, ...]
    ) -> dict[tuple[str, str], int]:
        return parse_functions(await self._lizard_lines(tools, worktree, files))

    async def weak_nets(
        self, worktree: Path, sinks: SinkFactory, functions: Sequence[tuple[str, str]]
    ) -> frozenset[tuple[str, str]]:
        """The functions of ``functions`` (file, name) the tests do not protect as gate 7 asks of
        new code: a line no test runs, or a mutation score under the limit. For the planner.

        A function that cannot be measured (no coverage tool, a file the mutation rules cannot
        read, tests that do not run) is not called weak: nothing was seen of it."""
        files = tuple(dict.fromkeys(file for file, _ in functions))
        if not files:
            return frozenset()
        tools = self._tools(sinks, Level.EXECUTION, self._run_id)
        try:
            spans = parse_spans(await self._lizard_lines(tools, worktree, files))
        except Undecided:
            return frozenset()
        active = self._active(worktree, files)
        shares = {id(adapter): CoverageShare() for adapter in active}
        head = (await self._git.at(worktree).lines("rev-parse", "HEAD"))[0]
        values = self._values.get("mutation", {})
        data = GateInput(worktree, head, PLANNING, PLANNING, PLANNING, values=values)
        weak: set[tuple[str, str]] = set()
        for file, name in functions:
            adapter, span = owner_of(active, file), spans.get((file, name))
            if adapter is None or span is None:
                continue
            check = MutationCheck(
                tools,
                adapter,
                worktree,
                NO_CHANGES,
                (file,),
                self._env,
                adapter.language,
                share=shares[id(adapter)],
            )
            try:
                lines = {file: frozenset(range(span.first, span.last + 1))}
                if not await check.protects(data, lines):
                    weak.add((file, name))
            except Undecided:
                continue
        return frozenset(weak)

    async def functions(self, worktree: Path, sinks: SinkFactory) -> dict[tuple[str, str], int]:
        """The cyclomatic complexity of every function of the production code, for the planner."""
        files = await self._production_files(worktree)
        if not files:
            return {}
        tools = self._tools(sinks, Level.EXECUTION, self._run_id)
        try:
            return await self._complexity(tools, worktree, files)
        except Undecided as stop:
            raise RuntimeError(f"the complexity could not be measured: {_describe(stop)}") from None

    # -- attempts and integration -------------------------------------------------------------

    async def judge(
        self, ctx: AttemptContext, sinks: SinkFactory, expectations: Expectations | None = None
    ) -> PipelineOutcome:
        if ctx.task.role in NO_COMMIT:
            return await self._judge_answer(ctx, expectations)
        git = self._git.at(ctx.worktree)
        tools = self._tools(sinks, Level.ATTEMPT, ctx.attempt_id)
        changes = await self._normalized(tools, ctx.worktree, git, await read_changes(git))
        own = own_files(self._adapters)
        before = await read_before(git, changes, own)
        # an extra test stage judges the code the implementer committed, read against what it was
        base = await self._code_base(git, ctx.task.reviews)
        code = changes if base == HEAD else await read_changes(git, base)
        code_before = before if base == HEAD else await read_before(git, code, own, base)
        before_shapes = await read_before(git, code, SHAPE_SUFFIXES, base)
        manifests = manifests_of(self._adapters.languages())
        everything = await read_before(git, changes, ())
        test_texts = await read_before(git, changes, self.is_test)
        artifacts = await read_before(git, changes, is_artifact)
        declared = await read_before(git, changes, lambda path: is_manifest(manifests, path))
        head = (await git.lines("rev-parse", "HEAD"))[0]
        if self._installer is not None:  # before the checks, which run in the environment it makes
            stopped = await install_findings(
                ctx.worktree,
                changes,
                declared,
                manifests,
                requirements_of(self._adapters.languages()),
                self._installer,
                self._registry_get,
            )
            if stopped:
                data = GateInput(
                    ctx.worktree, head, ctx.task.task_id, ctx.task.stage_id, ctx.attempt_id
                )
                return await self._pipeline([DependencyInstallCheck(tuple(stopped))]).run(
                    data, set()
                )
        production = tuple(p for p in code.current() if self._classify(p) is FileClass.PRODUCTION)
        unstable, passing = self._read_base()
        e2e_unstable, e2e_passing = self._read_e2e_base()
        worktree = ctx.worktree
        active = self._active(worktree, (*production, *changes.current(own)))
        shares = {id(a): CoverageShare() for a in active}
        mutation = self._each(
            active,
            lambda a: MutationCheck(
                tools,
                a,
                worktree,
                code,
                self._files_of(active, a, production),
                self._env,
                a.language,
                share=shares[id(a)],
            ),
        )
        complexity = self._each(
            active,
            lambda a: ComplexityCheck(
                tools,
                worktree,
                code,
                self._files_of(active, a, production),
                self._texts_of(active, a, code_before),
                self._lizard,
                a.language,
            ),
        )
        crap = self._each(
            active,
            lambda a: CrapCheck(
                tools,
                a,
                worktree,
                code,
                self._files_of(active, a, production),
                self._texts_of(active, a, code_before),
                self._lizard,
                a.language,
                share=shares[id(a)],
            ),
        )
        tracked_tests = await self.test_files(worktree)
        checks: list[Check] = [
            ScopeCheck(
                changes,
                ctx.task,
                self._classify,
                dependency_edit=dependency_edit_of(self._adapters.languages(), worktree, declared),
                silencers=silencers(self._adapters.for_file),
            ),
            *await self._frozen(ctx, changes),
            SecretsCheck(
                tools, worktree, changes, self._workdir / "secrets" / ctx.attempt_id, self._gitleaks
            ),
            FormatCheck(tools, active, worktree, changes),
            LintCheck(tools, active, worktree, changes, before),
            TypesCheck(tools, active, worktree, changes, before),
            BuildCheck(tools, active, worktree),
            self._tests_check(ctx, tools, active, expectations, unstable, passing),
            mutation,
            complexity,
            crap,
            PassThroughCheck(worktree, code, production, before_shapes),
            ModuleDepthCheck(worktree, code, production, before_shapes),
            DuplicationCheck(
                tools,
                worktree,
                production,
                self._workdir / "duplication" / ctx.attempt_id,
                self._jscpd,
                ignore=tuple(dict.fromkeys((*tracked_tests, *changes.current(self.is_test)))),
                base=base,
            ),
            *await self._architecture(git, worktree, changes, everything, active),
            SastCheck(
                tools,
                worktree,
                changes,
                everything,
                self._workdir / "sast" / ctx.attempt_id,
                self._semgrep_config,
                self._semgrep,
            ),
            DependencyAuditCheck(
                tools,
                worktree,
                changes,
                artifacts,
                self._workdir / "audit" / ctx.attempt_id,
                self._forbidden,
                self._osv_scanner,
            ),
            NewDependencyCheck(worktree, changes, declared, manifests, self._registry_get),
            DifferentialCheck(
                tools,
                self._differential_cases,
                self._differential_base(),
                worktree,
                self._env,
                tuple(dict.fromkeys(a.language for a in active)),
            ),
            E2ECheck(
                tools,
                active,
                worktree,
                self._e2e,
                self._env,
                quarantine=e2e_unstable,
                passing=e2e_passing,
            ),
            PersonalDataCheck(worktree, changes, test_texts, self.is_test),
            PropertiesCheck(worktree, changes, production, self.is_test),
            VoiceCheck(worktree, changes, self._classify, self._markers, ctx.message),
            CommentsCheck(worktree, changes, everything, self._classify),
        ]
        claims = expectations.claims if expectations is not None else ()
        evidence = frozenset({*self._settings, *self._read_base_metrics(), *STATIC_METRICS})
        if claims or ctx.task.role is Producer.DOCUMENTER:  # whoever states something cites it
            checks.append(CitationCheck(worktree, claims, evidence, self._fact_ids))
        if ctx.task.role is Producer.DOCUMENTER:
            files, found, unreadable = await self._task_changes(ctx)
            name = readme_path(worktree)
            tracked = await git.lines("ls-tree", "--name-only", "HEAD", "--", name) if name else []
            old = "\n".join(await git.lines("show", f"HEAD:{name}")) + "\n" if tracked else None
            build, test = self._readme_commands(worktree)
            checks.append(ReadmeCheck(worktree, old, build, test))
            records = frozenset(
                await git.lines("ls-tree", "-r", "--name-only", HEAD, "--", ADR_DIR)
            )
            checks.append(AdrCheck(worktree, tuple(await self._adr_triggers(ctx)), records))
            documents, facts = await read_repository(
                git,
                worktree,
                lambda path: self._classify(path) is FileClass.DOCUMENTATION,
                CHANGES_DIR,
            )
            since = Base(git, await self._task_base(git, ctx.task.task_id))
            checks.append(DocDriftCheck(documents, facts, since))
            checks.append(
                ChangeRecordCheck(
                    worktree, ctx.task.task_id, render_changed(files, found, unreadable)
                )
            )
        data = GateInput(worktree, head, ctx.task.task_id, ctx.task.stage_id, ctx.attempt_id)
        return await self._pipeline(checks).run(data, _capabilities(active))

    async def _judge_answer(
        self, ctx: AttemptContext, expectations: Expectations | None
    ) -> PipelineOutcome:
        """The specifier and the archaeologist write no file: only that holds and their claims cite
        what exists. The specifier's criteria and the elements of the input lead to each other."""
        git = self._git.at(ctx.worktree)
        changes = await read_changes(git)
        spec = expectations or Expectations(frozenset(), ())
        evidence = frozenset({*self._settings, *self._read_base_metrics(), *STATIC_METRICS})
        checks: list[Check] = [
            ScopeCheck(
                changes, ctx.task, self._classify, silencers=silencers(self._adapters.for_file)
            ),
            CitationCheck(ctx.worktree, spec.claims, evidence, self._fact_ids),
        ]
        if ctx.task.role is Producer.SPECIFIER:
            checks.append(TraceabilityCheck(spec.elements, spec.specified, spec.left_out))
        head = (await git.lines("rev-parse", "HEAD"))[0]
        data = GateInput(ctx.worktree, head, ctx.task.task_id, ctx.task.stage_id, ctx.attempt_id)
        return await self._pipeline(checks).run(data, ())

    async def _architecture(
        self,
        git: GitAt,
        worktree: Path,
        changes: Changes,
        was: dict[str, str],
        active: tuple[ScopedAdapter, ...],
    ) -> list[Check]:
        """The checks of gate 9, each answering for every project: the graph of its production
        files in the parent commit and in the attempt, with no copy of the worktree."""
        tracked = await git.lines("ls-files")
        states = {
            id(a): self._project_states(worktree, changes, was, tracked, active, a) for a in active
        }
        return [
            self._each(active, lambda a: DependencyCyclesCheck(*states[id(a)])),
            self._each(
                active,
                lambda a: LayerRulesCheck(
                    *states[id(a)], self._layers, "" if a.base == "." else a.base
                ),
            ),
            self._each(active, lambda a: PackageMetricsCheck(*states[id(a)])),
        ]

    def _project_states(
        self,
        worktree: Path,
        changes: Changes,
        was: dict[str, str],
        tracked: list[str],
        active: tuple[ScopedAdapter, ...],
        project: ScopedAdapter,
    ) -> tuple[Snapshot, Snapshot, tuple[str, ...]]:
        """The project's production files before and after the attempt, by path inside the project,
        and the ones the attempt changed. A project the attempt left alone has nothing to read."""

        def mine(path: str) -> bool:
            return (
                dialect_of(path) is not None
                and self._classify(path) is FileClass.PRODUCTION
                and owner_of(active, path) is project
            )

        changed = tuple(project.local(p) for p in changes.current() if mine(p))
        if not changed:
            return Snapshot(project.language, {}), Snapshot(project.language, {}), ()
        moved = {c.path for c in changes.files}
        after: dict[str, str] = {}
        before: dict[str, str] = {}
        for path in filter(mine, tracked):
            text = _text_of(worktree / path)
            if text is not None:
                after[project.local(path)] = text
                if path not in moved:
                    before[project.local(path)] = text
        for change in changes.files:
            old = change.before
            if old is not None and old in was and mine(old):
                before[project.local(old)] = was[old]
        manifest = project.repository("go.mod") if project.language == "go" else None
        now = _text_of(worktree / manifest) if manifest else None
        old_manifest = was.get(manifest, now) if manifest else None
        return (
            Snapshot(project.language, before, old_manifest),
            Snapshot(project.language, after, now),
            changed,
        )

    async def _architecture_metrics(
        self, worktree: Path, active: tuple[ScopedAdapter, ...]
    ) -> dict[str, float]:
        """Cycles, edges and the largest distance from the main sequence of each project's graph.
        A project whose files cannot be read is left out, not made up."""
        found: dict[str, float] = {}
        tracked = [p for p in await self._production_files(worktree) if dialect_of(p) is not None]
        for project in active:
            files = {
                project.local(p): text
                for p in tracked
                if owner_of(active, p) is project and (text := _text_of(worktree / p)) is not None
            }
            manifest = project.repository("go.mod") if project.language == "go" else None
            try:
                graph = build(
                    project.language, files, _text_of(worktree / manifest) if manifest else None
                )
            except Unavailable:
                continue
            where = "" if project.base == "." else f"[{project.base}]"
            found[f"dependency_cycles{where}"] = float(len(graph.cycles()))
            found[f"dependency_edges{where}"] = float(len(graph.edges))
            found[f"max_distance{where}"] = round(
                max(graph.metrics(folder).distance for folder in graph.nodes), 2
            )
        return found

    async def _code_base(self, git: GitAt, stage: str) -> str:
        """The commit before the one that holds the work of ``stage``; ``HEAD`` for no stage."""
        if not stage:
            return HEAD
        found = await git.lines("log", "-1", "--format=%H", f"--grep=^Stage-Id: {stage}$")
        if not found:
            raise RuntimeError(f"the work of stage {stage} is not in the history")
        return f"{found[0]}^"

    async def _frozen(self, ctx: AttemptContext, changes: Changes) -> list[Check]:
        task = ctx.task
        if task.reviews:  # an extra test stage only adds tests
            return [AppendOnlyCheck(changes, task.test_root, self.is_test)]
        if not task.frozen_digest:
            return []
        tests = await self.test_files(ctx.worktree)
        return [
            FreezeCheck(
                ctx.worktree, changes, task.test_root, task.frozen_digest, tests, self.is_test
            )
        ]

    def _tests_check(
        self,
        ctx: AttemptContext,
        tools: Tools,
        active: tuple[ScopedAdapter, ...],
        expectations: Expectations | None,
        unstable: frozenset[str],
        passing: frozenset[str],
    ) -> Check:
        """The test author's tests are red on purpose, so its stage has its own judge of them;
        the tests of an extra stage describe code that exists, so they must pass, and so do the
        ones written before a refactoring."""
        task = ctx.task
        if task.role is not Producer.TEST_AUTHOR or task.reviews or task.characterizes:
            return TestsCheck(tools, active, ctx.worktree, unstable, passing)
        assert expectations is not None  # the cycle gives them to every test author stage
        return RedGreenCheck(
            tools,
            active,
            ctx.worktree,
            self._classify,
            expectations.criteria,
            expectations.declared,
            unstable,
            passing,
        )

    async def integration(self, worktree: Path, sinks: SinkFactory) -> PipelineOutcome:
        git = self._git.at(worktree)
        tools = self._tools(sinks, Level.EXECUTION, self._run_id)
        unstable, passing = self._read_base()
        active = self._active(worktree)
        checks: list[Check] = [
            BuildCheck(tools, active, worktree),
            TestsCheck(tools, active, worktree, unstable, passing),
        ]
        head = (await git.lines("rev-parse", "HEAD"))[0]
        data = GateInput(worktree, head, "integration", "integration", "integration")
        return await self._pipeline(checks).run(data, _capabilities(active))

    @staticmethod
    def _files_of(
        projects: tuple[ScopedAdapter, ...], project: ScopedAdapter, files: tuple[str, ...]
    ) -> tuple[str, ...]:
        return tuple(p for p in files if owner_of(projects, p) is project)

    @staticmethod
    def _texts_of(
        projects: tuple[ScopedAdapter, ...], project: ScopedAdapter, texts: dict[str, str]
    ) -> dict[str, str]:
        return {p: t for p, t in texts.items() if owner_of(projects, p) is project}

    @staticmethod
    def _each(active: tuple[ScopedAdapter, ...], make: Callable[[ScopedAdapter], Check]) -> Check:
        """The check made for every language of the project, answering as one."""
        parts = [Part(make(adapter), frozenset(_capabilities([adapter]))) for adapter in active]
        return EachLanguage(parts, parts[0].check.id, parts[0].check.gate)

    def _pipeline(self, checks: list[Check]) -> GatePipeline:
        return GatePipeline(checks, self._settings, self._values)

    async def reconcile(self, operation: OpenOperation) -> Reconciliation:
        """A tool run changes nothing the next one depends on, so it is simply run again."""
        return Reconciliation(Decision.REDO)


def _text_of(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _capabilities(adapters: Iterable[ScopedAdapter]) -> set[str]:
    return {capability.value for adapter in adapters for capability in adapter.capabilities}


def _describe(stop: Undecided) -> str:
    result = stop.result
    if isinstance(result, Inconclusive):
        return result.detail
    return f"not applicable: {result.missing_capability}"
