"""Reading what ruff, mypy, compileall and pytest print.

Every parser works from the text of the log and never raises: a line it does not understand is
skipped, and output it cannot make sense of at all is ``Unparseable``, which the gate turns into an
inconclusive result instead of a pass.
"""

import json
import re
from collections import Counter

from forja.core.adapters import EvidenceDraft, Parsed, ParseResult, ToolOutput, Unparseable
from forja.core.adapters.common import read_output_lines
from forja.core.evidence import Target, TargetKind

MAX_LINE = 2000  # a line longer than this is not one of ours

_RUFF_LINE = re.compile(
    r"^(?P<path>[^\s:][^:]*):(?P<row>\d+):(?P<col>\d+): (?P<code>[A-Za-z0-9_-]+):? "
)
_RUFF_PATH = re.compile(r"^\s*--> (?P<path>.+?):\d+:\d+$")
_COMPILE_ERROR = re.compile(r"^\*\*\* Error compiling '(?P<path>.+)'\.\.\.$")
_TEST_LINE = re.compile(
    r"^(?P<outcome>PASSED|FAILED|ERROR|XFAIL|XPASS) (?P<id>.+?)(?: - (?P<why>.*))?$"
)
# what a failed test raised, as pytest writes it after the id: ``NameError: name ...``, or the
# name alone when the exception says nothing; a word alone that is no such name is a message
_RAISED = re.compile(r"^(?P<name>[A-Za-z_][\w.]*)(?::|(?<=Error)$|(?<=Exception)$)")
_SKIPPED = re.compile(r"^SKIPPED \[(?P<count>\d{1,9})\]")
_SUMMARY = re.compile(r"^=+ short test summary info =+$")

LINT_OK = (0, 1)
MYPY_OK = (0, 1)  # 2 is a blocking error: a syntax error, an unreadable source, a bad option
PYTEST_NO_TESTS = 5


def _lines(output: ToolOutput) -> list[str]:
    return [line for line in read_output_lines(output.log_path) if len(line) <= MAX_LINE]


def _path(text: str) -> str:
    return text.replace("\\\\", "/").replace("\\", "/").removeprefix("./")


def _draft(
    measurement: str, kind: TargetKind, target: str, value: bool | int | float | str, unit: str
) -> EvidenceDraft:
    return EvidenceDraft(measurement, Target(kind=kind, id=target), value, unit)


def parse_lint(output: ToolOutput) -> ParseResult:
    """Violations per file and rule (``ruff check --output-format=concise``)."""
    if output.exit_code not in LINT_OK:
        return Unparseable(f"ruff check ended with {output.exit_code}")
    counts: Counter[tuple[str, str]] = Counter()
    for line in _lines(output):
        found = _RUFF_LINE.match(line)
        if found:
            counts[(_path(found["path"]), found["code"])] += 1
    if output.exit_code == 1 and not counts:
        return Unparseable("ruff check reported violations in a form this parser does not know")
    return Parsed(
        tuple(
            _draft(f"lint.{code}", TargetKind.FILE, path, count, "violations")
            for (path, code), count in sorted(counts.items())
        )
    )


def parse_types(output: ToolOutput) -> ParseResult:
    """Type errors per file and error code (``mypy --output json``: one object per line)."""
    if output.exit_code not in MYPY_OK:
        return Unparseable(f"mypy ended with {output.exit_code}")
    counts: Counter[tuple[str, str]] = Counter()
    for line in read_output_lines(output.log_path):
        found = _mypy_error(line)
        if found is not None:
            counts[found] += 1
    if output.exit_code == 1 and not counts:
        return Unparseable("mypy reported errors in a form this parser does not know")
    return Parsed(
        tuple(
            _draft(f"types.{code}", TargetKind.FILE, path, count, "errors")
            for (path, code), count in sorted(counts.items())
        )
    )


def _mypy_error(line: str) -> tuple[str, str] | None:
    """The (file, code) of a line that is a mypy error object; ``None`` for any other line."""
    if not line.startswith("{"):
        return None
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("severity") != "error":
        return None
    path, code = data.get("file"), data.get("code")
    if not isinstance(path, str):
        return None
    return _path(path), code if isinstance(code, str) else "error"


