"""What the agent is told: the task, the rules, the files it may change, and why it was rejected."""

from dataclasses import replace
from pathlib import Path

from forja.core.artifacts.models import Producer
from forja.core.redaction import Redactor
from forja.orchestrator.agent_runner import DIARY_ORDER, _fence, build_package, diary_document
from forja.orchestrator.ports import AttemptContext, TaskInfo
from forja.orchestrator.voice_brief import brief

TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def context(tmp_path: Path, scope: tuple[str, ...], rejection: str | None = None) -> AttemptContext:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    task = TaskInfo("t1", "t1-s", "reduce the complexity of f", scope, 3, 120)
    return AttemptContext(task, "t1-a1", tmp_path, rejection)


def package(tmp_path: Path, scope: tuple[str, ...] = ("src/a.py",), **more: str | None) -> str:
    return build_package(context(tmp_path, scope, **more), Redactor())


def test_the_package_names_the_task_the_scope_and_the_limits(tmp_path: Path) -> None:
    text = package(tmp_path)

    assert "reduce the complexity of f" in text
    assert "Only inside: `src/a.py`." in text
    assert "At most 3 files and 120 changed lines." in text


def test_the_package_tells_the_agent_what_it_cannot_do_and_how_to_declare_its_work(
    tmp_path: Path,
) -> None:
    text = package(tmp_path)

    assert "You cannot run commands" in text
    assert "Do not change tests" in text
    assert ".forja-agent/saida.json" in text
    assert '"change": "modified"' in text


def test_the_files_in_scope_are_in_the_package_as_they_are(tmp_path: Path) -> None:
    text = package(tmp_path)

    assert "### `src/a.py`\n\n```\ndef f():\n    return 1\n\n```" in text


def test_a_secret_in_a_file_never_reaches_the_package(tmp_path: Path) -> None:
    context(tmp_path, ("src/a.py",))
    (tmp_path / "src" / "a.py").write_text(f'KEY = "{TOKEN}"\n', encoding="utf-8")

    assert TOKEN not in package(tmp_path)


def test_a_sensitive_file_is_withheld_and_said_to_be(tmp_path: Path) -> None:
    context(tmp_path, ("src/a.py",))
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")

    text = package(tmp_path, ("src/a.py", ".env"))

    assert "`.env` was withheld (sensitive_file)." in text
    assert "A=1" not in text


def test_a_directory_in_the_scope_is_named_and_not_pasted(tmp_path: Path) -> None:
    text = package(tmp_path, ("src",))

    assert "`src` is a directory: read the files you need from it." in text
    assert "return 1" not in text


def test_the_reason_for_the_last_rejection_is_in_the_package_only_when_there_was_one(
    tmp_path: Path,
) -> None:
    assert "previous attempt" not in package(tmp_path)

    text = package(tmp_path, rejection="lint: 2 new F401 violation(s) [src/a.py]")

    assert "## The previous attempt was rejected\n\nlint: 2 new F401 violation(s)" in text


def test_a_fence_is_longer_than_any_run_of_backticks_inside() -> None:
    assert _fence("plain") == "```"
    assert _fence("a ``` b") == "````"
    assert _fence("a ```` b") == "`````"


def test_the_agent_is_told_it_cannot_run_commands_unless_the_facade_is_on(tmp_path: Path) -> None:
    plain = build_package(context(tmp_path, ("src/a.py",)), Redactor())
    facade = build_package(context(tmp_path, ("src/a.py",)), Redactor(), facade=True)

    assert "You cannot run commands. " in plain and "forja agente" not in plain
    assert "except exactly `forja agente lint`" in facade


def test_the_files_section_is_there_for_files_and_for_directories_and_only_then(
    tmp_path: Path,
) -> None:
    assert "## Files in scope" in package(tmp_path)
    assert "## Files in scope" in package(tmp_path, ("src",))
    assert "## Files in scope" not in package(tmp_path, ())


