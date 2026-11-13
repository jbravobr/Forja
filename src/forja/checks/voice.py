"""Gate 12: the prose an attempt adds carries no marker of text written on autopilot.

Prose is the added lines of a documentation file and the added comment lines of a source file in a
language the Forja reads (a string in code is data, not prose). A finding names the file, the line
and why the pattern is on the list; it never copies the text. Em dashes are counted per file.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from forja.checks.base import decided, rejected
from forja.checks.changes import AddedLine, Changes, added_lines
from forja.checks.structure import SyntaxProblem, tree_of
from forja.checks.structure.nodes import COMMENTS, walk
from forja.checks.voice_markers import MARKERS, MAX_DASHES, Marker
from forja.core.adapters import FileClass
from forja.core.gates import Approved, Finding, GateInput, GateResult, Location

DASH = "—"
COMMIT_MESSAGE = "commit message"


def comment_lines(path: str, source: str) -> frozenset[int] | None:
    """The lines (from 1) a comment covers, or ``None`` for a file the Forja does not read."""
    try:
        tree = tree_of(path, source)
    except SyntaxProblem:
        return None
    if tree is None:
        return None
    covered: set[int] = set()
    for node in walk(tree[1]):
        if node.type in COMMENTS:
            covered.update(range(node.start_point[0] + 1, node.end_point[0] + 2))
    return frozenset(covered)


@dataclass(frozen=True)
class VoiceCheck:
    worktree: Path
    changes: Changes
    classify: Callable[[str], FileClass]
    markers: Sequence[Marker] = MARKERS
    message: str = ""  # the commit message the attempt would get: same profile as the rest
    id: str = "voice"
    gate: int = 12
    requires: frozenset[str] = frozenset()

    async def evaluate(self, data: GateInput) -> GateResult:
        return await decided(self._judge(data))

    async def _judge(self, data: GateInput) -> GateResult:
        limit = _max_dashes(data)
        findings: list[Finding] = [
            Finding(message=f"commit message: {marker.why}", target=COMMIT_MESSAGE)
            for marker in self.markers
            if marker.found_in(self.message)
        ]
        if self.message.count(DASH) > limit:
            findings.append(
                Finding(
                    message="too many em dashes in the commit message",
                    target=COMMIT_MESSAGE,
                    measured=self.message.count(DASH),
                    limit=limit,
                )
            )
        for path, lines in self._prose().items():
            for line in lines:
                for marker in self.markers:
                    if marker.found_in(line.text):
                        findings.append(_finding(marker.why, path, line.number))
            dashes = sum(line.text.count(DASH) for line in lines)
            if dashes > limit:
                findings.append(
                    Finding(
                        message="too many em dashes in the added text",
                        target=path,
                        measured=dashes,
                        limit=limit,
                    )
                )
        return rejected(findings) if findings else Approved()

    def _prose(self) -> dict[str, list[AddedLine]]:
        """The added lines that are prose, by file."""
        prose: dict[str, list[AddedLine]] = {}
        covered: dict[str, frozenset[int] | None] = {}
        for line in added_lines(self.changes.patch):
            if line.path is None or line.number is None:
                continue
            if self.classify(line.path) is FileClass.DOCUMENTATION:
                prose.setdefault(line.path, []).append(line)
                continue
            if line.path not in covered:
                covered[line.path] = self._comments_of(line.path)
            comments = covered[line.path]
            if comments is not None and line.number in comments:
                prose.setdefault(line.path, []).append(line)
        return prose

    def _comments_of(self, path: str) -> frozenset[int] | None:
        try:
            source = (self.worktree / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        return comment_lines(path, source)


def _max_dashes(data: GateInput) -> int:
    limits = data.values.get("limits")
    chosen = limits.get("max_dashes") if isinstance(limits, dict) else None
    return chosen if isinstance(chosen, int) and not isinstance(chosen, bool) else MAX_DASHES


def _finding(message: str, path: str, number: int | None) -> Finding:
    try:
        return Finding(message=message, target=path, location=Location(path=path, line=number))
    except ValidationError:
        return Finding(message=message, target=path)
