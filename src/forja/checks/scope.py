"""Gate 1: the attempt touched only what the task allowed, and not too much of it."""

import functools
import re
from collections.abc import Callable
from dataclasses import dataclass

from forja.checks.base import rejected
from forja.checks.change_record import CHANGES_DIR, diary_path, record_path
from forja.checks.changes import Change, Changes, added_lines
from forja.core.adapters import FileClass
from forja.core.artifacts.models import Producer
from forja.core.gates import Approved, Finding, GateInput, GateResult
from forja.core.task import TaskInfo

_ANY_LANGUAGE = (
    r"noqa|fmt:\s*(?:off|on|skip)|yapf:\s*(?:disable|enable)|gitleaks:allow"
    r"|lizard\s+forgive|#\s*lizard|generated\s+code|nosem"
    r"|pragma:\s*no\s*\w+|type:\s*ignore|(?:mypy|pyright):\s*(?:ignore|basic|off)"
    r"|pylint:\s*(?:disable|skip)|nosec"
)
SUPPRESSIONS = re.compile(_ANY_LANGUAGE, re.IGNORECASE)


@functools.cache
def _with(own: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile("|".join((f"(?i:{_ANY_LANGUAGE})", *own))) if own else SUPPRESSIONS


def silencers(owner: Callable[[str], object | None]) -> Callable[[str], re.Pattern[str]]:
    """What switches a tool off in each file: the markers looked for everywhere, plus those the
    language that owns the file declares in ``suppressions``. The markers of a language are only
    looked for in its own files, as they are written: ``.Skip(`` skips a test in Go and takes
    the rest of a sequence in C#."""

    def of(path: str) -> re.Pattern[str]:
        return _with(tuple(getattr(owner(path), "suppressions", ())))

    return of


# Directories the tools write their caches to. The compiled files of Python are not here: the
# adapter sends them outside the worktree, so any that turn up inside it are suspect.
TOOL_CACHES = frozenset({".pytest_cache", ".ruff_cache", ".mypy_cache", ".hypothesis"})
TEST_AUTHOR_CLASSES = frozenset({FileClass.TEST, FileClass.FIXTURE})
CODE_SUFFIXES = (".py", ".pyi", ".pyw", ".pyz", ".pth", ".so", ".pyd", ".dll", ".zip")
COMPILED_SUFFIXES = (".pyc", ".pyo")
# What the interpreter names a cache file (PEP 3147): module.cpython-312.pyc, never importable
# as a module because of the dots in the name.
CACHE_NAME = re.compile(r"[^.]+\.[a-z0-9]+-\d+(?:\.opt-[12])?\.py[co]")


def disposable(path: str) -> bool:
    """Whether an ignored file is only a tool's cache and cannot be run or imported."""
    parts = path.replace("\\", "/").split("/")
    name, directories = parts[-1].lower(), parts[:-1]
    if name.endswith(COMPILED_SUFFIXES):
        return (
            bool(directories)
            and directories[-1] == "__pycache__"
            and bool(CACHE_NAME.fullmatch(name))
        )
    if name.endswith(CODE_SUFFIXES):
        return False
    return any(directory in TOOL_CACHES for directory in directories)


def _same_file(path: str) -> str:
    """The path as a file system that ignores case, dots and spaces at the end of a name reads it,
    so two spellings of one file compare equal."""
    parts = (part.rstrip(" .").casefold() for part in path.replace("\\", "/").split("/"))
    return "/".join(part for part in parts if part)


def in_scope(path: str, scope: tuple[str, ...]) -> bool:
    """Whether ``path`` is one of the scope entries or lies under one that is a directory."""
    normal = path.replace("\\", "/")
    for entry in scope:
        wanted = entry.replace("\\", "/").rstrip("/")
        if normal == wanted or normal.startswith(wanted + "/"):
            return True
    return False


def _limit(task_value: int, data: GateInput, name: str) -> int:
    """The stricter of the task's limit and the policy's; zero means neither set one."""
    limits = data.values.get("limits")
    from_policy = limits.get(name) if isinstance(limits, dict) else None
    policy = (
        from_policy if isinstance(from_policy, int) and not isinstance(from_policy, bool) else 0
    )
    chosen = [v for v in (task_value, policy) if v > 0]
    return min(chosen) if chosen else 0


@dataclass(frozen=True)
class ScopeCheck:
    """The implementer may change only production files in the task's scope; tests stay frozen.

    The dependencies a manifest declares are the one other thing the implementer and the test
    author may change; the implementer only in a manifest inside the task scope, the test author
    in any manifest (the library of its tests is declared at the root). The dependency it adds is
    examined by gate 10, and the Forja installs it only after that.
    The rest of the manifest stays out of reach."""

    changes: Changes
    task: TaskInfo
    classify: Callable[[str], FileClass]
    declared: tuple[str, ...] = ()  # what the agent said it changed; empty means not asked
    dependency_edit: Callable[[Change], bool] = lambda change: False  # touches only dependencies
    silencers: Callable[[str], re.Pattern[str]] = lambda path: SUPPRESSIONS  # by file
    id: str = "scope"
    gate: int = 1
    requires: frozenset[str] = frozenset()

    async def evaluate(self, data: GateInput) -> GateResult:
        changes = self.changes
        if not changes.files:
            if self.task.role in (Producer.SPECIFIER, Producer.ARCHAEOLOGIST):  # no file is right
                return Approved()
            return rejected([Finding(message="the attempt changed nothing", target="diff")])
        findings = [
            *self._forbidden(),
            *self._hidden(),
            *self._suppressions(),
            *self._too_big(data),
            *self._undeclared(),
        ]
        return rejected(findings) if findings else Approved()

    def _forbidden(self) -> list[Finding]:
        findings = []
        for change in self.changes.files:
            edit = self.dependency_edit(change)
            for path in (change.old_path, change.path):
                if path is None:
                    continue
                finding = self._dependencies(path) if edit else self._by_role(path)
                if finding:
                    findings.append(finding)
        return findings

    def _dependencies(self, path: str) -> Finding | None:
        match self.task.role:
            case Producer.IMPLEMENTER:
                if self.task.scope and not in_scope(path, self.task.scope):
                    return Finding(message="outside the task scope", target=path)
            case Producer.TEST_AUTHOR:
                pass  # its library is declared at the root, outside the test directory
            case _:
                return self._by_role(path)
        return None  # a new dependency is the agent's to declare; gate 10 examines it

    def _by_role(self, path: str) -> Finding | None:
        match self.task.role:
            case Producer.TEST_AUTHOR:
                return self._author(path)
            case Producer.DOCUMENTER:
                return self._documenter(path)
            case Producer.SPECIFIER:
                return Finding(message="the specifier writes criteria, not files", target=path)
            case Producer.ARCHAEOLOGIST:
                return Finding(message="the archaeologist writes findings, not files", target=path)
        return self._implementer(path)

    def _documenter(self, path: str) -> Finding | None:
        kind = self.classify(path)
        if kind is not FileClass.DOCUMENTATION:
            message = f"the documenter may change only documentation (this is {kind.value})"
            return Finding(message=message, target=path)
        key = _same_file(path)
        own = {
            _same_file(record_path(self.task.task_id)),
            _same_file(diary_path(self.task.task_id)),
        }
        if key.startswith(f"{_same_file(CHANGES_DIR)}/") and key not in own:
            return Finding(
                message="a change record is the Forja's, not the documenter's", target=path
            )
        return None

    def _implementer(self, path: str) -> Finding | None:
        kind = self.classify(path)
        if kind is not FileClass.PRODUCTION:
            message = f"only production files may change in this stage (this is {kind.value})"
            return Finding(message=message, target=path)
        if self.task.scope and not in_scope(path, self.task.scope):
            return Finding(message="outside the task scope", target=path)
        return None

    def _author(self, path: str) -> Finding | None:
        kind = self.classify(path)
        if kind not in TEST_AUTHOR_CLASSES:
            message = f"the test author may change only tests and fixtures (this is {kind.value})"
            return Finding(message=message, target=path)
        if self.task.test_root and not in_scope(path, (self.task.test_root,)):
            return Finding(message="outside the test directory", target=path)
        return None

    def _hidden(self) -> list[Finding]:
        """Files the ignore rules hide from the commit but not from the build and tests."""
        return [
            Finding(message="a file git ignores was added", target=path)
            for path in self.changes.ignored
            if not disposable(path)
        ]

    def _suppressions(self) -> list[Finding]:
        """A comment that tells a tool to look away is the tool being switched off."""
        return [
            Finding(
                message="a comment that silences a tool was added",
                target=line.path or "diff",
            )
            for line in added_lines(self.changes.patch)
            if self.silencers(line.path or "").search(line.text)
        ]

    def _too_big(self, data: GateInput) -> list[Finding]:
        findings: list[Finding] = []
        if self.task.role is Producer.DOCUMENTER:  # the limits are for the code of the task
            return findings
        files = len(self.changes.files)
        max_files = _limit(self.task.max_files, data, "max_files")
        if max_files and files > max_files:
            message = "more files changed than the task allows"
            findings.append(
                Finding(message=message, target="diff", measured=files, limit=max_files)
            )
        lines = self.changes.lines
        max_lines = _limit(self.task.max_lines, data, "max_lines")
        if max_lines and lines > max_lines:
            message = "more lines changed than the task allows"
            findings.append(
                Finding(message=message, target="diff", measured=lines, limit=max_lines)
            )
        return findings

    def _undeclared(self) -> list[Finding]:
        if not self.declared:
            return []
        changed = {c.path for c in self.changes.files}
        declared = {path.replace("\\", "/") for path in self.declared}
        if changed == declared:
            return []
        missing, extra = sorted(declared - changed), sorted(changed - declared)
        detail = "; ".join(
            part
            for part in (
                f"declared but not changed: {', '.join(missing)}" if missing else "",
                f"changed but not declared: {', '.join(extra)}" if extra else "",
            )
            if part
        )
        return [
            Finding(
                message=f"the declared files differ from the changed ones ({detail})", target="diff"
            )
        ]