def fix_context(
    tmp_path: Path, role: Producer, rejection: str | None = None, test_root: str = "tests"
) -> AttemptContext:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    intent = "page 1 is wrong\n\nCriteria:\n1. page 1 is the first items"
    task = TaskInfo(
        "t1", "t1-t", intent, ("src/a.py",), 4, 200, role, test_root, frozen_digest="abc"
    )
    return AttemptContext(task, "t1-ta1", tmp_path, rejection)


def test_the_test_author_is_told_to_write_failing_tests_under_the_test_root(
    tmp_path: Path,
) -> None:
    text = build_package(fix_context(tmp_path, Producer.TEST_AUTHOR), Redactor())

    assert "1. page 1 is the first items" in text
    assert "Only under `tests`. The production files below are for reading." in text
    assert "must FAIL" in text
    assert "an import, a syntax or a collection" in text
    assert '"tests": [{"criterion_id": 1, "test_id": "tests/test_x.py::test_name"' in text
    assert "### `src/a.py`" in text
    assert "refactoring" not in text


def test_the_implementer_of_a_fix_is_told_the_tests_are_frozen(tmp_path: Path) -> None:
    text = build_package(fix_context(tmp_path, Producer.IMPLEMENTER), Redactor())

    assert "are frozen" in text and "must pass" in text
    assert "anything\nunder `tests`" in text
    assert "Only inside: `src/a.py`." in text
    assert "refactoring" not in text


def test_without_a_test_root_the_agents_are_told_to_use_the_project_test_files(
    tmp_path: Path,
) -> None:
    author = build_package(fix_context(tmp_path, Producer.TEST_AUTHOR, test_root=""), Redactor())
    fixer = build_package(fix_context(tmp_path, Producer.IMPLEMENTER, test_root=""), Redactor())

    assert "Only in the project's test files. The production files" in author
    assert "only in the project's test files." in author
    assert "The tests in the project's test files were written" in fixer
    assert "anything\nin the project's test files" in fixer


def test_a_rejected_test_author_is_shown_the_verdict(tmp_path: Path) -> None:
    rejection = "the declared test passes on the code with the defect [tests/test_x.py::t]"

    text = build_package(fix_context(tmp_path, Producer.TEST_AUTHOR, rejection), Redactor())

    assert "## The previous attempt was rejected" in text and rejection in text


def test_a_refactoring_package_keeps_its_own_instructions(tmp_path: Path) -> None:
    text = package(tmp_path)

    assert "You are refactoring existing code" in text
    assert "frozen" not in text and "must FAIL" not in text


def test_earlier_attempts_are_a_file_to_read_before_acting_and_only_when_there_are_some(
    tmp_path: Path,
) -> None:
    assert "Earlier attempts" not in package(tmp_path, rejection="lint: no")

    ctx = replace(
        context(tmp_path, ("src/a.py",), "lint: no"),
        diary="### Attempt `t1-a1`: rejected\nYou said: split f\n",
    )
    text = build_package(ctx, Redactor())

    assert f"## Earlier attempts of this stage\n\n{DIARY_ORDER}\n" in text
    assert "Before you do anything else, read `.forja-agent/diary.md`" in text
    assert "split f" not in text
    assert text.index("## Earlier attempts") < text.index("## The previous attempt was rejected")


def test_the_last_rejection_comes_with_what_the_agent_said_whole(tmp_path: Path) -> None:
    said = "first line\n\nsecond " + "w" * 3000
    ctx = replace(context(tmp_path, ("src/a.py",), "lint: no"), previous_said=said)

    text = build_package(ctx, Redactor())

    assert "lint: no\n\nWhat you said in that attempt:\n\n> first line\n>\n> second " in text
    assert "w" * 3000 in text and "..." not in text


def test_the_hypothesis_request_comes_after_the_earlier_attempts_and_only_when_asked(
    tmp_path: Path,
) -> None:
    assert "hypothesis" not in package(tmp_path, rejection="lint: no")

    ctx = replace(
        context(tmp_path, ("src/a.py",), "lint: no"),
        diary="- `t1-a1`: rejected\n",
        hypothesis="State the hypothesis.",
    )
    text = build_package(ctx, Redactor())

    assert "## Attack the hypothesis\n\nState the hypothesis." in text
    order = [
        text.index(title)
        for title in (
            "## Earlier attempts",
            "## Attack the hypothesis",
            "## The previous attempt was rejected",
        )
    ]
    assert order == sorted(order)


