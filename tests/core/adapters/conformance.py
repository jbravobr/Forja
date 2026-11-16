"""What every adapter must satisfy, as a reusable check that lists what is wrong.

Real adapters (Python, Claude Code...) are run through the same functions when they exist, so the
promises of the interface are tested once and applied to all of them.
"""

import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

from forja.core.adapters import (
    CAPABILITY_OPERATION,
    CONTRACT_VERSION,
    REQUIRED_OPERATIONS,
    AgentRequest,
    CommandActivity,
    CommandPlan,
    ErrorActivity,
    ExecutorAdapter,
    FileClass,
    FileEditActivity,
    FileReadActivity,
    LanguageAdapter,
    LanguageCapability,
    NotSupported,
    Operation,
    OperationRequest,
    Parsed,
    PermissionSetup,
    Progress,
    RolePermissions,
    TextActivity,
    ToolInfo,
    ToolOutput,
    Unparseable,
    UsageActivity,
)
from forja.core.artifacts.models import Producer
from forja.core.policy.registry import LANGUAGES
from forja.core.process import UnsafeCommand, check_command_safety
from forja.core.reports import ReportSpec, formats_of

SHELLS = {"sh", "bash", "zsh", "dash", "cmd", "powershell", "pwsh"}
ACTIVITY_TYPES = (
    TextActivity,
    FileReadActivity,
    FileEditActivity,
    CommandActivity,
    UsageActivity,
    ErrorActivity,
)


def _garbage_texts() -> list[str]:
    rng = random.Random(1234)
    blobs = [
        rng.randbytes(rng.randint(1, 200)).decode("utf-8", errors="replace") for _ in range(20)
    ]
    return [
        "",
        " ",
        "{",
        "}",
        "null",
        "[]",
        '{"type": null}',
        '{"type": "edit", "added": "many", "removed": -4}',
        "9" * 5000,
        "RESULT passed=" + "9" * 5000 + " failed=1",
        "[" + "9" * 5000 + "/" + "9" * 5000 + "]",
        "COVERAGE " + "9" * 400,
        chr(0) + chr(1) + chr(2),
        "é" * 3000,
        "x" * 200_000,
        "[0/0]",
        "[5/3]",
        "[out] [err] [out]",
        *blobs,
    ]


def _attempt(problems: list[str], label: str, call: Callable[..., Any], *args: Any) -> Any:
    """Call ``call(*args)``; an exception becomes a problem instead of aborting the whole check."""
    try:
        return call(*args)
    except Exception as error:
        problems.append(f"{label} raised {type(error).__name__}: {error}")
        return None


def _command_problems(label: str, plan: CommandPlan) -> list[str]:
    problems = []
    try:
        check_command_safety(plan.argv)
    except UnsafeCommand as error:
        problems.append(f"{label}: unsafe command: {error}")
    program = Path(plan.argv[0]).stem.lower()
    if program in SHELLS:
        problems.append(f"{label}: runs through a shell ({program}); give the program directly")
    if any(ch in plan.argv[0] for ch in "&|;<>"):
        problems.append(f"{label}: the program name looks like shell text")
    return problems


