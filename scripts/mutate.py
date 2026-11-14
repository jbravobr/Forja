"""The mutation gate, as one command whose answer is the exit code.

    scripts/mutate.py              # the source files changed since HEAD (and new ones)
    scripts/mutate.py --all        # every source file
    scripts/mutate.py src/forja/checks/changes.py
    scripts/mutate.py --max-workers 4   # at most four runs at once (default: 8, or the cores)

It runs the Forja's own mutator (``forja.mutation``, the rules of unclebob/mutator on tree-sitter,
ADR 0021) on the Forja's own source, the way the mutator runs on any project: the coverage report
decides which sites are run (a site no test reaches is reported as uncovered, not run), each mutant
is judged by the test command, and a failing or timed-out run kills it. The choice of tests is the
script's: only those that exercise the file, found from a coverage run that records which test ran
each line. A surviving mutant fails the run unless ``mutation-suppressions.toml`` names it, with a
reason a person approved.

What makes it faster changes no verdict. The coverage run deals the test files into several runs
that go at the same time. The unchanged copy of each file, where every test of the file has to
pass, is run over several processes. A mutant is judged first by the test files that run its
line; one that survives them is judged again by every test of the file before it counts as a
survivor, so the answer is always the one all of them give.

As in the mutator, a run leaves under ``.metrics/mutate`` what became of each mutant and a hash
of each function, and the next run is differential: a mutant killed then, in a function whose
text is the same now, is not run again. Survivors and the mutants of a changed function are.
Unlike the mutator, the run also keeps which test files killed each mutant and a hash of each:
a mutant whose tests changed, or went away, is run again. ``--mutate-all`` ignores what was left
and runs every one: the run to make before a release, because what the tests lean on outside
``tests`` (helpers under ``src``, data files, another function) is not looked at.

Nothing in the source can switch the gate off: a comment that does is rejected by the scope gate.

Exit: 0 every mutant was killed or suppressed; 2 the tests fail before any mutation; 3 a mutant
survived; 4 the canary failed. It runs wherever Python runs, Windows included.
"""

import argparse
import ast
import asyncio
import functools
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

from coverage import CoverageData

from forja.core.reports.models import MutantResult, MutantStatus, MutationReport
from forja.mutation import apply_site, discover
from forja.mutation.executor import (
    TIMEOUT_FACTOR,
    BaselineFailed,
    Mutant,
    TestsDidNotRun,
    TestVerdict,
    run_mutants,
)
from forja.mutation.sites import function_spans

ROOT = Path(__file__).resolve().parent.parent
SUPPRESSIONS = ROOT / "mutation-suppressions.toml"
COVERAGE = ROOT / "target" / "coverage" / "python"
NL = chr(10)
LCOV = COVERAGE / "lcov.info"  # where the engine looks for the coverage of a Python project
# The integration tests run too: without them the code they alone reach (the call to the agent CLI,
# the way back) counts as untested. Each file only runs the test files that reach it.
TESTS_ENV: dict[str, str] = {}
TEST_TIMEOUT = 30  # seconds for one test; the slowest of the project takes a few


def run(
    *command: str, env: dict[str, str] | None = None, cwd: Path = ROOT
) -> subprocess.CompletedProcess[str]:
    merged = {**os.environ, **(env or {})}
    return subprocess.run(command, cwd=cwd, env=merged, capture_output=True, text=True)


def changed_sources() -> list[str]:
    tracked = run("git", "diff", "--name-only", "HEAD", "--", "src").stdout.split()
    fresh = run("git", "ls-files", "--others", "--exclude-standard", "--", "src").stdout.split()
    return sorted(p for p in {*tracked, *fresh} if p.endswith(".py") and (ROOT / p).is_file())


def all_sources() -> list[str]:
    found = (p.relative_to(ROOT).as_posix() for p in (ROOT / "src").rglob("*.py"))
    return sorted(p for p in found if not p.endswith("__init__.py"))


