import json
from typing import Any

import pytest
from pydantic import ValidationError

from forja.core.artifacts import (
    Action,
    ChangeDeclaration,
    DeclaredChange,
    DeclaredFile,
    InvalidLlmOutput,
    Mismatch,
    compare_with_diff,
    parse_llm_output,
)
from forja.core.manifest import ManifestDiff

VALID: dict[str, Any] = {
    "claims": [
        {
            "statement": "parse() is only called from cli.py",
            "citations": [
                {"kind": "file", "path": "src/cli.py", "line_start": 10, "line_end": 12},
                {"kind": "evidence", "evidence_id": "ev1"},
                {"kind": "fact", "fact_id": "fact1"},
            ],
        }
    ],
    "actions": [
        {
            "summary": "extract parser",
            "reason": "reduce the size of cli.py",
            "files": [
                {"path": "src/parser.py", "change": "created"},
                {"path": "src/cli.py", "change": "modified"},
            ],
        }
    ],
    "assumptions": [
        {
            "statement": "Python 3.12 is the minimum",
            "reason": "pyproject says so",
            "missing_evidence": "no CI matrix",
        }
    ],
}


def problems(data: Any) -> list[str]:
    with pytest.raises(InvalidLlmOutput) as info:
        parse_llm_output(json.dumps(data))
    return info.value.problems


def test_a_valid_answer_is_parsed_into_the_three_kinds_of_content() -> None:
    output = parse_llm_output(json.dumps(VALID).encode())

    assert len(output.claims[0].citations) == 3
    assert output.actions[0].files[1].change is DeclaredChange.MODIFIED
    assert output.assumptions[0].missing_evidence == "no CI matrix"


def test_an_empty_answer_is_valid_because_a_role_may_have_nothing_to_say() -> None:
    assert parse_llm_output("{}").claims == ()


def test_text_that_is_not_json_is_rejected_with_a_clear_message() -> None:
    with pytest.raises(InvalidLlmOutput, match="not valid JSON"):
        parse_llm_output("Sure! Here is my answer")


def test_a_claim_without_evidence_is_rejected() -> None:
    data = {"claims": [{"statement": "it is fine", "citations": []}]}

    assert any("citations" in p and "at least 1" in p for p in problems(data))


def test_anything_beyond_the_three_kinds_is_rejected() -> None:
    assert any("opinion" in p for p in problems({"opinion": "looks good"}))
    data = {"claims": [{"statement": "x", "citations": [{"kind": "fact", "fact_id": "f", "n": 1}]}]}
    assert problems(data)


def test_an_action_must_name_the_files_it_touched() -> None:
    data = {"actions": [{"summary": "s", "reason": "r", "files": []}]}

    assert any("files" in p for p in problems(data))


def test_an_assumption_must_say_which_evidence_was_missing() -> None:
    data = {"assumptions": [{"statement": "s", "reason": "r"}]}

    assert any("missing_evidence" in p for p in problems(data))


@pytest.mark.parametrize("text", ["", "   "])
def test_blank_statements_are_rejected(text: str) -> None:
    data = {"claims": [{"statement": text, "citations": [{"kind": "fact", "fact_id": "f"}]}]}

    assert problems(data)


@pytest.mark.parametrize(("start", "end"), [(0, 1), (1, 0), (5, 4), (-1, 3)])
def test_line_ranges_must_be_positive_and_ordered(start: int, end: int) -> None:
    citation = {"kind": "file", "path": "a.py", "line_start": start, "line_end": end}
    data = {"claims": [{"statement": "s", "citations": [citation]}]}

    assert problems(data)


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../x.py",
        "a/../../x.py",
        "a\\b.py",
        "C:/x.py",
        "c:x.py",
        "",
        "a//b.py",
        "./a.py",
        "a/",
    ],
)
def test_paths_must_stay_inside_the_repository(path: str) -> None:
    file_citation = {"kind": "file", "path": path, "line_start": 1, "line_end": 1}
    claim = {"claims": [{"statement": "s", "citations": [file_citation]}]}
    action = {
        "actions": [{"summary": "s", "reason": "r", "files": [{"path": path, "change": "created"}]}]
    }

    assert problems(claim)
    assert problems(action)