def language_problems(
    adapter: LanguageAdapter, project: Path, scratch: Path, test_id: str = "test_a::case"
) -> list[str]:
    """Everything wrong with a language adapter, judged on a project with its manifest present.
    ``test_id`` is the id of a test as the reports of the language give it: an adapter that
    filters by test is asked for that one."""
    problems: list[str] = []
    if adapter.contract_version != CONTRACT_VERSION:
        problems.append(f"contract version {adapter.contract_version}, expected {CONTRACT_VERSION}")
    if adapter.language not in LANGUAGES:
        problems.append(f"{adapter.language!r} is not a supported language")
    if not all(isinstance(c, LanguageCapability) for c in adapter.capabilities):
        problems.append("capabilities are not LanguageCapability values")
    if adapter.grammar is not None and not isinstance(adapter.grammar, str):
        problems.append("grammar must be a string or None")

    plain = OperationRequest(root=project)
    plans: dict[Operation, CommandPlan | NotSupported] = {}
    for operation in Operation:
        label = f"plan({operation.value})"
        plan = _attempt(problems, label, adapter.plan, operation, plain)
        if plan is None:
            continue
        plans[operation] = plan
        if not isinstance(plan, CommandPlan | NotSupported):
            problems.append(f"{label} must return a CommandPlan or NotSupported")
            continue
        if _attempt(problems, label, adapter.plan, operation, plain) != plan:
            problems.append(f"{label} is not deterministic")
        if isinstance(plan, CommandPlan):
            problems += _command_problems(label, plan)
            info = _attempt(problems, f"tool_info({operation.value})", adapter.tool_info, operation)
            if not isinstance(info, ToolInfo):
                problems.append(f"tool_info({operation.value}) must describe a planned operation")
        if operation in REQUIRED_OPERATIONS and isinstance(plan, NotSupported):
            problems.append(f"{operation.value} is required but not supported")

    for capability, operation in CAPABILITY_OPERATION.items():
        planned = isinstance(plans.get(operation), CommandPlan)
        if capability in adapter.capabilities and not planned:
            problems.append(f"declares {capability.value} but {operation.value} cannot be planned")
        if capability not in adapter.capabilities and planned:
            problems.append(f"plans {operation.value} without declaring {capability.value}")

    problems += _filter_problems(adapter, project, test_id)
    problems += _report_problems(adapter)

    for text in _garbage_texts():
        path = text[:300]
        first = _attempt(problems, "classify", adapter.classify, path)
        if first is not None and not isinstance(first, FileClass):
            problems.append("classify must return a FileClass or None")
        if _attempt(problems, "classify", adapter.classify, path) != first:
            problems.append("classify is not deterministic")
    for unix, windows in (("tests/test_a.py", "tests\\test_a.py"), ("src/a.py", "src\\a.py")):
        as_unix = _attempt(problems, "classify", adapter.classify, unix)
        as_windows = _attempt(problems, "classify", adapter.classify, windows)
        if as_unix != as_windows:
            problems.append(f"classify treats {unix!r} and {windows!r} differently")

    problems += _parse_problems(adapter, scratch)
    problems += _progress_problems(adapter)

    detection = _attempt(problems, "detect", adapter.detect, project)
    if detection is not None and detection.language != adapter.language:
        problems.append("detect found a different language than the adapter's own")
    if _attempt(problems, "detect(empty)", adapter.detect, scratch / "nothing-here") is not None:
        problems.append("detect must answer None where there is no project")
    return problems


def _filter_problems(adapter: LanguageAdapter, project: Path, test_id: str) -> list[str]:
    problems: list[str] = []
    plain = adapter.plan(Operation.TEST, OperationRequest(root=project))
    by_file = adapter.plan(
        Operation.TEST, OperationRequest(root=project, test_files=("tests/test_a.py",))
    )
    by_test = adapter.plan(Operation.TEST, OperationRequest(root=project, tests=(test_id,)))
    for capability, answer in (
        (LanguageCapability.TEST_FILTER_BY_FILE, by_file),
        (LanguageCapability.TEST_FILTER_BY_TEST, by_test),
    ):
        if capability in adapter.capabilities:
            if not isinstance(answer, CommandPlan):
                problems.append(f"declares {capability.value} but the filtered plan is {answer}")
            elif answer == plain:
                problems.append(f"{capability.value}: the filter does not change the command")
        elif not isinstance(answer, NotSupported):
            problems.append(f"without {capability.value} a filtered request must be NotSupported")
    incremental = adapter.plan(
        Operation.MUTATION,
        OperationRequest(root=project, incremental=True, changed_files=("src/a.py",)),
    )
    if LanguageCapability.MUTATION_INCREMENTAL in adapter.capabilities:
        if not isinstance(incremental, CommandPlan):
            problems.append("declares mutation_incremental but cannot plan it")
    elif not isinstance(incremental, NotSupported):
        problems.append("without mutation_incremental an incremental request must be NotSupported")
    return problems


def _report_problems(adapter: LanguageAdapter) -> list[str]:
    """Coverage and mutation write a report; the adapter says which, and only if it runs them."""
    problems: list[str] = []
    report = getattr(adapter, "report", None)
    for capability, operation in CAPABILITY_OPERATION.items():
        if not formats_of(operation):
            continue
        declared = capability in adapter.capabilities
        if report is None:
            if declared:
                problems.append(
                    f"declares {capability.value} but has no report() to say what it writes"
                )
            continue
        label = f"report({operation.value})"
        answer = _attempt(problems, label, report, operation)
        if answer is None:
            continue
        if _attempt(problems, label, report, operation) != answer:
            problems.append(f"{label} is not deterministic")
        if declared:
            if not isinstance(answer, ReportSpec):
                problems.append(f"{label} must describe the report of a declared capability")
            elif answer.format not in formats_of(operation):
                problems.append(f"{label}: {answer.format.value} is not a {operation.value} format")
        elif not isinstance(answer, NotSupported):
            problems.append(f"without {capability.value}, {label} must be NotSupported")
    return problems