def settings() -> dict[str, list[str]]:
    """``[tool.forja.mutation]`` of pyproject.toml: tests that cannot run where the gate runs."""
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    found: dict[str, list[str]] = document.get("tool", {}).get("forja", {}).get("mutation", {})
    return found


def deselected() -> list[str]:
    return [arg for name in settings().get("deselect", []) for arg in ("--deselect", name)]


LinesToTests = dict[str, dict[int, set[str]]]


def record_coverage(reuse: bool, workers: int) -> tuple[dict[str, list[str]], LinesToTests]:
    """Which test files ran each source file, and which ran each of its lines, from one run of
    the fast tests."""
    COVERAGE.mkdir(parents=True, exist_ok=True)
    data = COVERAGE / ".coverage"
    # the report this script once wrote here: gigabytes nobody reads any more
    (COVERAGE / "contexts.json").unlink(missing_ok=True)
    if not (reuse and data.exists() and LCOV.exists()):
        measure_coverage(data, workers)
    by_line = tests_by_line(recorded_contexts(data))
    tests_of = {
        name: ordered(name, set().union(*lines.values())) for name, lines in by_line.items()
    }
    return {name: tests for name, tests in tests_of.items() if tests}, by_line


def recorded_contexts(data: Path) -> Iterator[tuple[str, dict[int, list[str]]]]:
    """Each measured file of the project with the tests that ran each of its lines, read from
    the coverage data a file at a time. The same thing as a report with every context written
    out weighs gigabytes, on the disk and again in memory."""
    recorded = CoverageData(str(data))
    recorded.read()
    for name in sorted(recorded.measured_files()):
        try:
            inside = Path(name).resolve().relative_to(ROOT).as_posix()
        except ValueError:
            continue
        yield inside, recorded.contexts_by_lineno(name)


def tests_by_line(files: Iterable[tuple[str, Mapping[int, Iterable[str]]]]) -> LinesToTests:
    """The test files that ran each line of each source file, from the contexts of the coverage
    data. A statement of several lines is reached or it is not, so its other lines take the
    tests of the first (as ``widen`` does with the hits)."""
    found: LinesToTests = {}
    for name, by_number in files:
        lines: dict[int, set[str]] = defaultdict(set)
        for written, contexts in by_number.items():
            modules = {module for module in map(test_file, contexts) if module}
            lines[written] |= modules
        path = Path(name) if Path(name).is_absolute() else ROOT / name
        if path.is_file():
            for first, last in statement_spans(path.read_text(encoding="utf-8")):
                for number in range(first + 1, last + 1):
                    lines[number] |= lines.get(first, set())
        found[Path(name).as_posix()] = dict(lines)
    return found


def tests_of_line(by_line: LinesToTests, file: str, line: int, everything: list[str]) -> list[str]:
    """The test files that run ``line``, in the order of ``everything`` (the tests of the whole
    file). A line no test is named for (one that only runs while a module is imported) keeps
    them all."""
    reached = by_line.get(file, {}).get(line)
    return [test for test in everything if test in reached] if reached else everything


def all_test_files() -> list[str]:
    """Every test file, the largest first: a rough order of how long each takes to run."""
    found = [
        path
        for path in (ROOT / "tests").rglob("*.py")
        if path.name.startswith("test_") or path.name.endswith("_test.py")
    ]
    found.sort(key=lambda path: (-path.stat().st_size, path.as_posix()))
    return [path.relative_to(ROOT).as_posix() for path in found]


def shards(files: list[str], count: int) -> list[list[str]]:
    """``files`` dealt into at most ``count`` hands, each of about the same weight, and each
    in the order of the names: the order pytest gives the files of a directory, which some
    tests count on without saying (a fixture one ``conftest.py`` takes from another)."""
    hands = [files[start :: max(1, count)] for start in range(max(1, count))]
    return [sorted(hand) for hand in hands if hand]


SHARD_OK = (0, 5)  # 5: every test of the hand was left out, which is no failure


