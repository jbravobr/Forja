"""Five detectors that answered "nothing found" to something they could not see (review item 27)."""

import asyncio
import sys
from pathlib import Path

import pytest

from forja.checks.base import Undecided
from forja.checks.complexity import read_spans
from forja.checks.readme import Chunk, _satisfied, evaluate
from forja.checks.voice_markers import MARKERS
from forja.core.adapters import Parsed, ToolOutput
from forja.core.adapters.common import keep_worst
from forja.core.events import EventLog
from forja.core.gates import Inconclusive, InconclusiveReason
from forja.core.redaction.pii import cpf_valid
from forja.core.states import ExecutionState, Level
from forja.languages.csharp import parsers as csharp
from forja.languages.java import parsers as java
from tests.checks.support import RUN
from tests.checks.test_units import tools
from tests.core.events.support import FakeClock
from tests.core.redaction.test_pii import out


@pytest.fixture
def log(tmp_path: Path) -> EventLog:
    opened = EventLog.open(tmp_path / "events.jsonl", RUN, clock=FakeClock())
    opened.append_transition(Level.EXECUTION, RUN, None, ExecutionState.PREPARING)
    return opened


# -- lizard's report in a form the reader does not know --------------------------------------------

ROW = '12,3,40,2,14,"f@1-14@m.py","m.py","f","f( a, b )",1,14'


def spans_of(log: EventLog, tmp_path: Path, *printed: str) -> object:
    script = "print(" + repr("\n".join(printed)) + ")"
    lizard = (sys.executable, "-c", script)
    return asyncio.run(read_spans(tools(log, tmp_path), lizard, tmp_path, ("m.py",)))


def test_a_report_of_lizard_with_the_columns_it_has_today_is_read(
    log: EventLog, tmp_path: Path
) -> None:
    spans = spans_of(log, tmp_path, ROW)

    assert isinstance(spans, dict) and [key for key in spans] == [("m.py", "f")]


def test_a_report_of_lizard_with_other_columns_is_not_read_as_no_function(
    log: EventLog, tmp_path: Path
) -> None:
    with pytest.raises(Undecided) as stop:
        spans_of(log, tmp_path, ROW + ",extra", ROW + ",extra")

    result = stop.value.result
    assert isinstance(result, Inconclusive) and result.reason is InconclusiveReason.TOOL_FAILED
    assert result.detail == (
        "complexity: lizard printed its report in a form this reader does not know"
    )


def test_a_file_without_functions_and_a_warning_of_lizard_are_still_no_function(
    log: EventLog, tmp_path: Path
) -> None:
    assert spans_of(log, tmp_path, "") == {}
    assert spans_of(log, tmp_path, "warning: m.py, skipped") == {}


# -- a README heading that only holds the letters of a section -------------------------------------

README = (
    "# Tool\n\nWhat it is.\n\n## Build\n\ntext\n\n## Tests\n\ntext\n\n## {heading}\n\ntext\n\n"
    "## Configuration\n\ntext\n\n## Dependencies\n\ntext\n"
)


def missing(heading: str) -> list[str]:
    problems = evaluate(README.format(heading=heading), lambda path: True)
    return [problem.section for problem in problems]


@pytest.mark.parametrize("heading", ["Trunk", "Prune", "Abuso", "Rerun history"])
def test_a_heading_that_only_holds_the_letters_of_run_is_not_the_run_section(heading: str) -> None:
    assert missing(heading) == ["run"]


@pytest.mark.parametrize(
    "heading", ["Run", "Running it", "How to run", "Como rodar", "Execução", "Uso", "Usage"]
)
def test_a_heading_with_a_word_about_running_is_the_run_section(heading: str) -> None:
    assert missing(heading) == []


def test_latest_is_not_the_test_section() -> None:
    text = "# Tool\n\nWhat it is.\n\n## Latest\n\ntext\n"

    assert "test" in [problem.section for problem in evaluate(text, lambda path: True)]


# -- personal data written without its usual separators --------------------------------------------

CPF = "529.982.247-25"  # the example every validator is tested with
assert cpf_valid(CPF)


@pytest.mark.parametrize("written", ["529 982 247 25", "529 982 247-25", "529.982.247 25"])
def test_a_cpf_written_with_blanks_is_masked(written: str) -> None:
    assert out(f"cpf {written} ok") == "cpf [PII:cpf:1] ok"


def test_digits_apart_that_are_no_cpf_are_left_alone() -> None:
    assert out("sizes 100 200 300 40 ok") == "sizes 100 200 300 40 ok"


