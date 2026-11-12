"""The agent runner for a real CLI: a copy of the worktree without git, the call, the way back.

The agent works in a copy, so it cannot touch git or the other worktrees. Its work comes back only
if it declared what it did (``.forja-agent/saida.json``, the output contract of every role) and the
declaration matches the files that really changed. What the CLI prints is read as it arrives: it
becomes activity and token events, and a CLI that stays silent for too long is ended.
"""

import asyncio
import contextlib
import re
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from forja.checks.readme import MODEL as README_MODEL
from forja.core.adapters.common import build_spec
from forja.core.adapters.executor import (
    AGENT_DIR,
    DIARY_FILE,
    AgentActivity,
    AgentRequest,
    CommandActivity,
    ErrorActivity,
    ErrorKind,
    ExecutorAdapter,
    FileEditActivity,
    FileReadActivity,
    InstructionChannel,
    PermissionSetup,
    RolePermissions,
    TextActivity,
    UsageActivity,
)
from forja.core.artifacts.elements import elements_of
from forja.core.artifacts.errors import InvalidLlmOutput
from forja.core.artifacts.llm_output import (
    ChangeDeclaration,
    Claim,
    DeclaredTest,
    LlmOutput,
    compare_with_diff,
    parse_llm_output,
)
from forja.core.artifacts.models import Producer
from forja.core.artifacts.schemas import CriteriaBody
from forja.core.events import EventLog, OpenOperation
from forja.core.idempotency import (
    Reconciliation,
    reconcile_apply_diff,
    reconcile_prepare_workcopy,
)
from forja.core.isolation import SensitivePaths, git_blocking_environment
from forja.core.manifest import (
    EntryKind,
    Manifest,
    ManifestDiff,
    ManifestEntry,
    build_manifest,
    diff_manifests,
    find_violations,
)
from forja.core.process import EventSink, ProcessIdentity, ProcessRunner
from forja.core.redaction import PackageItem, Redactor
from forja.core.redaction.engine import ItemKind
from forja.core.states import Level
from forja.orchestrator.budget import AGENT_ACTIVITY
from forja.orchestrator.criteria import manifest_of
from forja.orchestrator.operations import Step
from forja.orchestrator.ports import AgentResult, AttemptContext, RejectedWork

PACKAGE = "package.md"
README_REPORT = "README report"
ADR_REPORT = "ADR report"
FACTS_REPORT = "Recorded facts"
FINDINGS_REPORT = "Findings"
DIARY_REPORT = "Earlier attempts"
SAID_REPORT = "What the agent said"
REJECTION_REPORT = "Why the last attempt was rejected"
DIARY_ORDER = (
    f"Before you do anything else, read `{AGENT_DIR}/{DIARY_FILE}`. It holds every earlier attempt "
    "of this stage, whole: what you said and why each one was turned down. Do not act until "
    "you have read it."
)
OUTPUT = "saida.json"
IDLE_LIMIT = 300.0  # seconds without a single line of output: the CLI is stuck, not working
OPERATION = "call_agent_cli"
NO_COMMANDS = "You cannot run commands. "
FACADE_COMMANDS = (
    "You cannot run commands except exactly `forja agente lint`, `forja agente medir` and "
    "`forja agente testar` (nothing added to the line), which show what the checks will see. "
)

INSTRUCTIONS = """\
You are refactoring existing code. The behaviour must stay exactly the same: same results, same
public names, same side effects. Do not change tests and do not add dependencies.

{commands}Read the files you need and change them with the edit tools.

When you are done, write the file `{agent_dir}/{output}` with this shape and nothing else, listing
every file you created, modified or removed (the list is checked against what really changed):

{{"actions": [{{"summary": "what changed", "reason": "why it is better",
"files": [{{"path": "relative/path.py", "change": "modified"}}]}}]}}

`change` is one of `created`, `modified`, `removed`. Use no key that is not in the shape: an extra
key, such as `evidence` in an action, makes the file invalid and costs an attempt. Add
`"assumptions"` only for something you had to guess: a list of objects {{"statement": "what you
guessed", "reason": "why", "missing_evidence": "what would settle it"}}, never plain strings. Then
answer with one short sentence.
"""

