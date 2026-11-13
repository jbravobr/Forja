"""Reading what ``dotnet format``, ``dotnet build`` and ``dotnet test`` print.

Every parser works from the text of the log and never raises: a line it does not understand is
skipped, and output it cannot make sense of at all is ``Unparseable``, which the gate turns into an
inconclusive result instead of a pass. The tools print absolute paths; they are made relative to
the directory the command ran in.
"""

import re
from collections import Counter
from pathlib import PurePosixPath

from forja.core.adapters import EvidenceDraft, Parsed, ParseResult, ToolOutput, Unparseable
from forja.core.adapters.common import keep_worst, read_output_lines
from forja.core.evidence import Target, TargetKind

MAX_LINE = 100_000  # a test name can be long; beyond this the line is not ours

_POSITION = r"(?P<path>[^\s(][^(]*?\.cs)\(\d+,\d+\)"
_FORMAT_LINE = re.compile(rf"^{_POSITION}: error (?P<rule>[A-Z]+\d*): ")
_LINT_LINE = re.compile(rf"^{_POSITION}: (?:warning|error) (?P<rule>[A-Za-z]+\d+): ")
_BUILD_ERROR = re.compile(rf"^{_POSITION}: error [A-Z]+\d+: ")
_TEST_LINE = re.compile(r"^\s+(?P<outcome>Passed|Failed|Skipped) (?P<name>.+?) \[[^\]]*\]$")
_OUTCOMES = {"Passed": "passed", "Failed": "failed", "Skipped": "skipped"}
_FORMAT_OK = (0, 2)
_LINT_OK = (0, 2)


def _lines(output: ToolOutput) -> list[str]:
    return [line for line in read_output_lines(output.log_path) if len(line) <= MAX_LINE]


def _path(text: str, output: ToolOutput) -> str:
    """The path as the repository knows it: POSIX, and relative to where the command ran."""
    posix = text.strip().replace("\\", "/")
    root = output.root.as_posix().rstrip("/") + "/" if output.root is not None else ""
    if root and posix.startswith(root):
        posix = posix[len(root) :]
    elif PurePosixPath(posix).is_absolute() or re.match(r"^[A-Za-z]:/", posix):
        posix = posix.rpartition("/")[2]  # outside the repository: only the name is ours to say
    return posix.removeprefix("./")


def _draft(
    measurement: str, kind: TargetKind, target: str, value: bool | int | float | str, unit: str
) -> EvidenceDraft:
    return EvidenceDraft(measurement, Target(kind=kind, id=target), value, unit)


def parse_format(output: ToolOutput) -> ParseResult:
    """Files ``dotnet format`` says are not in the form the project's settings ask for."""
    if output.exit_code not in _FORMAT_OK:
        return Unparseable(f"dotnet format ended with {output.exit_code}")
    files: dict[str, EvidenceDraft] = {}
    for line in _lines(output):
        found = _FORMAT_LINE.match(line.strip())
        if found:
            path = _path(found["path"], output)
            files[path] = _draft("format.unformatted", TargetKind.FILE, path, 1, "files")
    if output.exit_code == 2 and not files:
        return Unparseable("dotnet format reported changes in a form this parser does not know")
    return Parsed(tuple(files[path] for path in sorted(files)))


def parse_lint(output: ToolOutput) -> ParseResult:
    """Analyzer findings per file and rule (``dotnet format analyzers``)."""
    if output.exit_code not in _LINT_OK:
        return Unparseable(f"dotnet format ended with {output.exit_code}")
    counts: Counter[tuple[str, str]] = Counter()
    for line in _lines(output):
        found = _LINT_LINE.match(line.strip())
        if found:
            counts[(_path(found["path"], output), found["rule"])] += 1
    if output.exit_code == 2 and not counts:
        return Unparseable("dotnet format reported findings in a form this parser does not know")
    return Parsed(
        tuple(
            _draft(f"lint.{rule}", TargetKind.FILE, path, count, "violations")
            for (path, rule), count in sorted(counts.items())
        )
    )


def parse_build(output: ToolOutput) -> ParseResult:
    """The files ``dotnet build`` could not compile."""
    if output.exit_code is None:
        return Unparseable("dotnet build did not finish")
    if output.exit_code == 0:
        return Parsed(())
    paths = sorted({_path(m["path"], output) for m in map(_BUILD_ERROR.match, _lines(output)) if m})
    if not paths:
        return Parsed((_draft("build.failed", TargetKind.MODULE, "build", 1, "failures"),))
    return Parsed(
        tuple(_draft("build.compile_error", TargetKind.FILE, path, 1, "errors") for path in paths)
    )


def parse_tests(output: ToolOutput) -> ParseResult:
    """Outcome of every test (``dotnet test`` with the console logger at normal verbosity)."""
    code = output.exit_code
    if code not in (0, 1):
        return Unparseable(f"dotnet test ended with {code}")
    outcomes: dict[str, str] = {}
    for line in _lines(output):
        found = _TEST_LINE.match(line)
        if found:
            keep_worst(outcomes, found["name"], _OUTCOMES[found["outcome"]])
    failed = any(o == "failed" for o in outcomes.values())
    if code == 1 and not failed:
        return Unparseable("dotnet test reported a failure in a form this parser does not know")
    if code == 0 and failed:
        return Unparseable("dotnet test passed but its output lists failures")
    if not outcomes:
        if code == 1:
            return Unparseable("dotnet test printed no test results")
        return Parsed((_draft("test.collected", TargetKind.MODULE, "tests", 0, "tests"),))
    drafts = [
        _draft("test.outcome", TargetKind.SYMBOL, test_id, outcome, "outcome")
        for test_id, outcome in sorted(outcomes.items())
    ]
    skipped = sum(1 for o in outcomes.values() if o == "skipped")
    if skipped:
        drafts.append(_draft("test.skipped", TargetKind.MODULE, "tests", skipped, "tests"))
    return Parsed(tuple(drafts))