def test_a_mobile_number_with_nothing_between_its_digits_is_masked() -> None:
    assert out("tel 11987654321 ok") == "tel [PII:phone:1] ok"


@pytest.mark.parametrize(
    "text",
    [
        "id 10987654321 ok",  # 10 is no area code
        "id 11887654321 ok",  # no 9 where a mobile number has it
        "id 119876543210 ok",  # twelve digits
        "v1.11987654321 ok",  # the end of a longer number
    ],
)
def test_eleven_digits_that_are_no_mobile_number_are_left_alone(text: str) -> None:
    assert out(text) == text


# -- a marker of generated text written with a curly apostrophe ------------------------------------


def marker(name: str):  # type: ignore[no-untyped-def]
    (found,) = [m for m in MARKERS if m.id == name]
    return found


@pytest.mark.parametrize("apostrophe", ["'", "’", "‘", "ʼ"])
def test_a_marker_is_found_whatever_apostrophe_it_was_written_with(apostrophe: str) -> None:
    assert marker("announcement").found_in(f"It{apostrophe}s worth noting that x is 1.")
    assert marker("inflated").found_in(f"In today{apostrophe}s fast-paced world")


def test_text_without_a_marker_is_still_without_one() -> None:
    assert not marker("announcement").found_in("It’s the first row of the table.")


# -- two test cases with one name ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "second", "kept"),
    [
        ("failed", "passed", "failed"),
        ("passed", "failed", "failed"),
        ("skipped", "passed", "passed"),
        ("passed", "skipped", "passed"),
        ("failed", "skipped", "failed"),
    ],
)
def test_of_two_outcomes_under_one_name_the_worse_is_kept(
    first: str, second: str, kept: str
) -> None:
    outcomes: dict[str, str] = {}
    keep_worst(outcomes, "t", first)
    keep_worst(outcomes, "t", second)

    assert outcomes == {"t": kept}


def outcomes_of(result: object) -> dict[str, object]:
    assert isinstance(result, Parsed), result
    return {d.target.id: d.value for d in result.drafts if d.measurement == "test.outcome"}


def test_a_csharp_case_that_failed_is_not_hidden_by_a_case_of_the_same_name_that_passed(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "test.log"
    log_path.write_text(
        "[out]   Failed Acme.CalcTests.Adds [3 ms]\n"
        "[out]   Passed Acme.CalcTests.Adds [1 ms]\n"
        "[out]   Passed Acme.CalcTests.Subs [1 ms]\n",
        encoding="utf-8",
    )

    result = csharp.parse_tests(ToolOutput(1, log_path, tmp_path))

    assert outcomes_of(result) == {"Acme.CalcTests.Adds": "failed", "Acme.CalcTests.Subs": "passed"}


@pytest.mark.parametrize("gradle", [False, True])
def test_a_java_case_that_failed_is_not_hidden_by_a_case_of_the_same_name_that_passed(
    tmp_path: Path, gradle: bool
) -> None:
    reports = tmp_path / ("build/test-results/test" if gradle else "target/surefire-reports")
    reports.mkdir(parents=True)
    (reports / "TEST-com.acme.CalcTest.xml").write_text(
        '<testsuite name="com.acme.CalcTest" tests="3">'
        '<testcase classname="com.acme.CalcTest" name="adds"><failure>no</failure></testcase>'
        '<testcase classname="com.acme.CalcTest" name="adds"/>'
        '<testcase classname="com.acme.CalcTest" name="subs"/>'
        "</testsuite>",
        encoding="utf-8",
    )
    log_path = tmp_path / "test.log"
    log_path.write_text("", encoding="utf-8")

    result = java.parse_tests(ToolOutput(1, log_path, tmp_path), gradle=gradle)

    assert outcomes_of(result) == {
        "com.acme.CalcTest.adds": "failed",
        "com.acme.CalcTest.subs": "passed",
    }


# -- two changes to these detectors that no test noticed (mutation of item 27) ---------------------


def test_a_line_of_lizard_with_four_fields_is_a_warning_and_one_more_is_a_row(
    log: EventLog, tmp_path: Path
) -> None:
    assert spans_of(log, tmp_path, "warning,m.py,skipped,twice") == {}

    with pytest.raises(Undecided):
        spans_of(log, tmp_path, "one,two,three,four,five")


def test_the_text_before_the_first_heading_is_about_no_section() -> None:
    assert _satisfied(Chunk(None, "how to run it"), "run", "run") is False
    assert _satisfied(Chunk("How to run it", ""), "run", "run") is True