def test_every_problem_is_listed_so_the_model_can_fix_them_all_at_once() -> None:
    data = {
        "claims": [{"statement": "a", "citations": []}],
        "actions": [{"summary": "s", "reason": "r", "files": []}],
        "extra": 1,
    }

    assert len(problems(data)) == 3


# -- declared files against the real diff ------------------------------------------------------


def declaration(*files: tuple[str, DeclaredChange]) -> ChangeDeclaration:
    return ChangeDeclaration(
        actions=(
            Action(
                summary="s",
                reason="r",
                files=tuple(DeclaredFile(path=p, change=c) for p, c in files),
            ),
        )
    )


C, M, R = DeclaredChange.CREATED, DeclaredChange.MODIFIED, DeclaredChange.REMOVED


def test_declared_files_that_match_the_diff_produce_no_mismatch() -> None:
    declared = declaration(("a.py", C), ("b.py", M), ("c.py", R))
    diff = ManifestDiff(created=("a.py",), modified=("b.py",), removed=("c.py",))

    assert compare_with_diff(declared, diff) == []


def test_a_changed_file_that_was_not_declared_is_reported() -> None:
    declared = declaration(("a.py", C))
    diff = ManifestDiff(created=("a.py",), modified=("sneaky.py",), removed=())

    assert compare_with_diff(declared, diff) == [Mismatch("sneaky.py", None, M)]


def test_a_declared_file_that_was_not_changed_is_reported() -> None:
    declared = declaration(("a.py", C), ("ghost.py", M))
    diff = ManifestDiff(created=("a.py",), modified=(), removed=())

    assert compare_with_diff(declared, diff) == [Mismatch("ghost.py", M, None)]


def test_declaring_the_wrong_kind_of_change_is_reported() -> None:
    declared = declaration(("a.py", M))
    diff = ManifestDiff(created=("a.py",), modified=(), removed=())

    assert compare_with_diff(declared, diff) == [Mismatch("a.py", M, C)]


def test_a_file_declared_under_two_kinds_never_matches() -> None:
    declared = declaration(("a.py", C), ("a.py", M))
    diff = ManifestDiff(created=("a.py",), modified=(), removed=())

    assert [m.path for m in compare_with_diff(declared, diff)] == ["a.py"]


def test_no_declaration_and_no_diff_agree() -> None:
    assert compare_with_diff(ChangeDeclaration(), ManifestDiff((), (), ())) == []


def test_a_citation_of_a_single_line_is_valid() -> None:
    citation = {"kind": "file", "path": "a.py", "line_start": 5, "line_end": 5}

    parsed = parse_llm_output(json.dumps({"claims": [{"statement": "s", "citations": [citation]}]}))

    (read,) = parsed.claims[0].citations
    assert (read.line_start, read.line_end) == (5, 5)  # type: ignore[union-attr]


def test_a_citation_cannot_be_changed_after_it_was_read() -> None:
    from forja.core.artifacts.llm_output import FileCitation  # noqa: PLC0415

    citation = FileCitation(kind="file", path="a.py", line_start=1, line_end=2)

    with pytest.raises(ValidationError, match="frozen"):
        citation.path = "b.py"


def test_an_empty_path_is_not_a_path_inside_the_repository() -> None:
    from forja.core.artifacts.llm_output import check_relative_path  # noqa: PLC0415

    with pytest.raises(ValueError, match="not a relative POSIX path"):
        check_relative_path("")


def test_the_tests_a_role_declares_are_parsed_and_default_to_none() -> None:
    declared = {"tests": [{"criterion_id": 1, "test_id": "tests/a.py::t", "path": "tests/a.py"}]}

    assert parse_llm_output(json.dumps(declared)).tests[0].test_id == "tests/a.py::t"
    assert parse_llm_output("{}").tests == ()


@pytest.mark.parametrize("path", ["../a.py", "/a.py", "a" + chr(92) + "b.py"])
def test_a_declared_test_must_stay_inside_the_repository(path: str) -> None:
    declared = {"tests": [{"criterion_id": 1, "test_id": "a::t", "path": path}]}

    assert any("tests" in p for p in problems(declared))