def parse_format(output: ToolOutput) -> ParseResult:
    """Files that are not in canonical form, and files ruff could not read."""
    if output.exit_code not in (0, 1, 2):
        return Unparseable(f"ruff format ended with {output.exit_code}")
    drafts: list[EvidenceDraft] = []
    pending: str | None = None
    for line in _lines(output):
        if line.startswith("unformatted:"):
            pending = "format.unformatted"
        elif line.startswith("invalid-syntax:"):
            pending = "format.invalid_syntax"
        elif line.startswith("Would reformat: "):
            drafts.append(
                _format_draft("format.unformatted", line.removeprefix("Would reformat: "))
            )
        else:
            found = _RUFF_PATH.match(line)
            if found and pending is not None:
                drafts.append(_format_draft(pending, found["path"]))
                pending = None
    if output.exit_code != 0 and not drafts:
        return Unparseable("ruff format failed in a form this parser does not know")
    return Parsed(tuple(sorted(drafts, key=lambda d: (d.measurement, d.target.id))))


def _format_draft(measurement: str, path: str) -> EvidenceDraft:
    return _draft(measurement, TargetKind.FILE, _path(path.strip()), 1, "files")


def parse_build(output: ToolOutput) -> ParseResult:
    """The files compileall could not compile."""
    if output.exit_code is None:
        return Unparseable("compileall did not finish")
    if output.exit_code == 0:
        return Parsed(())
    paths = sorted({_path(m["path"]) for m in map(_COMPILE_ERROR.match, _lines(output)) if m})
    if not paths:
        return Parsed((_draft("build.failed", TargetKind.MODULE, "build", 1, "failures"),))
    return Parsed(
        tuple(_draft("build.compile_error", TargetKind.FILE, path, 1, "errors") for path in paths)
    )


def parse_tests(output: ToolOutput) -> ParseResult:
    """Outcome of every test (``pytest -rA``), and how many were skipped."""
    code = output.exit_code
    if code == PYTEST_NO_TESTS:
        return Parsed((_draft("test.collected", TargetKind.MODULE, "tests", 0, "tests"),))
    if code not in (0, 1, 2):
        return Unparseable(f"pytest ended with {code}")
    outcomes, raised, skipped = _scan_tests(_summary_lines(_lines(output)))
    drafts = [
        _draft("test.outcome", TargetKind.SYMBOL, test_id, outcome, "outcome")
        for test_id, outcome in sorted(outcomes.items())
    ]
    drafts += [
        _draft("test.failure", TargetKind.SYMBOL, test_id, name, "exception")
        for test_id, name in sorted(raised.items())
    ]
    if skipped:
        drafts.append(_draft("test.skipped", TargetKind.MODULE, "tests", skipped, "tests"))
    failed = any(o in ("failed", "error") for o in outcomes.values())
    if code != 0 and not failed:
        return Unparseable("pytest reported a failure in a form this parser does not know")
    if code == 0 and failed:
        return Unparseable("pytest passed but its output lists failures")
    return Parsed(tuple(drafts))


def _summary_lines(lines: list[str]) -> list[str]:
    # what a test prints while it runs comes before the summary; only the summary is the verdict
    summary = [i for i, line in enumerate(lines) if _SUMMARY.match(line)]
    return lines[summary[-1] + 1 :] if summary else lines


def _scan_tests(lines: list[str]) -> tuple[dict[str, str], dict[str, str], int]:
    """The outcome of each test, what each failed one raised (when pytest says), and how many
    were skipped."""
    outcomes: dict[str, str] = {}
    raised: dict[str, str] = {}
    skipped = 0
    for line in lines:
        found = _TEST_LINE.match(line)
        if found:
            outcomes[found["id"]] = found["outcome"].lower()
            exception = _RAISED.match(found["why"] or "")
            if found["outcome"] == "FAILED" and exception:
                raised[found["id"]] = exception["name"]
            continue
        counted = _SKIPPED.match(line)
        if counted:
            skipped += int(counted["count"])
    return outcomes, raised, skipped
