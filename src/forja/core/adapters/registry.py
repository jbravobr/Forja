from collections.abc import Callable, Iterator
from pathlib import Path

from forja.core.adapters.common import CONTRACT_VERSION
from forja.core.adapters.executor import ExecutorAdapter
from forja.core.adapters.language import (
    Detection,
    FileClass,
    LanguageAdapter,
    LanguageCapability,
    is_documentation,
    valid_language,
)
from forja.core.adapters.projects import find_projects
from forja.core.adapters.scoped import ScopedAdapter


class AdapterRejected(ValueError):
    """The adapter cannot be registered; the message says why."""


class AdapterRegistry:
    """The adapters Forja knows. Built in only: nothing is loaded from the environment."""

    def __init__(self) -> None:
        self._languages: dict[str, LanguageAdapter] = {}
        self._factories: dict[str, Callable[[], LanguageAdapter]] = {}
        self._executors: dict[str, ExecutorAdapter] = {}

    def register_language(
        self, adapter: LanguageAdapter, factory: Callable[[], LanguageAdapter] | None = None
    ) -> None:
        """``factory`` makes the instance of each project; without it the project shares
        ``adapter``, which is only right for an adapter that keeps no state between calls."""
        if adapter.contract_version != CONTRACT_VERSION:
            raise AdapterRejected(
                f"language adapter {adapter.language!r} speaks contract "
                f"{adapter.contract_version}; Forja speaks {CONTRACT_VERSION}"
            )
        if not valid_language(adapter.language):
            raise AdapterRejected(f"{adapter.language!r} is not a supported language")
        if adapter.language in self._languages:
            raise AdapterRejected(f"a {adapter.language!r} adapter is already registered")
        if not all(isinstance(c, LanguageCapability) for c in adapter.capabilities):
            raise AdapterRejected("capabilities must be LanguageCapability values")
        self._languages[adapter.language] = adapter
        self._factories[adapter.language] = factory or (lambda: adapter)

    def register_executor(self, adapter: ExecutorAdapter) -> None:
        if adapter.contract_version != CONTRACT_VERSION:
            raise AdapterRejected(
                f"executor adapter {adapter.name!r} speaks contract "
                f"{adapter.contract_version}; Forja speaks {CONTRACT_VERSION}"
            )
        if not adapter.name or adapter.name in self._executors:
            raise AdapterRejected(f"executor name {adapter.name!r} is empty or already registered")
        self._executors[adapter.name] = adapter

    def language(self, name: str) -> LanguageAdapter:
        return self._languages[name]

    def executor(self, name: str) -> ExecutorAdapter:
        return self._executors[name]

    def languages(self) -> Iterator[LanguageAdapter]:
        return iter(self._languages.values())

    def executors(self) -> Iterator[ExecutorAdapter]:
        return iter(self._executors.values())

    def detect_all(self, root: Path) -> list[tuple[LanguageAdapter, Detection]]:
        """Every project found under ``root``, in registration order, one entry per manifest
        directory."""
        return [
            (adapter, detection)
            for adapter in self._languages.values()
            for detection in find_projects(adapter, root)
        ]

    def projects(self, root: Path) -> list[ScopedAdapter]:
        """The projects under ``root``, one adapter instance each, in registration order and
        then by directory. Each instance has seen the manifest of its own project: an adapter
        may learn there what it needs to read the output of the tools (which build tool the
        project uses), and the instance that found the project is not the one that works on it."""
        found = []
        for adapter in self._languages.values():
            for detection in find_projects(adapter, root):
                project = self.project(adapter.language, detection.roots[0])
                project.detect(root)
                found.append(project)
        return found

    def project(self, language: str, base: str = ".") -> ScopedAdapter:
        """A project of ``language`` at ``base``, with an instance of its own."""
        return ScopedAdapter(self._factories[language](), base)

    def for_file(self, path: str) -> LanguageAdapter | None:
        """The first adapter that claims the file, or ``None`` when nobody does."""
        for adapter in self._languages.values():
            if adapter.classify(path) is not None:
                return adapter
        return None

    def classify(self, path: str) -> FileClass:
        """The first adapter that claims the file decides; then documentation (no language owns
        it); the rest is ``OTHER``."""
        for adapter in self._languages.values():
            claimed = adapter.classify(path)
            if claimed is not None:
                return claimed
        return FileClass.DOCUMENTATION if is_documentation(path) else FileClass.OTHER