def measure_coverage(data: Path, workers: int) -> None:
    """Run the fast tests under coverage, the test files dealt into ``workers`` runs that go at
    the same time, and join what each one saw. Every test still runs once, in a process that
    runs its file from start to end, so what each test covers is what one run alone would say."""
    data.unlink(missing_ok=True)
    for stale in COVERAGE.glob(".coverage.part-*"):
        stale.unlink()
    hands = shards(all_test_files(), workers)
    parts = [COVERAGE / f".coverage.part-{number}" for number in range(len(hands))]

    def one(part: Path, files: list[str]) -> subprocess.CompletedProcess[str]:
        return run(
            sys.executable, "-m", "coverage", "run", f"--data-file={part}", "-m", "pytest", "-q",
            "-p", "no:cacheprovider", "--no-header", "-n0", "-m", "not slow", *deselected(),
            *files, env=TESTS_ENV,
        )  # fmt: skip

    with ThreadPoolExecutor(max_workers=len(hands) or 1) as pool:
        finished = list(pool.map(one, parts, hands))
    for tested in finished:
        if tested.returncode not in SHARD_OK:
            print(tested.stdout[-3000:], file=sys.stderr)
            raise SystemExit(2)
    written = [str(part) for part in parts if part.is_file()]
    joined = run(sys.executable, "-m", "coverage", "combine", f"--data-file={data}", *written)
    if joined.returncode != 0:
        print(joined.stderr, file=sys.stderr)
        raise SystemExit(2)
    exported = run(
        sys.executable, "-m", "coverage", "lcov", f"--data-file={data}", "-o", str(LCOV)
    )  # fmt: skip
    if exported.returncode != 0:
        print(exported.stderr, file=sys.stderr)
        raise SystemExit(2)
    LCOV.write_text(widen(LCOV.read_text(encoding="utf-8")), encoding="utf-8")


def statement_spans(source: str) -> list[tuple[int, int]]:
    """First and last line of each statement; of a compound one, only its header."""
    spans = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.stmt):
            continue
        body = getattr(node, "body", None)
        if body and isinstance(body, list) and isinstance(body[0], ast.stmt):
            header_end = max(node.lineno, body[0].lineno - 1)
            spans.append((node.lineno, header_end))
        else:
            spans.append((node.lineno, node.end_lineno or node.lineno))
    return spans


def widen(lcov: str) -> str:
    """Make a statement that spans several lines count as covered on all of them.

    ``coverage`` records the first line of a statement, so a mutation on the second line of a
    call or of a condition would be called uncovered although the statement runs. A statement is
    reached or it is not: the lines after the first take the hits of the first.
    """
    out = []
    for record in lcov.split("end_of_record\n"):
        lines = record.splitlines()
        name = next((line[3:] for line in lines if line.startswith("SF:")), None)
        if name is None:
            continue
        path = Path(name) if Path(name).is_absolute() else ROOT / name
        hits = line_hits(lines)
        if path.is_file():
            spread_hits(hits, statement_spans(path.read_text(encoding="utf-8")))
        out.append(render_record(lines, hits))
    return "".join(out)


def line_hits(lines: list[str]) -> dict[int, int]:
    hits: dict[int, int] = {}
    for line in lines:
        if line.startswith("DA:"):
            number, count = line[3:].split(",")[:2]
            hits[int(number)] = int(count)
    return hits


def spread_hits(hits: dict[int, int], spans: list[tuple[int, int]]) -> None:
    for first, last in spans:
        if hits.get(first, 0) > 0:
            for number in range(first + 1, last + 1):
                hits[number] = max(hits.get(number, 0), hits[first])


def render_record(lines: list[str], hits: dict[int, int]) -> str:
    kept = [line for line in lines if not line.startswith(("DA:", "LF:", "LH:"))]
    end = kept.index("end_of_record") if "end_of_record" in kept else len(kept)
    body = [f"DA:{number},{count}" for number, count in sorted(hits.items())]
    body += [f"LF:{len(hits)}", f"LH:{sum(1 for c in hits.values() if c > 0)}"]
    return NL.join([*kept[:end], *body, "end_of_record"]) + NL