AUTHOR_INSTRUCTIONS = """\
You are writing tests for a defect. You do not fix it and you do not touch production code: the
defect stays, and your tests must show it.

{commands}Read the files you need and write the tests with the edit tools, only {tests_in}.
Write at least one test for each numbered criterion of the task. Every test you declare must FAIL
on the code as it is now, by an assertion about the behaviour in the criterion. Your work is
rejected if a declared test passes now, if it errors out (an import, a syntax or a collection
error is not a failing test), if it asserts nothing about the code, or if a test that exists now
stops passing. Do not change the tests that exist; add new files or new tests.
Prefer the standard library and the packages the project already declares. Add a new
dependency to the manifest only when the work cannot reasonably be done without it, add no more
than that, and say in the `reason` of the action why nothing already available serves.

When you are done, write the file `{agent_dir}/{output}` with this shape and nothing else. List
every file you created, modified or removed in `actions` (the list is checked against what really
changed) and, in `tests`, the tests that cover each criterion; `test_id` is the id pytest prints
for the test (`path::name`) and `path` is its file:

{{"actions": [{{"summary": "what changed", "reason": "why", "files": [{{"path": "tests/test_x.py",
"change": "created"}}]}}], "tests": [{{"criterion_id": 1, "test_id": "tests/test_x.py::test_name",
"path": "tests/test_x.py"}}]}}

`change` is one of `created`, `modified`, `removed`. Use no key that is not in the shape: an extra
key, such as `evidence` in an action, makes the file invalid and costs an attempt. Add
`"assumptions"` only for something you had to guess: a list of objects {{"statement": "what you
guessed", "reason": "why", "missing_evidence": "what would settle it"}}, never plain strings. Then
answer with one short sentence.
"""

FIX_INSTRUCTIONS = """\
You are fixing a defect. The tests {tests_in} were written for it and are frozen: they
fail now and must pass when you are done. Do not change, add, move or remove anything
{tests_in}. Change only what the defect needs.
Prefer the standard library and the packages the project already declares. Add a new
dependency to the manifest only when the work cannot reasonably be done without it, add no more
than that, and say in the `reason` of the action why nothing already available serves.

{commands}Read the tests and the files you need and change the code with the edit tools.

When you are done, write the file `{agent_dir}/{output}` with this shape and nothing else, listing
every file you created, modified or removed (the list is checked against what really changed):

{{"actions": [{{"summary": "what changed", "reason": "why it fixes the defect",
"files": [{{"path": "relative/path.py", "change": "modified"}}]}}]}}

`change` is one of `created`, `modified`, `removed`. Use no key that is not in the shape: an extra
key, such as `evidence` in an action, makes the file invalid and costs an attempt. Add
`"assumptions"` only for something you had to guess: a list of objects {{"statement": "what you
guessed", "reason": "why", "missing_evidence": "what would settle it"}}, never plain strings. Then
answer with one short sentence.
"""

README_SECTIONS = ", ".join(f"`{name}`" for name, _ in README_MODEL)
# the sections the gate asks for (each answers one of its questions); the rest are optional
README_REQUIRED = ", ".join(f"`{name}`" for name, answers in README_MODEL if answers)

DOCUMENTER_INSTRUCTIONS = """\
You are documenting a change that is already made and approved. You do not touch code, tests or
configuration: you may change only documentation files (the README, files under `docs/`, other
Markdown or reStructuredText files). Do not write the change record: Forja writes it from your
claims.

{commands}Read the files you need and change the documents with the edit tools. Change a document
only when it is wrong or missing something because of this change. If the task below says a
decision record is owed, write it: the alternatives you weigh and the consequences, each backed
by the claims you declare.

A README follows this model, in this order: a title, status badges and a status line; a topics
index with a link to each section; then the sections {model}. Head each section with the name
above, or its equivalent in the language the README is already written in. The gate asks only for
{required}; write any other section only when there is something true to put in it (no deploy, no
open tasks, no filler), and never to complete the model. Keep the README easy to read: each
section as short as it can be and still true, a few lines or one short list; nothing repeated
from another section; nothing that is not about this project; no section rewritten when it
already answers what its heading asks. Every command you write in a section is one you can run.
The README report below, when there is one, names the sections that are missing or wrong: change
only those.

When you are done, write the file `{agent_dir}/{output}` with this shape and nothing else. In
`claims`, say why the change was made and what it affects, each statement with at least one
citation: a file and lines (`file`), a measurement (`evidence`) or a recorded fact (`fact`). In
`actions`, list every file you created, modified or removed (checked against what really changed);
use `"actions": []` if you changed nothing:

{{"claims": [{{"statement": "why or what it affects", "citations": [{{"kind": "file",
"path": "src/x.py", "line_start": 3, "line_end": 9}}]}}],
"actions": [{{"summary": "what changed", "reason": "why", "files": [{{"path": "README.md",
"change": "modified"}}]}}]}}

`change` is one of `created`, `modified`, `removed`. Use no key that is not in the shape: an extra
key, such as `evidence` in an action, makes the file invalid and costs an attempt. Add
`"assumptions"` only for something you had to guess: a list of objects {{"statement": "what you
guessed", "reason": "why", "missing_evidence": "what would settle it"}}, never plain strings. Then
answer with one short sentence.
"""

