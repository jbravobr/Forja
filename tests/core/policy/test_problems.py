"""The exact words of every problem the policy loader reports, and where it says it found it.

A user fixes a configuration from these lines alone, so the text and the location are the contract.
"""

from pathlib import Path

import pytest

from forja.core.gates import InconclusivePolicy, Mode
from forja.core.policy import (
    POLICY_SCHEMA_VERSION,
    CheckRegistry,
    CheckSpec,
    PolicyConfigError,
    build_policy,
    freeze_policy,
    load_frozen_policy,
    load_policy,
    parse_layer,
)
from forja.core.policy.config import ADAPTER_OPERATIONS
from forja.core.policy.registry import LANGUAGES
from forja.core.redaction import PiiKind
from tests.core.policy.test_config import make_registry, policy

LANGUAGE_LIST = ", ".join(sorted(LANGUAGES))
OPERATION_LIST = ", ".join(sorted(ADAPTER_OPERATIONS))


def found(*toml: str) -> list[str]:
    layers = [parse_layer(f"layer {i}", text) for i, text in enumerate(toml)]
    with pytest.raises(PolicyConfigError) as info:
        build_policy(make_registry(), layers)
    return info.value.problems


CASES = {
    "a test root that leaves the repository": (
        "[tests]" + chr(10) + 'root = "../outside"',
        ["layer 0: tests.root: must be a directory inside the repository, as a POSIX path"],
    ),
    "the repository itself as the test root": (
        "[tests]" + chr(10) + 'root = "."',
        ["layer 0: tests.root: must be a directory inside the repository, as a POSIX path"],
    ),
    "a test root that is not text": (
        "[tests]" + chr(10) + "root = 3",
        ["layer 0: tests.root: must be a directory inside the repository, as a POSIX path"],
    ),
    "an unknown key under tests": (
        '[tests]\nname = "x"',
        ["layer 0: tests.name: unknown key"],
    ),
    "checks not a table": ("checks = 1", ["layer 0: checks: must be a table"]),
    "a check that is not a table, and the next one is still read": (
        "[checks]\nbuild = 1\ncomplexity = 2",
        [
            "layer 0: checks.build: must be a table",
            "layer 0: checks.complexity: must be a table",
        ],
    ),
    "a bad inconclusive policy": (
        '[checks.build]\non_inconclusive = "maybe"',
        [
            "layer 0: checks.build.on_inconclusive: 'maybe' is not one of: "
            + ", ".join(m.value for m in InconclusivePolicy)
        ],
    ),
    "a bad mode": (
        '[checks.build]\nmode = "loud"',
        ["layer 0: checks.build.mode: 'loud' is not one of: " + ", ".join(m.value for m in Mode)],
    ),
    "a promotion that is not a table": (
        "[checks.complexity]\npromotion = 3",
        ["layer 0: checks.complexity.promotion: must be a table with exactly 'date' and 'reason'"],
    ),
    "a promotion with a bad date": (
        '[checks.complexity]\npromotion = { date = "soon", reason = "r" }',
        ["layer 0: checks.complexity.promotion.date: must be a date such as 2026-10-10"],
    ),
    "a promotion without a reason": (
        '[checks.complexity]\npromotion = { date = 2026-10-10, reason = " " }',
        ["layer 0: checks.complexity.promotion.reason: must say why the check was promoted"],
    ),
    "thresholds not a table": (
        "[checks.complexity]\nthresholds = 1",
        ["layer 0: checks.complexity.thresholds: must be a table of languages"],
    ),
    "thresholds of an unknown language, and the next language is still read": (
        "[checks.complexity.thresholds]\ncobol = { max_complexity = 1 }\npython = 3",
        [
            f"layer 0: checks.complexity.thresholds.cobol: unknown language; use: {LANGUAGE_LIST}",
            "layer 0: checks.complexity.thresholds.python: must be a table",
        ],
    ),
    "thresholds of a language that is not a table, and the next is still read": (
        "[checks.complexity.thresholds]\npython = 3\ngo = 4",
        [
            "layer 0: checks.complexity.thresholds.python: must be a table",
            "layer 0: checks.complexity.thresholds.go: must be a table",
        ],
    ),
    "commands not a table": (
        "[checks.build]\ncommands = 1",
        ["layer 0: checks.build.commands: must be a table"],
    ),
    "a command with a part that is not a string": (
        '[checks.build.commands]\nbuild = ["make", 1]',
        [
            "layer 0: checks.build.commands.build: "
            "must be a non-empty list of strings (no shell string)"
        ],
    ),
    "limits not a table": (
        "[checks.complexity]\nlimits = 1",
        ["layer 0: checks.complexity.limits: must be a table"],
    ),
    "a limit the check does not have": (
        "[checks.complexity.limits]\nmax_lines = 1",
        ["layer 0: checks.complexity.limits.max_lines: this check has no such limit"],
    ),
    "a negative limit": (
        "[checks.complexity.limits]\nmax_files = -1",
        ["layer 0: checks.complexity.limits.max_files: must be a finite number, zero or more"],
    ),
    "sensitive paths that are not strings": (
        "[sensitive]\npaths = [1]",
        ["layer 0: sensitive.paths: must be a list of strings"],
    ),
    "a branch name with a space": (
        '[branches]\nprotected = ["a b"]',
        ["layer 0: branches.protected: invalid branch name 'a b'"],
    ),
    "redaction patterns not a table": (
        "[redaction]\npatterns = 3",
        ["layer 0: redaction.patterns: must be a table of name = regular expression"],
    ),
    "redaction allow list with an item that is too long": (
        f'[redaction]\nallow = ["{"x" * 501}"]',
        [
            "layer 0: redaction.allow: "
            "must be a list of non-empty strings, each up to 500 characters"
        ],
    ),
    "redaction pii of an unknown kind": (
        '[redaction]\npii = ["passport"]',
        [
            "layer 0: redaction.pii: must be a list drawn from: "
            + ", ".join(sorted(kind.value for kind in PiiKind))
        ],
    ),
    "redaction entropy threshold below the range": (
        "[redaction]\nentropy_threshold = -0.1",
        ["layer 0: redaction.entropy_threshold: must be a number between 0 and 8"],
    ),
    "redaction entropy threshold above the range": (
        "[redaction]\nentropy_threshold = 8.1",
        ["layer 0: redaction.entropy_threshold: must be a number between 0 and 8"],
    ),
    "an unknown language in adapters, and the next language is still read": (
        "[adapters.cobol]\n[adapters.python]\ncommands = 3",
        [
            f"layer 0: adapters.cobol: unknown language; use: {LANGUAGE_LIST}",
            "layer 0: adapters.python.commands: must be a table",
        ],
    ),
    "adapters.javascript, which no adapter answers to, and the next language is still read": (
        '[adapters.javascript.commands]\ne2e = ["npm", "run", "e2e"]\n'
        "[adapters.python]\ncommands = 3",
        [
            "layer 0: adapters.javascript: "
            "no adapter has this name; use typescript, which serves JavaScript",
            "layer 0: adapters.python.commands: must be a table",
        ],
    ),
    "an adapter that is not a table, and the next one is still read": (
        "[adapters]\npython = 3\n[adapters.go]\ncommands = 3",
        [
            "layer 0: adapters.python: must be a table",
            "layer 0: adapters.go.commands: must be a table",
        ],
    ),
    "adapter commands that are not tables, both reported": (
        "[adapters.python]\ncommands = 3\n[adapters.go]\ncommands = 4",
        [
            "layer 0: adapters.python.commands: must be a table",
            "layer 0: adapters.go.commands: must be a table",
        ],
    ),
    "an adapter command for an operation that does not exist": (
        '[adapters.python.commands]\ndeploy = ["x"]',
        [f"layer 0: adapters.python.commands.deploy: unknown operation; use: {OPERATION_LIST}"],
    ),
    "an adapter command that is a shell string": (
        '[adapters.python.commands]\nbuild = "make"',
        [
            "layer 0: adapters.python.commands.build: "
            "must be a non-empty list of strings (no shell string)"
        ],
    ),
    "a promotion without a decision record": (
        '[checks.complexity]\nmode = "blocking"\non_inconclusive = "block"',
        [
            "checks.complexity: promoting a check from observation to blocking needs a recorded "
            "decision: promotion = { date = ..., reason = ... }"
        ],
    ),
}


