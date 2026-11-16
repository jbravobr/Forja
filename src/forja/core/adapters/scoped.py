"""A language adapter that works on one project of a repository: the directory of its manifest.

The adapters assume the manifest is at the root they are given. This wrapper keeps that true:
the inner adapter sees ``worktree/base`` as the root and paths relative to it, and everyone else
sees paths relative to the worktree. With ``base == "."`` nothing changes.
"""

import dataclasses
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from forja.core.adapters.common import (
    CommandPlan,
    EvidenceDraft,
    NotSupported,
    Parsed,
    ParseResult,
    ProgressParser,
    ToolInfo,
    ToolOutput,
)
from forja.core.adapters.language import (
    Detection,
    FileClass,
    LanguageAdapter,
    LanguageCapability,
    Operation,
    OperationRequest,
    ProjectTemplate,
)
from forja.core.evidence.models import Target, TargetKind

if TYPE_CHECKING:  # forja.core.reports imports the adapters' common types
    from forja.core.reports.formats import ReportSpec

# Where a test id starts with the test file's path, and what follows the path. The other
# languages name a test without its file.
_TEST_ID_SEPARATOR = {"python": "::", "typescript": " > "}


class ScopedAdapter:
    def __init__(self, adapter: LanguageAdapter, base: str = ".") -> None:
        self._adapter = adapter
        self.base = PurePosixPath(base).as_posix()
        # only some adapters answer this; the check asks with getattr, so absent must stay absent
        if callable(getattr(adapter, "owns_test", None)):
            self.owns_test = self._owns_test

    # -- what the adapter is -------------------------------------------------------------------

    @property
    def contract_version(self) -> int:
        return self._adapter.contract_version

    @property
    def language(self) -> str:
        return self._adapter.language

    @property
    def capabilities(self) -> frozenset[LanguageCapability]:
        return self._adapter.capabilities

    @property
    def grammar(self) -> str | None:
        return self._adapter.grammar

    @property
    def inner(self) -> LanguageAdapter:
        return self._adapter

    # -- paths ---------------------------------------------------------------------------------

    def holds(self, path: str) -> bool:
        """Whether the file is in this project's directory."""
        return self.base == "." or path.replace("\\", "/").startswith(f"{self.base}/")

    def local(self, path: str) -> str:
        """The path relative to the project's directory (the worktree's, for ``path`` outside)."""
        text = path.replace("\\", "/")
        return text[len(self.base) + 1 :] if self.base != "." and self.holds(text) else text

    def repository(self, path: str) -> str:
        """The path relative to the worktree, from one relative to the project's directory."""
        return path if self.base == "." else f"{self.base}/{path}"

    def directory(self, worktree: Path) -> Path:
        return worktree if self.base == "." else worktree / self.base

    # -- finding and classifying ---------------------------------------------------------------

    def detect(self, root: Path) -> Detection | None:
        found = self._adapter.detect(self.directory(root))
        return None if found is None else dataclasses.replace(found, roots=(self.base,))

    def classify(self, path: str) -> FileClass | None:
        return self._adapter.classify(self.local(path)) if self.holds(path) else None

    # -- commands ------------------------------------------------------------------------------

    def tool_info(self, operation: Operation) -> ToolInfo | NotSupported:
        return self._adapter.tool_info(operation)

    def plan(self, operation: Operation, request: OperationRequest) -> CommandPlan | NotSupported:
        given = {
            "files": request.files,
            "test_files": request.test_files,
            "changed_files": request.changed_files,
        }
        kept = {name: self._inside(paths) for name, paths in given.items()}
        for name, paths in given.items():
            if paths and not kept[name]:
                return NotSupported(operation.value, f"no {name} of the project {self.base!r}")
        local = dataclasses.replace(
            request,
            root=self.directory(request.root),
            files=kept["files"],
            test_files=kept["test_files"],
            changed_files=kept["changed_files"],
            mutated=None if request.mutated is None else self.local(request.mutated),
        )
        plan = self._adapter.plan(operation, local)
        if isinstance(plan, NotSupported):
            return plan
        return dataclasses.replace(
            plan, config_files=tuple(self.repository(path) for path in plan.config_files)
        )

    def _inside(self, paths: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(self.local(path) for path in paths if self.holds(path))

    def progress_parser(self, operation: Operation) -> ProgressParser | None:
        return self._adapter.progress_parser(operation)

    def template(self, name: str) -> ProjectTemplate:
        return self._adapter.template(name)

    # -- reading what they print ---------------------------------------------------------------

    def parse(self, operation: Operation, output: ToolOutput) -> ParseResult:
        result = self._adapter.parse(operation, output)
        if self.base == "." or not isinstance(result, Parsed):
            return result
        return Parsed(tuple(self._draft(draft) for draft in result.drafts))

    def _draft(self, draft: EvidenceDraft) -> EvidenceDraft:
        target = draft.target
        if target.kind is TargetKind.FILE:
            return self._retarget(draft, self.repository(target.id))
        separator = _TEST_ID_SEPARATOR.get(self.language)
        if target.kind is TargetKind.SYMBOL and separator and separator in target.id:
            return self._retarget(draft, self.repository(target.id))
        return draft

    @staticmethod
    def _retarget(draft: EvidenceDraft, identifier: str) -> EvidenceDraft:
        return dataclasses.replace(draft, target=Target(kind=draft.target.kind, id=identifier))

    def report(self, operation: Operation) -> "ReportSpec | NotSupported":
        """What the inner adapter writes, with the paths relative to the worktree. The paths
        inside the report stay relative to the project: read them with ``directory`` as root and
        put ``repository`` on each."""
        report = getattr(self._adapter, "report", None)
        if not callable(report):
            return NotSupported(operation.value, "the adapter offers no report")
        spec: ReportSpec | NotSupported = report(operation)
        if isinstance(spec, NotSupported) or self.base == ".":
            return spec
        return dataclasses.replace(
            spec,
            path=self.repository(spec.path),
            source_roots=tuple(self.repository(root) for root in spec.source_roots),
            index=None if spec.index is None else self.repository(spec.index),
        )

    def _owns_test(self, test_id: str, path: str, text: str) -> bool:
        owns = self._adapter.owns_test  # type: ignore[attr-defined]
        separator = _TEST_ID_SEPARATOR.get(self.language)
        local_id = self.local(test_id) if separator and separator in test_id else test_id
        return bool(owns(local_id, self.local(path), text))