SPECIFIER_INSTRUCTIONS = """\
You are writing the acceptance criteria for the task below. You do not touch code, tests or
documents, and you write no file but the answer: the criteria go in it. The input was cut into
numbered elements (below); every element must become at least one criterion, or be left out with
a reason.

{commands}Read the files you need to see what the code does today. Each criterion is one thing a
test can check, stated by the behaviour a person or a program sees. `level` is `unit`,
`integration` or `e2e` (a flow through the whole program is `e2e`). `origin` lists the ids of the
elements the criterion comes from. If a flow cannot be tested end to end, give it the level it
can have and say why in `assumptions`.

When you are done, write the file `{agent_dir}/{output}` with this shape and nothing else:

{{"criteria": [{{"id": 1, "text": "what must hold", "level": "unit", "origin": ["E1"]}}],
"left_out": [{{"element": "E3", "reason": "why it is not built",
"missing_evidence": "what would settle it"}}],
"claims": [{{"statement": "what the code does today", "citations": [{{"kind": "file",
"path": "src/x.py", "line_start": 3, "line_end": 9}}]}}],
"actions": []}}

The ids of the criteria are 1, 2, 3... with no repeats. `claims` and `left_out` may be empty
lists. Add `"assumptions"` only for something you had to guess: a list of objects
{{"statement": "what you guessed", "reason": "why", "missing_evidence": "what would settle it"}},
never plain strings. Then answer with one short sentence.
"""

ARCHAEOLOGIST_INSTRUCTIONS = """\
You are finding out how the code works, to answer the task below. You do not touch code, tests or
documents, and you write no file but the answer: what you found goes in it.

{commands}Read the files you need. Every statement you make in `claims` must be backed by at least
one citation: a file and lines (`file`), a measurement (`evidence`) or a recorded fact (`fact`),
and it must be something the cited lines or fact show, not something you infer. What you could not
verify and had to guess is not a claim: put it in `assumptions`, with why you guessed it and what
evidence would settle it.

When you are done, write the file `{agent_dir}/{output}` with this shape and nothing else:

{{"claims": [{{"statement": "what the code does", "citations": [{{"kind": "file",
"path": "src/x.py", "line_start": 3, "line_end": 9}}]}}],
"assumptions": [{{"statement": "what you guessed", "reason": "why", "missing_evidence": "what
would settle it"}}],
"actions": []}}

Give at least one claim or one assumption. Then answer with one short sentence.
"""

FINDINGS_INSTRUCTIONS = """\
There is no change to document in this task: it asked a question, and an archaeologist answered
it. Write the documentation from the findings below, in files under `docs/`. Keep the three kinds
apart, each under a heading of its own: what is proven, what a person confirmed, and the premises,
which you present as unverified. Do not turn a premise into a statement of fact. Cite what you
state in `claims`, as above.
"""

ELEMENTS = "Elements of the input"

_SHORT_NAME = re.compile(r"~\d")


def plain(path: str) -> str:
    """The path as Windows would read it: trailing dots and spaces of each part are dropped."""
    return "/".join(part.rstrip(" .") for part in path.replace("\\", "/").split("/"))


def touches_git(path: str) -> bool:
    """Whether a path goes through git's own directory, by any spelling the system would accept.

    Windows ignores trailing dots and spaces and has short names (``GIT~1``), and the file system
    may fold case, so each part is compared in a normal form.
    """
    for part in path.replace("\\", "/").split("/"):
        plain = part.casefold().rstrip(" .")
        if plain == ".git" or plain.startswith("git~"):
            return True
    return False


def _wipe(directory: Path, keep: frozenset[str], prefix: str = "") -> None:
    """Empty ``directory`` of everything but git's own entry and the paths in ``keep``."""
    for entry in directory.iterdir():
        relative = f"{prefix}{entry.name}"
        if (not prefix and entry.name == ".git") or relative in keep:
            continue
        if entry.is_dir() and not (entry.is_symlink() or entry.is_junction()):
            _wipe(entry, keep, f"{relative}/")
            if not any(entry.iterdir()):
                entry.rmdir()
        else:
            entry.unlink()


