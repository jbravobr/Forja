"""A task as the orchestrator hands it to whoever works on one of its stages or judges it."""

from dataclasses import dataclass

from forja.core.artifacts.models import Producer


@dataclass(frozen=True)
class TaskInfo:
    """A task seen through one of its stages: ``stage_id`` and ``role`` are that stage's."""

    task_id: str
    stage_id: str
    intent: str
    scope: tuple[str, ...]
    max_files: int
    max_lines: int
    role: Producer = Producer.IMPLEMENTER
    test_root: str = ""
    attempt_prefix: str = ""  # attempts are ``<prefix><n>``; empty means ``<task>-a``
    frozen_digest: str = ""  # of the files under ``test_root`` the test author committed
    reviews: str = ""  # an extra test stage: the implementer stage whose committed code it tests
    # a refactoring whose test author goes first: its tests say what the code does today and pass
    characterizes: bool = False