def test_a_secret_in_what_the_agent_said_never_reaches_the_package(tmp_path: Path) -> None:
    ctx = replace(context(tmp_path, ("src/a.py",), "lint: no"), previous_said=f"key {TOKEN}")

    assert TOKEN not in build_package(ctx, Redactor())


def test_a_secret_in_the_reason_of_the_rejection_never_reaches_the_package(tmp_path: Path) -> None:
    rejected = f"gate 5 (tests): test_login failed\nE  assert login({TOKEN!r})"

    text = package(tmp_path, rejection=rejected)

    assert TOKEN not in text
    assert "## The previous attempt was rejected" in text
    assert "gate 5 (tests): test_login failed" in text
    assert "Why the last attempt was rejected" not in text  # the name of the item is not a file


def test_a_secret_in_the_diary_file_never_reaches_it(tmp_path: Path) -> None:
    ctx = replace(context(tmp_path, ("src/a.py",)), diary=f"You said: key {TOKEN}\n")

    assert TOKEN not in diary_document(ctx, Redactor())


def test_the_diary_file_is_the_diary_as_it_is_and_empty_without_one(tmp_path: Path) -> None:
    ctx = context(tmp_path, ("src/a.py",))

    assert diary_document(ctx, Redactor()) == ""
    assert diary_document(replace(ctx, diary="a\n" + "b" * 9000), Redactor()) == "a\n" + "b" * 9000


def test_the_voice_instruction_is_in_the_package_of_every_role_that_writes_text(
    tmp_path: Path,
) -> None:
    base = context(tmp_path, ("src/a.py",))
    for role in (
        Producer.IMPLEMENTER,
        Producer.TEST_AUTHOR,
        Producer.SPECIFIER,
        Producer.ARCHAEOLOGIST,
        Producer.DOCUMENTER,
    ):
        ctx = replace(base, task=replace(base.task, role=role))

        text = build_package(ctx, Redactor(), voice=brief())

        assert "## Voice" in text, role
        assert "You write comments" in text, role


def test_the_voice_instruction_comes_as_given_not_redacted_and_only_once(tmp_path: Path) -> None:
    ctx = context(tmp_path, ("src/a.py",))

    text = build_package(ctx, Redactor(), voice=f"Never write {TOKEN}.")

    assert text.count("## Voice") == 1
    assert f"Never write {TOKEN}." in text


def test_without_a_voice_instruction_the_package_has_no_voice_section(tmp_path: Path) -> None:
    assert "## Voice" not in package(tmp_path)


def test_the_duplication_instruction_goes_only_to_the_roles_that_change_production_code(
    tmp_path: Path,
) -> None:
    base = context(tmp_path, ("src/a.py",))
    for role, given in (
        (Producer.IMPLEMENTER, True),
        (Producer.TEST_AUTHOR, False),
        (Producer.SPECIFIER, False),
        (Producer.ARCHAEOLOGIST, False),
        (Producer.DOCUMENTER, False),
    ):
        ctx = replace(base, task=replace(base.task, role=role))

        text = build_package(ctx, Redactor(), dry="Do not repeat code.")

        assert ("## Duplication" in text) is given, role


def test_the_property_instruction_goes_only_to_the_test_author(tmp_path: Path) -> None:
    base = context(tmp_path, ("src/a.py",))
    for role, given in (
        (Producer.IMPLEMENTER, False),
        (Producer.TEST_AUTHOR, True),
        (Producer.SPECIFIER, False),
        (Producer.ARCHAEOLOGIST, False),
        (Producer.DOCUMENTER, False),
    ):
        ctx = replace(base, task=replace(base.task, role=role))

        text = build_package(ctx, Redactor(), properties=f"Never write {TOKEN}.")

        assert ("## Property tests" in text) is given, role
        assert (f"Never write {TOKEN}." in text) is given, role