@pytest.mark.parametrize("name", CASES)
def test_each_problem_is_reported_with_its_exact_words_and_place(name: str) -> None:
    toml, expected = CASES[name]

    assert found(toml) == expected


def test_a_pattern_that_does_not_compile_is_reported_under_its_name() -> None:
    (problem,) = found('[redaction.patterns]\nbad = "("')

    assert problem.startswith("layer 0: redaction.patterns.bad: pattern 'bad' is not a valid")


def test_an_allow_pattern_that_does_not_compile_is_reported() -> None:
    (problem,) = found('[redaction]\nallow_patterns = ["("]')

    assert problem.startswith("layer 0: redaction.allow_patterns: '(' is not a valid regular")


def test_the_edges_of_the_accepted_ranges_are_accepted() -> None:
    result = policy(
        f'[redaction]\nallow = ["{"x" * 500}"]\nentropy_threshold = 0\n'
        "[checks.complexity.limits]\nmax_files = 0\n",
        "[redaction]\nentropy_threshold = 8\n[checks.complexity.limits]\nmax_files = 1\n",
    )

    assert result.redaction.entropy_threshold == 8
    assert result.checks["complexity"].limits == {"max_files": 1}


def test_all_the_problems_are_in_one_error_one_per_line() -> None:
    with pytest.raises(PolicyConfigError) as info:
        build_policy(make_registry(), [parse_layer("L", "[sensitive]\npaths = [1]\nzzz = 1")])

    assert str(info.value) == (
        "invalid policy configuration:\n- L: sensitive.zzz: unknown key\n"
        "- L: sensitive.paths: must be a list of strings"
    )