def _refuse_links_on_the_way(worktree: Path, path: str) -> None:
    """Writing into the worktree must never go through a link that is already there."""
    parts = path.split("/")
    for i in range(len(parts)):
        step = worktree.joinpath(*parts[: i + 1])
        if step.is_symlink() or step.is_junction():
            raise RuntimeError(f"{path} would be written through a link in the worktree")


def workcopy_of(ctx: AttemptContext) -> Path:
    return ctx.worktree.with_name(f"{ctx.attempt_id}-copy")


def _quote(text: str) -> str:
    return "\n".join(f"> {line}".rstrip() for line in text.strip().splitlines())


def diary_document(ctx: AttemptContext, redactor: Redactor) -> str:
    """The file the agent is told to read: every earlier attempt, redacted like the rest."""
    return redacted_diary(ctx.diary, redactor)


def redacted_diary(diary: str, redactor: Redactor) -> str:
    """A diary text through the redactor: secrets masked, the rest of the text whole."""
    if not diary:
        return ""
    result = redactor.redact([PackageItem(ItemKind.TEXT, DIARY_REPORT, diary)])
    for dropped in result.dropped:
        return f"The earlier attempts were withheld ({dropped.reason.value}).\n"
    return result.items[0].content if result.items else ""


def _fence(text: str) -> str:
    """A code fence longer than any run of backticks inside the text."""
    ticks = 3
    while "`" * ticks in text:
        ticks += 1
    return "`" * ticks


