import sys
from typing import Any, Protocol


class ProcessGroup(Protocol):
    """Everything needed to stop one process and all its descendants."""

    def spawn_kwargs(self) -> dict[str, Any]:
        """Extra keyword arguments for process creation."""
        ...

    def lower_priority(self) -> None:
        """Make the whole tree yield the processor to everything else on the machine. Asked
        before the process is created."""
        ...

    def attach(self, pid: int) -> None:
        """Put the freshly created process under this group's control."""
        ...

    def interrupt(self) -> None:
        """Polite stop request. Best effort; failure is not an error."""
        ...

    def kill(self) -> None:
        """Kill every process in the group. Safe to call repeatedly."""
        ...

    def peak_memory(self) -> int | None:
        """Peak bytes held at once by the whole tree, or ``None`` where it cannot be measured.

        Meant to be read after ``kill`` and before ``close``. Never zero for "unknown".
        """
        ...

    def close(self) -> None: ...


def new_group() -> ProcessGroup:
    if sys.platform == "win32":
        from forja.core.process import _windows

        return _windows.WindowsJobGroup()
    from forja.core.process import _posix

    return _posix.PosixGroup()