def test_the_effective_policy_carries_the_schema_version_and_check_ids() -> None:
    result = policy()

    assert result.to_dict()["schema_version"] == POLICY_SCHEMA_VERSION
    assert result.checks["build"].id == "build"


def test_an_adapter_without_commands_is_valid_and_adds_nothing() -> None:
    assert policy("[adapters.python]\n").adapter_commands == {}


def test_a_layer_that_does_not_mention_a_check_does_not_hide_the_layers_after_it() -> None:
    result = policy("[branches]\nprotected = []", "[checks.complexity.limits]\nmax_files = 7")

    assert result.checks["complexity"].limits == {"max_files": 7}


# -- loading files -----------------------------------------------------------------------------


def test_a_missing_optional_file_is_skipped_and_the_next_layer_is_still_read(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "forja.toml"
    repo.write_text("[checks.complexity.limits]\nmax_files = 4\n", encoding="utf-8")

    result = load_policy(make_registry(), user=tmp_path / "none.toml", repo=repo)

    assert result.checks["complexity"].limits == {"max_files": 4}
    assert load_policy(make_registry(), repo=tmp_path / "absent.toml").checks


def test_a_missing_execution_file_is_an_error_that_names_it(tmp_path: Path) -> None:
    missing = tmp_path / "run.toml"

    with pytest.raises(PolicyConfigError) as info:
        load_policy(make_registry(), execution=missing)

    assert info.value.problems == [f"execution file: {missing} does not exist"]


def test_a_problem_in_a_file_names_the_layer_and_the_path(tmp_path: Path) -> None:
    user, repo = tmp_path / "user.toml", tmp_path / "forja.toml"
    user.write_text("[sensitive]\npaths = [1]\n", encoding="utf-8")
    repo.write_text("[branches]\nprotected = [1]\n", encoding="utf-8")

    with pytest.raises(PolicyConfigError) as info:
        load_policy(make_registry(), user=user, repo=repo)

    assert info.value.problems == [
        f"user profile ({user}): sensitive.paths: must be a list of strings",
        f"repository forja.toml ({repo}): branches.protected: must be a list of strings",
    ]


def test_a_file_that_is_not_toml_names_its_layer(tmp_path: Path) -> None:
    execution = tmp_path / "run.toml"
    execution.write_text("this is = = not toml", encoding="utf-8")

    with pytest.raises(PolicyConfigError) as info:
        load_policy(make_registry(), execution=execution)

    assert info.value.problems[0].startswith(f"execution file ({execution}): not valid TOML")


# -- the frozen file ---------------------------------------------------------------------------


def test_a_frozen_policy_that_no_longer_fits_the_code_names_the_frozen_layer(
    tmp_path: Path,
) -> None:
    freeze_policy(policy(), tmp_path, "1.0.0")
    smaller = make_registry()
    smaller._specs.pop("raw_draft")  # noqa: SLF001 - a registry that lost a check

    with pytest.raises(PolicyConfigError) as info:
        load_frozen_policy(smaller, tmp_path)

    assert info.value.problems == ["frozen policy: checks.raw_draft: unknown check"]


def test_a_frozen_policy_edited_after_the_fact_is_refused_with_both_reasons(
    tmp_path: Path,
) -> None:
    version = freeze_policy(policy(), tmp_path, "1.0.0")
    path = tmp_path / "policy.json"
    path.write_text(path.read_text().replace('"build"', '"build"', 1).replace("[]", '["x"]', 1))

    with pytest.raises(PolicyConfigError) as info:
        load_frozen_policy(make_registry(), tmp_path)

    assert info.value.problems == [
        f"frozen policy {path} does not match its version {version}: the file was edited, "
        "or the checks it refers to changed since the run started"
    ]


def test_a_forja_version_with_a_plus_in_it_still_round_trips(tmp_path: Path) -> None:
    version = freeze_policy(policy(), tmp_path, "1.0+dev")

    assert load_frozen_policy(make_registry(), tmp_path).version == version
    assert version.startswith("1.0+dev+")


def _with_voice() -> CheckRegistry:
    registry = make_registry()
    registry.register(CheckSpec("voice", 12, Mode.OBSERVATION, InconclusivePolicy.PREMISE))
    registry.register(CheckSpec("comments", 12, Mode.OBSERVATION, InconclusivePolicy.PREMISE))
    return registry


@pytest.mark.parametrize("check", ["voice", "comments"])
def test_the_voice_checks_cannot_be_made_blocking(check: str) -> None:
    layer = parse_layer(
        "layer 0", f'[checks.{check}]\nmode = "blocking"\non_inconclusive = "block"'
    )

    with pytest.raises(PolicyConfigError) as info:
        build_policy(_with_voice(), [layer])

    assert info.value.problems == [
        f"layer 0: checks.{check}.mode: the voice never blocks: the Forja instructs the agent and "
        "reports what it measured (see docs/guia/documentacao-gerada.md)"
    ]


def test_the_voice_checks_can_still_be_set_to_observation() -> None:
    layer = parse_layer("layer 0", '[checks.voice]\nmode = "observation"\n[checks.comments]')

    built = build_policy(_with_voice(), [layer])

    assert built.checks["voice"].settings.mode is Mode.OBSERVATION
    assert built.checks["comments"].settings.mode is Mode.OBSERVATION


def test_other_checks_are_still_promotable_to_blocking() -> None:
    toml = (
        '[checks.complexity]\nmode = "blocking"\non_inconclusive = "block"\n'
        'promotion = { date = 2026-10-10, reason = "r" }'
    )

    built = build_policy(_with_voice(), [parse_layer("layer 0", toml)])

    assert built.checks["complexity"].settings.mode is Mode.BLOCKING
