"""Reading what Prettier, ESLint (through Forja's formatter), ``tsc`` and the test runners print.

Every parser works from the text of the log and never raises: a line it does not understand is
skipped, and output it cannot make sense of at all is ``Unparseable``, which the gate turns into an
inconclusive result instead of a pass. Vitest (``tap-flat``) and Jest (Forja's reporter) both print
TAP, so one parser reads the tests of either.
"""

import re
from collections import Counter

from forja.core.adapters import EvidenceDraft, Parsed, ParseResult, ToolOutput, Unparseable
from forja.core.adapters.common import read_output_lines
from forja.core.evidence import Target, TargetKind

MAX_LINE = 100_000  # a test name can be long; beyond this the line is not ours

_SOURCE = r"[^\s:()][^:()]*\.(?:[cm]?[jt]s|[jt]sx)"
_PRETTIER_ERROR = re.compile(rf"^\[error\] (?P<path>{_SOURCE}): ")
_PRETTIER_FILE = re.compile(rf"^(?P<path>{_SOURCE})$")
_LINT_LINE = re.compile(rf"^(?P<path>{_SOURCE}):\d+:\d+: .*\((?P<rule>[^()\s]+)\)$")
_TSC_ERROR = re.compile(rf"^(?P<path>{_SOURCE})\(\d+,\d+\): error TS\d+: ")
_SYNTAX_ERROR = re.compile(rf"^(?P<path>{_SOURCE}):\d+: \w*Error\b")  # Forja's ``js_syntax.cjs``
_TAP_RESULT = re.compile(r"^(?P<not>not )?ok \d+ - (?P<rest>.*)$")
_TAP_NOTE = re.compile(r" # (?P<note>SKIP|TODO|time=)\S*.*$")
_LINT_OK = (0, 1)
_PRETTIER_OK = (0, 1, 2)


def _lines(output: ToolOutput) -> list[str]:
    return [line for line in read_output_lines(output.log_path) if len(line) <= MAX_LINE]


def _path(text: str) -> str:
    return text.strip().replace("\\", "/").removeprefix("./")


def _draft(
    measurement: str, kind: TargetKind, target: str, value: bool | int | float | str, unit: str
) -> EvidenceDraft:
    return EvidenceDraft(measurement, Target(kind=kind, id=target), value, unit)


def parse_format(output: ToolOutput) -> ParseResult:
    """Files ``prettier -l`` lists (not in its form) and files it could not parse."""
    if output.exit_code not in _PRETTIER_OK:
        return Unparseable(f"prettier ended with {output.exit_code}")
    drafts: dict[tuple[str, str], EvidenceDraft] = {}
    for line in _lines(output):
        text = line.strip()
        broken = _PRETTIER_ERROR.match(text)
        listed = _PRETTIER_FILE.match(text)
        if broken:
            name, path = "format.invalid_syntax", _path(broken["path"])
        elif listed:
            name, path = "format.unformatted", _path(listed["path"])
        else:
            continue
        drafts[(name, path)] = _draft(name, TargetKind.FILE, path, 1, "files")
    if output.exit_code == 2 and not drafts:
        return Unparseable("prettier failed in a form this parser does not know")
    return Parsed(tuple(drafts[key] for key in sorted(drafts)))


def parse_lint(output: ToolOutput) -> ParseResult:
    """Findings per file and rule (``path:line:col: message (rule)``, Forja's ESLint formatter)."""
    if output.exit_code not in _LINT_OK:
        return Unparseable(f"eslint ended with {output.exit_code}")
    counts: Counter[tuple[str, str]] = Counter()
    for line in _lines(output):
        found = _LINT_LINE.match(line.strip())
        if found:
            counts[(_path(found["path"]), found["rule"])] += 1
    if output.exit_code == 1 and not counts:
        return Unparseable("eslint reported problems in a form this parser does not know")
    return Parsed(
        tuple(
            _draft(f"lint.{rule}", TargetKind.FILE, path, count, "violations")
            for (path, rule), count in sorted(counts.items())
        )
    )


def parse_build(output: ToolOutput) -> ParseResult:
    """The files ``tsc`` could not compile or, in plain JavaScript, Node could not parse."""
    if output.exit_code is None:
        return Unparseable("the build did not finish")
    if output.exit_code == 0:
        return Parsed(())
    lines = _lines(output)
    found = [_TSC_ERROR.match(line) or _SYNTAX_ERROR.match(line) for line in lines]
    paths = sorted({_path(m["path"]) for m in found if m})
    if not paths:
        return Parsed((_draft("build.failed", TargetKind.MODULE, "build", 1, "failures"),))
    return Parsed(
        tuple(_draft("build.compile_error", TargetKind.FILE, path, 1, "errors") for path in paths)
    )


def parse_tests(output: ToolOutput) -> ParseResult:
    """Outcome of every test (TAP), and how many were skipped.

    A test file that did not load is one failed outcome named after the file, so the failure is
    never lost.
    """
    code = output.exit_code
    if code not in (0, 1):
        return Unparseable(f"the test runner ended with {code}")
    outcomes: dict[str, str] = {}
    for line in _lines(output):
        found = _TAP_RESULT.match(line)
        if not found:
            continue
        name = _TAP_NOTE.sub("", found["rest"]).strip()
        note = _TAP_NOTE.search(found["rest"])
        if found["not"]:
            outcome = "failed"
        elif note and note["note"] in ("SKIP", "TODO"):
            outcome = "skipped"
        else:
            outcome = "passed"
        outcomes[name] = outcome
    failed = any(o == "failed" for o in outcomes.values())
    if code == 1 and not failed:
        return Unparseable("the test runner reported a failure in a form this parser does not know")
    if code == 0 and failed:
        return Unparseable("the test runner passed but its output lists failures")
    if not outcomes:
        if code == 1:
            return Unparseable("the test runner printed no test results")
        return Parsed((_draft("test.collected", TargetKind.MODULE, "tests", 0, "tests"),))
    drafts = [
        _draft("test.outcome", TargetKind.SYMBOL, test_id, outcome, "outcome")
        for test_id, outcome in sorted(outcomes.items())
    ]
    skipped = sum(1 for o in outcomes.values() if o == "skipped")
    if skipped:
        drafts.append(_draft("test.skipped", TargetKind.MODULE, "tests", skipped, "tests"))
    return Parsed(tuple(drafts))