def build_package(
    ctx: AttemptContext,
    redactor: Redactor,
    *,
    facade: bool = False,
    voice: str = "",
    dry: str = "",
    properties: str = "",
) -> str:
    """The text the agent receives: the task, the rules, the files in scope and the last verdict.

    ``voice`` is the Forja's own instruction on how to write (``voice_brief.brief``): fixed text,
    so it does not pass through the redactor. Empty leaves the section out. ``dry`` is the one on
    duplication (``dry_brief.brief``), the same kind of text, given only to a role that changes
    production code. ``properties`` is the one on property tests (``properties_brief.brief``),
    given only to the test author.
    """
    task = ctx.task
    items, notes = [], []
    for name in task.scope:
        path = ctx.worktree / name
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:  # a binary file, or text in another encoding
                notes.append(f"`{name}` is not UTF-8 text: read it yourself if you need it.")
                continue
            items.append(PackageItem(ItemKind.FILE, name, content, True))
        else:
            notes.append(f"`{name}` is a directory: read the files you need from it.")
    documenter = task.role is Producer.DOCUMENTER
    if documenter and ctx.readme:  # text from the repository's README: redacted like a file
        items.append(PackageItem(ItemKind.TEXT, README_REPORT, ctx.readme, True))
    if documenter and ctx.adr:  # names come from the repository's manifests: redacted too
        items.append(PackageItem(ItemKind.TEXT, ADR_REPORT, ctx.adr, True))
    if documenter and ctx.findings:  # the archaeologist read the repository: redacted like a file
        items.append(PackageItem(ItemKind.TEXT, FINDINGS_REPORT, ctx.findings, True))
    if ctx.facts:  # text a person wrote: redacted like the rest
        recorded = "\n".join(f"- `{f.id}` ({f.author}, {f.date}): {f.text}" for f in ctx.facts)
        items.append(PackageItem(ItemKind.TEXT, FACTS_REPORT, recorded, True))
    if ctx.previous_said:  # what the agent itself said in the last rejected attempt
        items.append(PackageItem(ItemKind.TEXT, SAID_REPORT, ctx.previous_said, True))
    if ctx.previous_rejection:  # the output of the repository's tools and tests: redacted too
        items.append(PackageItem(ItemKind.TEXT, REJECTION_REPORT, ctx.previous_rejection, True))
    result = redactor.redact(items)
    report = next((i.content for i in result.items if i.name == README_REPORT), "")
    author = task.role is Producer.TEST_AUTHOR
    specifier = task.role is Producer.SPECIFIER
    archaeologist = task.role is Producer.ARCHAEOLOGIST
    commands = FACADE_COMMANDS if facade else NO_COMMANDS
    tests_in = f"under `{task.test_root}`" if task.test_root else "in the project's test files"
    template = (
        DOCUMENTER_INSTRUCTIONS
        if documenter
        else SPECIFIER_INSTRUCTIONS
        if specifier
        else ARCHAEOLOGIST_INSTRUCTIONS
        if archaeologist
        else AUTHOR_INSTRUCTIONS
        if author
        else FIX_INSTRUCTIONS
        if task.frozen_digest
        else INSTRUCTIONS
    )
    parts = [
        f"# Task\n\n{task.intent}\n",
        template.format(
            agent_dir=AGENT_DIR,
            output=OUTPUT,
            commands=commands,
            tests_in=tests_in,
            model=README_SECTIONS,
            required=README_REQUIRED,
        ),
        "## What you may change\n",
        (
            "Only documentation files. The files below are the ones the change touched."
            if documenter
            else "No file. The files below are for reading."
            if specifier or archaeologist
            else f"Only {tests_in}. The production files below are for reading."
            if author
            else f"Only inside: {', '.join(f'`{name}`' for name in task.scope)}."
            if task.scope
            else "Any production file."
        ),
        f"At most {task.max_files} files and {task.max_lines} changed lines.\n",
    ]
    if voice:
        parts.append(f"## Voice\n\n{voice}")
    if dry and not (documenter or specifier or archaeologist or author):
        parts.append(f"## Duplication\n\n{dry}")
    if properties and author:
        parts.append(f"## Property tests\n\n{properties}")
    if specifier:
        listed = "\n".join(f"- {e.id}: {e.text}" for e in elements_of(task.intent))
        parts.append(f"## {ELEMENTS}\n\n{listed}\n")
    adr = next((i.content for i in result.items if i.name == ADR_REPORT), "")
    recorded = next((i.content for i in result.items if i.name == FACTS_REPORT), "")
    found = next((i.content for i in result.items if i.name == FINDINGS_REPORT), "")
    said = next((i.content for i in result.items if i.name == SAID_REPORT), "")
    rejection = next((i.content for i in result.items if i.name == REJECTION_REPORT), "")
    hidden_names = (
        README_REPORT,
        ADR_REPORT,
        FACTS_REPORT,
        FINDINGS_REPORT,
        SAID_REPORT,
        REJECTION_REPORT,
    )
    shown = [i for i in result.items if i.name not in hidden_names]
    if shown or notes:
        parts.append("## Files in scope\n")
    for item in shown:
        fence = _fence(item.content)
        parts.append(f"### `{item.name}`\n\n{fence}\n{item.content}\n{fence}\n")
    parts += notes
    for dropped in result.dropped:
        parts.append(f"`{dropped.name}` was withheld ({dropped.reason.value}).")
    if report:
        parts.append(
            f"## README sections to fix\n\n{report}\n\n"
            "Change only these sections of the README (or add the one that is missing); every "
            "other section stays exactly as it is.\n"
        )
    if recorded:
        parts.append(
            f"## Facts a person recorded\n\n{recorded}\n\n"
            'They are true. To rest a claim on one, cite it: {"kind": "fact", "fact_id": "<id>"}.\n'
        )
    if found:
        parts.append(f"## Findings\n\n{FINDINGS_INSTRUCTIONS}\n{found}\n")
    if adr:
        parts.append(f"## Decision record owed\n\n{adr}\n")
    if ctx.diary:  # fixed text of the Forja: the attempts themselves are in the file
        parts.append(f"## Earlier attempts of this stage\n\n{DIARY_ORDER}\n")
    if ctx.hypothesis:  # fixed text of the Forja: nothing to redact
        parts.append(f"## Attack the hypothesis\n\n{ctx.hypothesis}\n")
    if rejection:
        parts.append(f"## The previous attempt was rejected\n\n{rejection}\n")
        if said:
            parts.append(f"What you said in that attempt:\n\n{_quote(said)}\n")
    return "\n".join(parts) + "\n"


class Watch:
    """Passes the runner's reports on, reads the CLI's output as activity, notes when it spoke."""

    def __init__(
        self,
        inner: EventSink,
        log: EventLog,
        ctx: AttemptContext,
        adapter: ExecutorAdapter,
        clock: Callable[[], float],
    ) -> None:
        self._inner, self._log, self._ctx = inner, log, ctx
        self._adapter, self._clock = adapter, clock
        self.last_output = clock()
        self.error: ErrorActivity | None = None
        self.operation_id = ""

    def intent(self, operation_id: str, details: Mapping[str, object]) -> None:
        self.operation_id = operation_id
        self._inner.intent(operation_id, details)

    def started(self, operation_id: str, identity: ProcessIdentity) -> None:
        self._inner.started(operation_id, identity)

    def effect(self, operation_id: str, details: Mapping[str, object]) -> None:
        self._inner.effect(operation_id, details)

    def progress(self, operation_id: str, channel: str, line: str) -> None:
        self.last_output = self._clock()
        self._inner.progress(operation_id, channel, line)
        if channel != "out":
            return
        activity = self._adapter.parse_event(line)
        if activity is None:
            return
        if isinstance(activity, ErrorActivity):
            self.error = activity
        self._log.append_progress(
            Level.ATTEMPT,
            self._ctx.attempt_id,
            AGENT_ACTIVITY,
            operation=OPERATION,
            operation_id=operation_id,
            payload=_payload(activity),
        )