def ordered(source: str, tests: set[str]) -> list[str]:
    """Tests named for the module first: they usually kill a mutant, so ``-x`` stops early."""
    stem = Path(source).stem
    return sorted(tests, key=lambda test: (stem not in Path(test).stem, test))


@functools.cache  # the report names each test once for every line it ran
def test_file(context: str) -> str | None:
    """The test file of a coverage context (``tests.pkg.test_mod.Class.test_x``), if any."""
    parts = [part for part in context.split("|")[0].split(".") if part]
    while parts:
        candidate = Path(*parts).with_suffix(".py")
        if (ROOT / candidate).is_file():
            return candidate.as_posix()
        parts.pop()
    return None


def suppressions() -> list[dict[str, str]]:
    if not SUPPRESSIONS.exists():
        return []
    entries: list[dict[str, str]] = tomllib.loads(SUPPRESSIONS.read_text(encoding="utf-8")).get(
        "suppress", []
    )
    for entry in entries:
        if not all(entry.get(key) for key in ("file", "function", "change", "reason")):
            raise SystemExit(
                f"{SUPPRESSIONS.name}: every entry needs file, function, change, reason"
            )
    return entries


def covered_lines() -> dict[str, frozenset[int]]:
    """The lines a test runs, by source file, from the coverage report of the last run."""
    found: dict[str, frozenset[int]] = {}
    for record in LCOV.read_text(encoding="utf-8").split("end_of_record" + NL):
        lines = record.splitlines()
        name = next((line[3:] for line in lines if line.startswith("SF:")), None)
        if name is None:
            continue
        path = Path(name) if Path(name).is_absolute() else ROOT / name
        try:
            key = path.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            continue
        found[key] = frozenset(number for number, count in line_hits(lines).items() if count > 0)
    return found


def mutants_of(file: str, root: Path, covered: frozenset[int]) -> list[Mutant]:
    source = (root / file).read_text(encoding="utf-8")
    return [
        Mutant(
            file, site.line, site.description, site.function,
            apply_site(source, site).encode("utf-8"), any(line in covered for line in site.lines),
        )
        for site in discover(source, file)
    ]  # fmt: skip


SNAPSHOTS = ROOT / ".metrics" / "mutate"  # where the mutator keeps what a run found
KILLED, SURVIVED = "killed", "survived"


class History(NamedTuple):
    """What the last run left about one source file."""

    forms: Mapping[str, str]  # function -> the hash of its text
    outcomes: Mapping[str, str]  # mutation id -> killed or survived
    judges: Mapping[str, tuple[str, ...]]  # mutation id -> the test files that killed it
    tests: Mapping[str, str]  # test file -> the hash of what its verdict rested on


NO_HISTORY = History({}, {}, {}, {})


def form_digests(source: str, file: str) -> dict[str, str]:
    """A hash of the text of each function, whatever white space ends its lines. Functions of
    the same name are hashed together, in the order of the file (as in the mutator)."""
    lines = source.splitlines()
    spans: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for name, first, last in function_spans(source, file):
        spans[name].append((first, last))

    def one(first: int, last: int) -> str:
        chunk = NL.join(line.rstrip() for line in lines[first - 1 : last])
        return hashlib.sha256(chunk.encode("utf-8")).hexdigest()

    return {
        name: hashlib.sha256("".join(one(*span) for span in sorted(found)).encode()).hexdigest()
        for name, found in spans.items()
    }


def mutation_ids(source: str, file: str) -> list[str]:
    """What names each mutant from one run to the next, in the order of ``mutants_of``: the
    file, the function, where the token is and the change (the mutator's id)."""
    return [
        json.dumps(
            [file, site.function, site.start, site.end, site.original, site.mutant],
            separators=(",", ":"),
        )
        for site in discover(source, file)
    ]