def test_without_a_property_instruction_the_package_has_no_such_section(tmp_path: Path) -> None:
    base = context(tmp_path, ("src/a.py",))
    ctx = replace(base, task=replace(base.task, role=Producer.TEST_AUTHOR))

    assert "## Property tests" not in build_package(ctx, Redactor())


def test_a_fix_gets_the_duplication_instruction_and_it_comes_as_given(tmp_path: Path) -> None:
    base = context(tmp_path, ("src/a.py",))
    ctx = replace(base, task=replace(base.task, frozen_digest="abc"))

    text = build_package(ctx, Redactor(), dry=f"Never write {TOKEN}.")

    assert text.count("## Duplication") == 1
    assert f"Never write {TOKEN}." in text


def test_without_a_duplication_instruction_the_package_has_no_duplication_section(
    tmp_path: Path,
) -> None:
    assert "## Duplication" not in package(tmp_path)


def test_a_task_with_no_scope_says_any_production_file_may_change(tmp_path: Path) -> None:
    text = package(tmp_path, scope=())

    assert "Any production file." in text
    assert "Only inside:" not in text


def test_every_template_that_takes_assumptions_shows_their_shape() -> None:
    from forja.orchestrator import agent_runner

    templates = [
        agent_runner.INSTRUCTIONS,
        agent_runner.AUTHOR_INSTRUCTIONS,
        agent_runner.FIX_INSTRUCTIONS,
        agent_runner.DOCUMENTER_INSTRUCTIONS,
        agent_runner.SPECIFIER_INSTRUCTIONS,
        agent_runner.ARCHAEOLOGIST_INSTRUCTIONS,
    ]

    for template in templates:
        assert '"missing_evidence"' in template


def test_every_template_that_shows_the_actions_forbids_the_keys_it_does_not_show() -> None:
    from forja.orchestrator import agent_runner

    templates = [
        agent_runner.INSTRUCTIONS,
        agent_runner.AUTHOR_INSTRUCTIONS,
        agent_runner.FIX_INSTRUCTIONS,
        agent_runner.DOCUMENTER_INSTRUCTIONS,
    ]

    for template in templates:
        assert "Use no key that is not in the shape" in template


def test_the_fix_and_the_test_author_may_add_a_dependency_only_when_nothing_available_serves() -> (
    None
):
    from forja.orchestrator import agent_runner

    for template in (agent_runner.AUTHOR_INSTRUCTIONS, agent_runner.FIX_INSTRUCTIONS):
        assert "do not add dependencies" not in template.lower()
        assert (
            "Prefer the standard library and the packages the project already declares" in template
        )
        assert "why nothing already available serves" in template


def test_a_refactoring_still_adds_no_dependency() -> None:
    from forja.orchestrator import agent_runner

    assert "do not add dependencies" in agent_runner.INSTRUCTIONS


def test_the_documenter_instructions_name_every_section_of_the_readme_model() -> None:
    from forja.checks.readme import MODEL
    from forja.orchestrator import agent_runner

    for name, _ in MODEL:
        assert f"`{name}`" in agent_runner.README_SECTIONS
    assert "{model}" in agent_runner.DOCUMENTER_INSTRUCTIONS


def test_the_documenter_asks_only_for_the_sections_the_gate_requires_and_for_short_text() -> None:
    from forja.checks.readme import MODEL
    from forja.orchestrator import agent_runner

    required = [name for name, answers in MODEL if answers]
    optional = [name for name, answers in MODEL if not answers]

    assert required
    for name in required:
        assert f"`{name}`" in agent_runner.README_REQUIRED
    for name in optional:
        assert f"`{name}`" not in agent_runner.README_REQUIRED
    assert "{required}" in agent_runner.DOCUMENTER_INSTRUCTIONS
    assert "as short as it can be and still true" in agent_runner.DOCUMENTER_INSTRUCTIONS