def _payload(activity: AgentActivity) -> dict[str, Any]:
    match activity:
        case UsageActivity(input_tokens, output_tokens):
            return {"type": "usage", "input": input_tokens or 0, "output": output_tokens or 0}
        case TextActivity(text):
            return {"type": "text", "text": text[:200]}
        case FileReadActivity(path):
            return {"type": "file_read", "path": path}
        case FileEditActivity(path, added, removed):
            return {"type": "file_edit", "path": path, "added": added, "removed": removed}
        case CommandActivity(command, exit_code):
            return {"type": "command", "command": command[:200], "exit_code": exit_code}
        case ErrorActivity(message, kind):
            return {"type": "error", "message": message[:200], "kind": kind.value}
    raise AssertionError(activity)


class ExecutorAgentRunner:
    """``AgentRunner`` over any ``ExecutorAdapter``; the CLI itself is the user's to have set up."""

    def __init__(
        self,
        adapter: ExecutorAdapter,
        executable: Path,
        host_env: Mapping[str, str],
        log: EventLog,
        *,
        redactor: Redactor | None = None,
        sensitive: SensitivePaths | None = None,
        role: Producer = Producer.IMPLEMENTER,
        permissions: RolePermissions = RolePermissions(can_edit_files=True),  # noqa: B008
        model: str = "",
        voice: str = "",
        dry: str = "",
        properties: str = "",
        idle_limit: float = IDLE_LIMIT,
        poll: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._adapter, self._executable, self._env, self._log = adapter, executable, host_env, log
        self._sensitive = sensitive or SensitivePaths.with_defaults()
        self._redactor = redactor or Redactor(sensitive=self._sensitive)
        self._role, self._permissions, self._model, self._voice = role, permissions, model, voice
        self._dry, self._properties = dry, properties
        self._idle_limit, self._poll, self._clock = idle_limit, poll, clock

    # -- the copy the agent works in ------------------------------------------------------------

    def prepare_workcopy(self, ctx: AttemptContext) -> Step:
        target = workcopy_of(ctx)

        async def payload() -> dict[str, Any]:
            seen = self._visible(build_manifest(ctx.worktree, exclude={".git"}))
            return {"workcopy": str(target), "expected_digest": seen.digest()}

        async def run() -> None:
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(ctx.worktree, target, ignore=self._left_out(ctx.worktree))
            agent_dir = target / AGENT_DIR
            agent_dir.mkdir()
            package = build_package(
                ctx,
                self._redactor,
                facade=bool(self._permissions.allowed_commands),
                voice=self._voice,
                dry=self._dry,
                properties=self._properties,
            )
            (agent_dir / PACKAGE).write_text(package, encoding="utf-8", newline="\n")
            if ctx.diary:
                diary = diary_document(ctx, self._redactor)
                (agent_dir / DIARY_FILE).write_text(diary, encoding="utf-8", newline="\n")
            setup = self._setup()
            for name, data in setup.files.items():
                (agent_dir / name).write_bytes(data)

        return Step(payload, run)

    def _setup(self) -> PermissionSetup:
        """The role's permissions and, where the CLI has a channel for it, the voice instruction."""
        setup = self._adapter.permission_setup(self._role, self._permissions)
        if not self._voice or not isinstance(self._adapter, InstructionChannel):
            return setup
        channel = self._adapter.instruction_setup(self._voice)
        return PermissionSetup(
            {**setup.files, **channel.files}, (*setup.extra_args, *channel.extra_args)
        )

    # -- the call ---------------------------------------------------------------------------------

    async def call(self, ctx: AttemptContext, operation_id: str, sink: EventSink) -> AgentResult:
        workcopy = workcopy_of(ctx)
        request = AgentRequest(
            self._role, workcopy / AGENT_DIR / PACKAGE, workcopy, self._permissions, self._model
        )
        setup = self._setup()
        plan = self._adapter.plan_invocation(request, setup, self._executable)
        # git must fail in the copy even if a repository lies above it (contract 3)
        blocking = git_blocking_environment(workcopy)
        plan = replace(plan, env_set={**plan.env_set, **blocking})
        spec = build_spec(plan, self._env, timeout=None)
        watch = Watch(sink, self._log, ctx, self._adapter, self._clock)
        log_path = ctx.worktree.parent / f"{operation_id}.log"
        task = asyncio.create_task(ProcessRunner(watch).run(operation_id, spec, log_path))
        stalled = False
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=self._poll)
                if not task.done() and self._clock() - watch.last_output > self._idle_limit:
                    stalled = True
                    break
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if stalled:
            message = f"the agent printed nothing for {self._idle_limit:.0f} seconds"
            return AgentResult(error=ErrorActivity(message, ErrorKind.TRANSIENT))
        if watch.error is not None:
            return AgentResult(error=watch.error)
        result = task.result()
        if result.exit_code != 0:
            message = (
                f"the agent CLI ended with {result.outcome.value}, exit code {result.exit_code}"
            )
            return AgentResult(error=ErrorActivity(message, ErrorKind.TRANSIENT))
        return AgentResult(reason=_reason(workcopy))

    # -- the way back -----------------------------------------------------------------------------

    def apply_diff(self, ctx: AttemptContext) -> Step:
        source = workcopy_of(ctx)

        async def payload() -> dict[str, Any]:
            output = _declared(source)
            if self._role is Producer.SPECIFIER:
                _check_criteria(output)
            if self._role is Producer.ARCHAEOLOGIST:
                _check_findings(output)
            if self._role is Producer.TEST_AUTHOR and not ctx.task.reviews:
                _check_manifest(output)
            declaration = ChangeDeclaration.from_output(output)
            after = build_manifest(source, exclude={AGENT_DIR})
            hidden = self._hidden(ctx.worktree)
            before = self._visible(build_manifest(ctx.worktree, exclude={".git"}))
            self._refuse_tampering(source, ctx)
            self._refuse_off_limits(after, diff_manifests(before, after))
            self._refuse_collisions(after, hidden)
            problems = find_violations(after, destination_root=ctx.worktree)
            if problems:
                raise RejectedWork("; ".join(f"{p.path}: {p.detail}" for p in problems))
            differences = compare_with_diff(declaration, diff_manifests(before, after))
            if differences:
                raise RejectedWork(
                    "the files declared are not the files changed: "
                    + "; ".join(
                        f"{d.path} declared {d.declared or 'nothing'}, "
                        f"changed {d.actual or 'nothing'}"
                        for d in differences
                    )
                )
            kept = Manifest({**after.entries, **hidden})
            return {"worktree": str(ctx.worktree), "expected_digest": kept.digest()}

        async def run() -> None:
            kept = self._hidden(ctx.worktree)
            _wipe(ctx.worktree, frozenset(kept))
            for path in build_manifest(source, exclude={AGENT_DIR}).entries:
                _refuse_links_on_the_way(ctx.worktree, path)
            shutil.copytree(
                source,
                ctx.worktree,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(AGENT_DIR, ".git"),
            )
            if self._hidden(ctx.worktree) != kept:
                raise RuntimeError("a file that was to stay untouched changed on the way back")

        return Step(payload, run)

    def declared_tests(self, ctx: AttemptContext) -> tuple[DeclaredTest, ...]:
        """The tests the agent says it wrote; the work copy outlives the attempt's judging."""
        return _declared(workcopy_of(ctx)).tests

    def declared_claims(self, ctx: AttemptContext) -> tuple[Claim, ...]:
        """The statements, each with its citations, the agent says are true of the change."""
        return _declared(workcopy_of(ctx)).claims

    def declared_output(self, ctx: AttemptContext) -> LlmOutput:
        """Everything the agent declared, for the roles whose answer holds more than files."""
        return _declared(workcopy_of(ctx))

    def redact_diary(self, diary: str) -> str:
        """The diary as the branch keeps it: through this run's redactor, like the agent's copy."""
        return redacted_diary(diary, self._redactor)

    async def reconcile(self, operation: OpenOperation) -> Reconciliation:
        p: Any = operation.payload
        if operation.operation == "prepare_workcopy":
            return reconcile_prepare_workcopy(
                Path(p["workcopy"]), p["expected_digest"], exclude={AGENT_DIR}
            )
        return reconcile_apply_diff(Path(p["worktree"]), p["expected_digest"])

    # -- what the agent may not see or touch ------------------------------------------------------

    def _hidden(self, worktree: Path) -> dict[str, ManifestEntry]:
        """The sensitive files: they stay out of the copy and come back untouched."""
        entries = build_manifest(worktree, exclude={".git"}).entries
        return {path: e for path, e in entries.items() if self._is_hidden(path, e)}

    def _is_hidden(self, path: str, entry: ManifestEntry) -> bool:
        # a link may point at anything, so the agent gets neither it nor what it points at
        return entry.kind is EntryKind.SYMLINK or self._sensitive.matches(plain(path))

    def _visible(self, manifest: Manifest) -> Manifest:
        return Manifest({p: e for p, e in manifest.entries.items() if not self._is_hidden(p, e)})

    def _left_out(self, worktree: Path) -> Callable[[str, list[str]], set[str]]:
        def ignore(directory: str, names: list[str]) -> set[str]:
            relative = Path(directory).relative_to(worktree).as_posix()
            prefix = "" if relative == "." else f"{relative}/"
            return {
                name
                for name in names
                if (not prefix and name == ".git")
                or (Path(directory) / name).is_symlink()
                or (Path(directory) / name).is_junction()
                or self._sensitive.matches(plain(f"{prefix}{name}"))
            }

        return ignore

    def _refuse_off_limits(self, after: Manifest, changed: ManifestDiff) -> None:
        """Git's own files and the sensitive ones are not the agent's to write, whatever it says.

        The copy holds none of the sensitive files, so any it holds the agent made. Names are read
        the way Windows reads them, where ``.env.`` and ``.env `` are ``.env``, and a short name
        (``ENV~1``) can stand for a long one, so no new name may look like one.
        """
        for path in after.entries:
            if touches_git(path):
                raise RejectedWork(f"{path}: the work reaches into git's own directory")
            if self._sensitive.matches(plain(path)):
                raise RejectedWork(f"{path}: the work creates or changes a sensitive file")
        for path in (*changed.created, *changed.modified):
            if any(_SHORT_NAME.search(part) for part in path.split("/")):
                raise RejectedWork(f"{path}: a name with a short-name marker (~digit) is refused")

    def _refuse_collisions(self, after: Manifest, hidden: Mapping[str, ManifestEntry]) -> None:
        """Nothing may be written at a hidden path or below it: below a link it would go through."""
        taken = {plain(path).casefold() for path in hidden}
        for path in after.entries:
            parts = plain(path).casefold().split("/")
            if any("/".join(parts[: i + 1]) in taken for i in range(len(parts))):
                raise RejectedWork(f"{path}: the work goes where it may not write")

    def _refuse_tampering(self, source: Path, ctx: AttemptContext) -> None:
        """The agent writes in its own directory; neither its permissions nor the diary are its."""
        setup = self._setup()
        for name, data in setup.files.items():
            kept = source / AGENT_DIR / name
            if not kept.is_file() or kept.read_bytes() != data:
                raise RejectedWork(f"{AGENT_DIR}/{name}: the agent changed its own permissions")
        if ctx.diary:
            kept = source / AGENT_DIR / DIARY_FILE
            expected = diary_document(ctx, self._redactor).encode("utf-8")
            if not kept.is_file() or kept.read_bytes() != expected:
                raise RejectedWork(f"{AGENT_DIR}/{DIARY_FILE}: the agent changed the diary")