def _parse_problems(adapter: LanguageAdapter, scratch: Path) -> list[str]:
    problems: list[str] = []
    scratch.mkdir(parents=True, exist_ok=True)
    logs = []
    for index, text in enumerate(_garbage_texts()):
        log = scratch / f"garbage-{index}.log"
        log.write_bytes(("[out] " + text).encode("utf-8", errors="replace"))
        logs.append(log)
    logs.append(scratch / "missing.log")
    binary = scratch / "binary.log"
    binary.write_bytes(bytes(range(256)) * 20)
    logs.append(binary)
    for operation in Operation:
        for log in logs:
            for code in (None, 0, 1, -9):
                output = ToolOutput(code, log)
                label = f"parse({operation.value}, {log.name}, {code})"
                result = _attempt(problems, label, adapter.parse, operation, output)
                if result is None:
                    return problems  # one report is enough; the rest would repeat it
                if not isinstance(result, Parsed | Unparseable):
                    problems.append(f"{label} must return Parsed or Unparseable")
                elif adapter.parse(operation, output) != result:
                    problems.append(f"{label} is not deterministic")
    return problems


def _progress_problems(adapter: LanguageAdapter) -> list[str]:
    problems: list[str] = []
    declared = LanguageCapability.PROGRESS_REALTIME in adapter.capabilities
    for operation in Operation:
        label = f"progress_parser({operation.value})"
        parser = _attempt(problems, label, adapter.progress_parser, operation)
        if parser is None:
            continue
        if not declared:
            problems.append(f"offers progress for {operation.value} without progress_realtime")
        for text in _garbage_texts():
            answer = _attempt(problems, "progress feed", parser.feed, text)
            if answer is not None and not isinstance(answer, Progress):
                problems.append("progress feed must return Progress or None")
            elif answer is not None and not (0 <= answer.done <= answer.total and answer.total > 0):
                problems.append(f"progress {answer.done}/{answer.total} is not a measured fraction")
    return problems


def executor_problems(adapter: ExecutorAdapter, scratch: Path) -> list[str]:
    """Everything wrong with an executor adapter."""
    problems: list[str] = []
    if adapter.contract_version != CONTRACT_VERSION:
        problems.append(f"contract version {adapter.contract_version}, expected {CONTRACT_VERSION}")
    if not adapter.name:
        problems.append("the adapter has no name")
    if any("=" in name or not name for name in adapter.env_allow):
        problems.append("env_allow holds an invalid variable name")
    if not adapter.probe.expected_file or not adapter.probe.expected_content:
        problems.append("the proof of life must expect a file and its content")

    scratch.mkdir(parents=True, exist_ok=True)
    marker = "PACKAGE-MARKER-" + "x" * 20_000
    package = scratch / "package.md"
    package.write_text(marker, encoding="utf-8")
    workdir = scratch / "copy"
    workdir.mkdir(exist_ok=True)
    executable = scratch / "agent-cli"

    for role in (r for r in Producer if r is not Producer.FORJA):
        for permissions in (
            RolePermissions(can_edit_files=False),
            RolePermissions(can_edit_files=True, allowed_commands=("forja agente testar",)),
        ):
            label = f"{role.value}/{permissions.can_edit_files}"
            setup = _attempt(
                problems, f"permission_setup({label})", adapter.permission_setup, role, permissions
            )
            if setup is None:
                continue
            if not isinstance(setup, PermissionSetup):
                problems.append(f"permission_setup({label}) must return a PermissionSetup")
                continue
            if adapter.permission_setup(role, permissions) != setup:
                problems.append(f"permission_setup({label}) is not deterministic")
            request = AgentRequest(role, package, workdir, permissions)
            name = f"plan_invocation({label})"
            plan = _attempt(problems, name, adapter.plan_invocation, request, setup, executable)
            if plan is None:
                continue
            problems += _command_problems(name, plan)
            if any(marker[:100] in part for part in plan.argv):
                problems.append(f"{name} puts the package in an argument")
            if not (str(package) in " ".join(plan.argv) or plan.stdin_file == package):
                problems.append(f"{name} does not deliver the package")
            if plan.cwd != workdir:
                problems.append(f"{name} does not run in the working copy")
            if adapter.plan_invocation(request, setup, executable) != plan:
                problems.append(f"{name} is not deterministic")

    for text in _garbage_texts():
        activity = _attempt(problems, "parse_event", adapter.parse_event, text)
        if activity is not None and not isinstance(activity, ACTIVITY_TYPES):
            problems.append("parse_event must return an activity or None")
        if _attempt(problems, "parse_event", adapter.parse_event, text) != activity:
            problems.append("parse_event is not deterministic")
    return problems