def test_digests(files: Iterable[str]) -> dict[str, str]:
    """For each test file, a hash of what a verdict it gave rests on: its content, every
    ``conftest.py`` under ``tests`` (a fixture may come from any of them, not only from the
    folders above the file) and the tests left out of the run. A file that is gone has a hash
    no file has."""
    shared = hashlib.sha256(json.dumps(deselected()).encode("utf-8"))
    for path in sorted((ROOT / "tests").rglob("conftest.py")):
        shared.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        shared.update(hashlib.sha256(path.read_bytes()).digest())

    def one(name: str) -> str:
        path = ROOT / name
        own = hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b"gone"
        return hashlib.sha256(shared.digest() + own).hexdigest()

    return {name: one(name) for name in sorted(set(files))}


def judges_of(
    file: str, mutants: list[Mutant], tests: list[str], by_line: LinesToTests, again: list[int]
) -> list[list[str]]:
    """The test files that gave each of ``mutants`` its verdict in ``judge_file``: those of its
    line, or every one of ``tests`` for a mutant in ``again``, judged a second time."""
    return [
        tests if place in again else tests_of_line(by_line, file, mutant.line, tests)
        for place, mutant in enumerate(mutants)
    ]


def snapshot_path(file: str) -> Path:
    return SNAPSHOTS / f"{file}.json"


def load_history(file: str) -> History:
    """What the last run of ``file`` left; nothing if it left nothing that can be read. What a
    run from before the tests were kept left has no tests in it, so none of its verdicts is
    taken."""
    try:
        data = json.loads(snapshot_path(file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return NO_HISTORY
    forms = data.get("forms") if isinstance(data, dict) else None
    outcomes = data.get("outcomes") if isinstance(data, dict) else None
    if not isinstance(forms, dict) or not isinstance(outcomes, dict):
        return NO_HISTORY
    judges, tests = data.get("judges"), data.get("tests")
    return History(
        {name: digest for name, digest in forms.items() if isinstance(digest, str)},
        {key: status for key, status in outcomes.items() if status in (KILLED, SURVIVED)},
        {
            key: tuple(names)
            for key, names in (judges.items() if isinstance(judges, dict) else ())
            if isinstance(names, list) and all(isinstance(name, str) for name in names)
        },
        {
            name: digest
            for name, digest in (tests.items() if isinstance(tests, dict) else ())
            if isinstance(digest, str)
        },
    )


def carried(
    mutants: list[Mutant],
    ids: list[str],
    digests: Mapping[str, str],
    history: History,
    tests: Mapping[str, str],
) -> set[int]:
    """The places in ``mutants`` of those that are not run again, as in the mutator's
    differential run: a test runs the line, the last run killed the mutant, and the text of its
    function is the same as then. A survivor runs again, and so does every mutant of a function
    that changed or is new. One thing more than the mutator asks: every test file that killed
    the mutant has in ``tests`` (the hashes of now) the hash it had then, so a mutant whose
    tests were weakened or removed runs again."""
    kept = set()
    for place, (mutant, key) in enumerate(zip(mutants, ids, strict=True)):
        before = history.forms.get(mutant.function or "")
        unchanged = before is not None and before == digests.get(mutant.function or "")
        judges = history.judges.get(key, ())
        same_tests = bool(judges) and all(
            name in history.tests and history.tests[name] == tests.get(name) for name in judges
        )
        if mutant.covered and unchanged and same_tests and history.outcomes.get(key) == KILLED:
            kept.add(place)
    return kept


def with_carried(mutants: list[Mutant], kept: set[int], ran: MutationReport) -> MutationReport:
    """The report of every mutant of the file, in the order of ``mutants``: those of ``kept``
    killed as the last run found them, the others as ``ran`` (the run of the others) says."""
    fresh = iter(ran.mutants)
    return MutationReport(
        tuple(
            MutantResult(m.file, m.line, m.change, MutantStatus.KILLED, m.function)
            if place in kept
            else next(fresh)
            for place, m in enumerate(mutants)
        )
    )


def write_history(
    file: str,
    digests: Mapping[str, str],
    ids: list[str],
    report: MutationReport,
    judged_by: list[list[str]],
    tests: Mapping[str, str],
) -> Path:
    """Leave for the next run the hash of each function and what became of each mutant that
    ran or was carried. A timeout is a kill here as it is in the score. Of a killed mutant the
    test files that killed it are left too (``judged_by``, in the order of the report), with
    the hash each has in ``tests``."""
    outcomes, judges = {}, {}
    for key, result, names in zip(ids, report.mutants, judged_by, strict=True):
        if result.status in (MutantStatus.KILLED, MutantStatus.TIMEOUT):
            outcomes[key] = KILLED
            judges[key] = sorted(names)
        elif result.status is MutantStatus.SURVIVED:
            outcomes[key] = SURVIVED
    used = {name for names in judges.values() for name in names}
    path = snapshot_path(file)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 2,
        "source": file,
        "forms": dict(digests),
        "outcomes": outcomes,
        "judges": judges,
        "tests": {name: tests[name] for name in used},
    }
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + NL, encoding="utf-8")
    return path