def _declared(workcopy: Path) -> LlmOutput:
    """What the agent said it did; a missing or malformed declaration rejects the work."""
    path = workcopy / AGENT_DIR / OUTPUT
    if not path.is_file():
        raise RejectedWork(f"{AGENT_DIR}/{OUTPUT} was not written")
    try:
        return parse_llm_output(path.read_bytes())
    except InvalidLlmOutput as error:
        raise RejectedWork(
            f"{AGENT_DIR}/{OUTPUT} is not valid: " + "; ".join(error.problems)
        ) from None


def _check_manifest(output: LlmOutput) -> None:
    """The test author's answer must make a manifest: one test at least, none twice."""
    try:
        manifest_of(output)
    except ValidationError as error:
        problems = "; ".join(item["msg"] for item in error.errors())
        raise RejectedWork(f"the declared tests are not valid: {problems}") from None


def _check_findings(output: LlmOutput) -> None:
    """The archaeologist must have found something: a claim or an assumption. Nothing is not an
    answer to a question."""
    if not output.claims and not output.assumptions:
        raise RejectedWork("the answer holds no claim and no assumption: nothing was found")


def _check_criteria(output: LlmOutput) -> None:
    """The specifier's answer must hold criteria that make a body: one at least, no id twice."""
    try:
        CriteriaBody(criteria=output.criteria)
    except ValidationError as error:
        problems = "; ".join(item["msg"] for item in error.errors())
        raise RejectedWork(f"the criteria are not valid: {problems}") from None


def _reason(workcopy: Path) -> str:
    try:
        return " ".join(action.reason for action in _declared(workcopy).actions)
    except RejectedWork:
        return ""  # apply_diff rejects the work with the details