def stop_tree(pid: int) -> None:
    """End a process and what it started: a mutant that loops must not leave workers behind."""
    if sys.platform == "win32":
        run("taskkill", "/F", "/T", "/PID", str(pid))
    else:
        os.killpg(pid, signal.SIGKILL)


def fingerprint(content: bytes) -> bytes:
    return hashlib.sha256(content).digest()


class PytestRunner:
    """Runs the chosen test files in a copy of the project, as the executor asks.

    ``tests`` are the test files of the whole source file. With ``source`` and ``narrowed``, a
    copy that holds a mutant (known by the content of ``source``) is judged by the tests of the
    mutated line alone; the unchanged copy, and a mutant nobody narrowed, by all of them.
    """

    __test__ = False

    def __init__(
        self,
        tests: list[str],
        deselect: list[str],
        source: str | None = None,
        narrowed: Mapping[bytes, list[str]] | None = None,
        base_workers: int = 1,
    ) -> None:
        self.tests, self.deselect = tests, deselect
        self.source, self.narrowed = source, narrowed or {}
        self.base_workers = max(1, base_workers)
        self.base_seconds: float | None = None  # how long the unchanged copy took, once known

    def chosen(self, root: Path) -> list[str]:
        if self.source is None:
            return self.tests
        return self.narrowed.get(fingerprint((root / self.source).read_bytes()), self.tests)

    def patience(self, timeout: float | None) -> float | None:
        """How long a mutant may run. The executor gives ten times what the unchanged copy took;
        that run was spread over ``base_workers`` processes and a mutant runs in one, so a mutant
        is given that many times more: never less than ten times the run of one process."""
        if timeout is None or self.base_seconds is None:
            return timeout
        return max(timeout, TIMEOUT_FACTOR * self.base_workers * self.base_seconds)

    async def __call__(self, root: Path, timeout: float | None) -> TestVerdict:
        # The executor asks without a limit only for the unchanged copy, before any mutant. Every
        # test of the file has to pass there, and they are many: that one run is spread over
        # ``base_workers`` processes. A mutant stops at its first failure and runs in one.
        base = timeout is None and self.base_workers > 1
        # --timeout: a mutant that loops forever fails its test in seconds, instead of the
        # executor waiting ten times the whole baseline for it
        command = [
            sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
            f"-n{self.base_workers if base else 0}",
            f"--timeout={TEST_TIMEOUT}", "-m", "not slow", *self.deselect, *self.chosen(root),
        ]  # fmt: skip
        started = time.monotonic()
        # the copy's own source must be the one imported, not the installed project's
        env = {
            **os.environ,
            **TESTS_ENV,
            "PYTHONPATH": str(root / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        process = await asyncio.create_subprocess_exec(
            *command, cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=sys.platform != "win32",
        )  # fmt: skip
        try:
            code = await asyncio.wait_for(process.wait(), self.patience(timeout))
        except TimeoutError:
            stop_tree(process.pid)
            await process.wait()
            return TestVerdict.TIMED_OUT
        if code == 0:
            if timeout is None:
                self.base_seconds = time.monotonic() - started
            return TestVerdict.PASSED
        if code in (1, 2):  # a test failed, or the changed code no longer loads
            return TestVerdict.FAILED
        raise TestsDidNotRun(f"pytest exited with {code}")


def judge(mutants: list[Mutant], runner: PytestRunner, workers: int) -> MutationReport:
    with tempfile.TemporaryDirectory(prefix="forja-mutants-", ignore_cleanup_errors=True) as copies:
        return asyncio.run(run_mutants(ROOT, Path(copies), mutants, runner, workers=workers))


def judge_file(
    file: str,
    mutants: list[Mutant],
    tests: list[str],
    by_line: LinesToTests,
    workers: int,
) -> tuple[MutationReport, list[int]]:
    """The verdict on every mutant of ``file``, and the places of those judged twice.

    Each mutant is first judged by the test files that run its line: a test that never reaches
    the line cannot notice the change, and ``tests`` (every test file that touches the source
    file) is many times more than that. Killed there, it is killed by ``tests`` too, which holds
    those files. A mutant that survives there is not yet a survivor: coverage may have named the
    wrong test for a line (what runs while a fixture is set up is counted for the test before),
    so it is judged again by all of ``tests``, as every mutant used to be. The verdicts are the
    ones the whole of ``tests`` gives; only the time changes.
    """
    narrowed = {
        fingerprint(mutant.mutated): tests_of_line(by_line, file, mutant.line, tests)
        for mutant in mutants
        if mutant.covered
    }
    first = judge(mutants, PytestRunner(tests, deselected(), file, narrowed, workers), workers)
    doubted = [
        index
        for index, (mutant, result) in enumerate(zip(mutants, first.mutants, strict=True))
        if result.status is MutantStatus.SURVIVED
        and narrowed.get(fingerprint(mutant.mutated), tests) != tests
    ]
    if not doubted:
        return first, []
    whole = PytestRunner(tests, deselected(), base_workers=workers)
    again = judge([mutants[i] for i in doubted], whole, workers)
    results = list(first.mutants)
    for index, result in zip(doubted, again.mutants, strict=True):
        results[index] = result
    return MutationReport(tuple(results)), doubted


def canary() -> bool:
    """Prove the harness can tell a mutant from a failed command, before trusting any verdict.

    A tiny project is mutated twice with the same runner the gate uses: tests that check nothing
    must let the mutant survive, and tests that check the sum must kill it. A command that fails
    for its own reasons (a wrong path, a missing module) would "kill" both.
    """
    with tempfile.TemporaryDirectory(prefix="forja-canary-") as name:
        root = Path(name) / "project"
        (root / "src").mkdir(parents=True)
        (root / "tests").mkdir()
        (root / "src" / "canary.py").write_text("def add(a, b):\n    return a + b\n", "utf-8")
        head = "from canary import add\n\n\ndef test_it():\n    assert "
        (root / "tests" / "test_loose.py").write_text(head + "add(1, 2) is not None\n", "utf-8")
        (root / "tests" / "test_tight.py").write_text(head + "add(1, 2) == 3\n", "utf-8")
        mutants = mutants_of("src/canary.py", root, frozenset({2}))
        survivors = {}
        for kind in ("loose", "tight"):
            runner = PytestRunner([f"tests/test_{kind}.py"], [])
            with tempfile.TemporaryDirectory(prefix="forja-canary-copies-") as copies:
                report = asyncio.run(run_mutants(root, Path(copies), mutants, runner, workers=1))
            survivors[kind] = len(report.with_status(MutantStatus.SURVIVED))
    return bool(mutants) and survivors["loose"] >= 1 and survivors["tight"] == 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("files", nargs="*")
    parser.add_argument("--all", action="store_true")
    # the name the mutator gives it: a ceiling, never more runs than there is work for
    parser.add_argument("--max-workers", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--reuse-coverage", action="store_true")
    parser.add_argument("--skip-canary", action="store_true")
    # the two names the mutator has for which mutants run; the second is what runs without either
    parser.add_argument("--mutate-all", action="store_true")
    parser.add_argument("--since-last-run", action="store_true")
    args = parser.parse_args(argv)
    if args.mutate_all and args.since_last_run:
        parser.error("--mutate-all cannot be combined with --since-last-run")
    return args


def report_survivors(report: MutationReport, allowed: list[dict[str, str]]) -> int:
    """Print the survivors no suppression names; return how many there were."""
    count = 0
    for mutant in report.with_status(MutantStatus.SURVIVED):
        muted = any(
            s["file"] == mutant.file
            and s["function"] == mutant.function
            and s["change"] == mutant.change
            for s in allowed
        )
        if not muted:
            print(f"SURVIVED {mutant.file}:{mutant.line} {mutant.change}  [{mutant.function}]")
            count += 1
    return count


def main() -> int:
    args = parse_args()
    # a line at a time even into a pipe or a file: whoever waits an hour sees where the run is
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    files = args.files or (all_sources() if args.all else changed_sources())
    if not files:
        print("no source file changed: nothing to mutate")
        return 0
    started = time.monotonic()
    if not args.skip_canary and not canary():
        print("the canary failed: the harness cannot tell a mutant from a broken command")
        return 4
    print(f"canary: {time.monotonic() - started:.0f}s")
    started = time.monotonic()
    if not args.reuse_coverage:
        print(f"coverage by test: the fast tests, in {args.max_workers} run(s) at once...")
    tests_of, by_line = record_coverage(args.reuse_coverage, args.max_workers)
    covered = covered_lines()
    print(f"coverage by test: {time.monotonic() - started:.0f}s")
    allowed = suppressions()
    survivors, unreached = 0, 0
    for number, file in enumerate(files, start=1):
        tests = tests_of.get(file)
        if not tests:
            print(f"{file}: no test runs it, so no mutant can be judged")
            survivors += 1
            continue
        started = time.monotonic()
        mutants = mutants_of(file, ROOT, covered.get(file, frozenset()))
        source = (ROOT / file).read_text(encoding="utf-8")
        ids, digests = mutation_ids(source, file), form_digests(source, file)
        history = NO_HISTORY if args.mutate_all else load_history(file)
        hashes = test_digests({*tests, *history.tests})
        kept = carried(mutants, ids, digests, history, hashes)
        place = f"[{number}/{len(files)}] {file}"
        before = (
            f", {len(kept)} killed before, the function and its tests left as they were"
            if kept
            else ""
        )
        print(f"{place}: {len(mutants)} mutant(s){before}, {len(tests)} test file(s)...")
        to_run = [mutant for at, mutant in enumerate(mutants) if at not in kept]
        try:
            ran, twice = judge_file(file, to_run, tests, by_line, args.max_workers)
        except (BaselineFailed, TestsDidNotRun) as error:
            print(f"{file}: the tests fail before any mutation ({error})")
            return 2
        report = with_carried(mutants, kept, ran)
        fresh = iter(judges_of(file, to_run, tests, by_line, twice))
        judged_by = [
            list(history.judges[key]) if at in kept else next(fresh) for at, key in enumerate(ids)
        ]
        write_history(file, digests, ids, report, judged_by, hashes)
        took = time.monotonic() - started
        again = f", {len(twice)} judged again by every test" if twice else ""
        print(f"{place}: {len(tests)} test file(s), {took:.0f}s{again}", flush=True)
        survivors += report_survivors(report, allowed)
        missed = len(report.with_status(MutantStatus.UNCOVERED))
        if missed:
            unreached += missed
            print(f"{file}: {missed} site(s) on lines no test runs (not run)")
    print(
        "mutation: "
        + ("all mutants killed or suppressed" if not survivors else f"{survivors} survived")
    )
    if unreached:
        print(f"{unreached} site(s) on lines no test runs: write a test, or the code is not needed")
    return 3 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
