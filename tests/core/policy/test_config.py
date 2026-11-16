import asyncio
from pathlib import Path

import pytest

from forja.core.gates import (
    Approved,
    GatePipeline,
    InconclusivePolicy,
    Mode,
    Verdict,
)
from forja.core.policy import (
    CheckRegistry,
    CheckSpec,
    EffectivePolicy,
    PolicyConfigError,
    Promotion,
    build_policy,
    load_policy,
    parse_layer,
)
from forja.core.policy.config import SECTION_KEYS
from tests.core.gates.support import FakeCheck, make_input


def make_registry() -> CheckRegistry:
    registry = CheckRegistry()
    registry.register(
        CheckSpec(
            "build", 4, Mode.BLOCKING, InconclusivePolicy.BLOCK, commands=frozenset({"build"})
        )
    )
    registry.register(
        CheckSpec(
            "complexity",
            8,
            Mode.OBSERVATION,
            InconclusivePolicy.PREMISE,
            thresholds=frozenset({"max_complexity"}),
            limits=frozenset({"max_files"}),
        )
    )
    registry.register(CheckSpec("raw_draft", 9, Mode.OBSERVATION))
    return registry


def policy(*toml: str) -> EffectivePolicy:
    layers = [parse_layer(f"layer {i}", text) for i, text in enumerate(toml)]
    return build_policy(make_registry(), layers)


def problems(*toml: str) -> str:
    with pytest.raises(PolicyConfigError) as info:
        policy(*toml)
    return "\n".join(info.value.problems)


# -- the registry ------------------------------------------------------------------------------


def test_a_check_that_starts_blocking_must_declare_what_inconclusive_means() -> None:
    with pytest.raises(ValueError, match="on_inconclusive"):
        CheckSpec("x", 1, Mode.BLOCKING)


def test_check_ids_and_gate_numbers_are_validated_and_ids_are_unique() -> None:
    for bad in ("Build", "1x", "a-b", ""):
        with pytest.raises(ValueError, match="invalid check id"):
            CheckSpec(bad, 1, Mode.OBSERVATION)
    with pytest.raises(ValueError, match="start at 1"):
        CheckSpec("x", 0, Mode.OBSERVATION)
    with pytest.raises(ValueError, match="already registered"):
        make_registry().register(CheckSpec("build", 4, Mode.OBSERVATION))


# -- defaults and precedence -------------------------------------------------------------------


def test_with_no_configuration_every_check_has_its_code_defaults() -> None:
    result = policy()

    assert result.checks["build"].settings.mode is Mode.BLOCKING
    assert result.checks["build"].settings.on_inconclusive is InconclusivePolicy.BLOCK
    assert result.checks["complexity"].settings.mode is Mode.OBSERVATION
    assert result.checks["raw_draft"].settings.on_inconclusive is None
    assert result.checks["complexity"].promotion is None


def test_later_layers_override_earlier_ones_value_by_value() -> None:
    user = """
[checks.complexity.thresholds.python]
max_complexity = 10
[checks.complexity.limits]
max_files = 5
[checks.build.commands]
build = ["make"]
"""
    repo = """
[checks.complexity.thresholds.python]
max_complexity = 12
[checks.complexity.thresholds.go]
max_complexity = 15
"""
    execution = """
[checks.complexity.thresholds.python]
max_complexity = 20
"""

    complexity = policy(user, repo, execution).checks["complexity"]

    assert complexity.thresholds == {"python": {"max_complexity": 20}, "go": {"max_complexity": 15}}
    assert complexity.limits == {"max_files": 5}
    assert policy(user).checks["build"].commands == {"build": ("make",)}


def test_values_are_what_a_check_receives_as_its_input() -> None:
    values = policy(
        '[checks.build.commands]\nbuild = ["uv", "build"]\n'
        "[checks.complexity.limits]\nmax_files = 3\n"
    ).values()

    assert values["build"] == {
        "thresholds": {},
        "commands": {"build": ["uv", "build"]},
        "limits": {},
    }
    assert values["complexity"]["limits"] == {"max_files": 3}


def test_a_blocking_check_can_be_relaxed_to_observation_without_a_decision_record() -> None:
    relaxed = policy('[checks.build]\nmode = "observation"\n').checks["build"]

    assert relaxed.settings.mode is Mode.OBSERVATION
    assert relaxed.settings.on_inconclusive is InconclusivePolicy.BLOCK
    assert relaxed.promotion is None


# -- promotion ---------------------------------------------------------------------------------


def test_promoting_a_check_to_blocking_needs_a_recorded_decision() -> None:
    message = problems('[checks.complexity]\nmode = "blocking"\n')

    assert "checks.complexity" in message and "recorded decision" in message


def test_a_recorded_promotion_makes_the_check_blocking() -> None:
    toml = """
[checks.complexity]
mode = "blocking"
promotion = { date = 2026-10-10, reason = "calibrated on 40 reviewed PRs" }
"""

    complexity = policy(toml).checks["complexity"]

    assert complexity.settings.mode is Mode.BLOCKING
    assert complexity.promotion == Promotion("2026-10-10", "calibrated on 40 reviewed PRs")


def test_the_decision_may_be_written_as_a_string_and_spread_over_layers() -> None:
    first = (
        '[checks.complexity]\nmode = "blocking"\npromotion = { date = "2026-10-10", reason = "ok" }'
    )
    second = '[checks.complexity.promotion]\ndate = "2026-11-01"\nreason = "recalibrated"'

    promotion = policy(first, second).checks["complexity"].promotion

    assert promotion == Promotion("2026-11-01", "recalibrated")


def test_a_promotion_without_a_promotion_is_refused() -> None:
    on_blocking_by_default = '[checks.build.promotion]\ndate = "2026-10-10"\nreason = "x"'
    on_still_observed = '[checks.complexity.promotion]\ndate = "2026-10-10"\nreason = "x"'

    for toml in (on_blocking_by_default, on_still_observed):
        assert "only valid on a check promoted to blocking" in problems(toml)


def test_a_check_without_a_default_policy_needs_one_to_be_promoted() -> None:
    toml = """
[checks.raw_draft]
mode = "blocking"
promotion = { date = 2026-10-10, reason = "calibrated" }
"""

    assert "on_inconclusive" in problems(toml)
    assert (
        policy(toml + 'on_inconclusive = "premise"\n').checks["raw_draft"].settings.mode
        is Mode.BLOCKING
    )


@pytest.mark.parametrize(
    "promotion",
    [
        'promotion = { date = "10/10/2026", reason = "x" }',
        'promotion = { date = 2026-10-10, reason = "  " }',
        "promotion = { date = 2026-10-10 }",
        'promotion = { date = 2026-10-10, reason = "x", by = "me" }',
        'promotion = "yes"',
    ],
)
def test_a_malformed_promotion_is_refused(promotion: str) -> None:
    assert "promotion" in problems(f'[checks.complexity]\nmode = "blocking"\n{promotion}')


# -- validation of the layers ------------------------------------------------------------------


def test_every_problem_is_reported_at_once_with_the_layer_that_has_it() -> None:
    bad = """
[nonsense]
x = 1
[checks.ghost]
mode = "observation"
[checks.complexity]
mode = "sometimes"
speed = 3
[checks.complexity.thresholds.cobol]
max_complexity = 1
[checks.complexity.thresholds.python]
max_complexity = true
unknown_threshold = 2
[checks.complexity.limits]
max_files = -1
[checks.build.commands]
build = "make all"
other = ["x"]
"""

    message = problems(bad)

    for expected in (
        "layer 0: nonsense: unknown section",
        "checks.ghost: unknown check",
        "checks.complexity.mode: 'sometimes' is not one of: blocking, observation",
        "checks.complexity.speed: unknown key",
        "checks.complexity.thresholds.cobol: unknown language",
        "checks.complexity.thresholds.python.max_complexity: must be a finite number",
        "checks.complexity.thresholds.python.unknown_threshold: this check has no such threshold",
        "checks.complexity.limits.max_files: must be a finite number, zero or more",
        "checks.build.commands.build: must be a non-empty list of strings (no shell string)",
        "checks.build.commands.other: this check has no such command",
    ):
        assert expected in message, expected


def test_numbers_must_be_finite_and_not_booleans() -> None:
    assert "finite number" in problems("[checks.complexity.limits]\nmax_files = inf\n")
    assert "finite number" in problems("[checks.complexity.limits]\nmax_files = nan\n")
    assert "finite number" in problems("[checks.complexity.limits]\nmax_files = false\n")


@pytest.mark.parametrize(
    ("check", "number", "name"),
    [
        ("duplication", 7, "min_tokens"),
        ("duplication", 7, "min_lines"),
        ("mutation", 5, "workers"),
        ("mutation", 7, "line_tests"),
        ("scope", 1, "max_files"),
        ("scope", 1, "max_lines"),
        ("voice", 12, "max_dashes"),
    ],
)
def test_whole_limits_must_be_whole_numbers(check: str, number: int, name: str) -> None:
    registry = CheckRegistry()
    registry.register(
        CheckSpec(
            check,
            number,
            Mode.OBSERVATION,
            InconclusivePolicy.PREMISE,
            limits=frozenset({name}),
        )
    )

    def load(text: str) -> EffectivePolicy:
        return build_policy(registry, [parse_layer("layer 0", text)])

    with pytest.raises(PolicyConfigError) as info:
        load(f"[checks.{check}.limits]\n{name} = 50.5\n")
    assert f"checks.{check}.limits.{name}: must be a whole number" in info.value.problems[0]
    assert load(f"[checks.{check}.limits]\n{name} = 50\n").checks[check].limits == {name: 50}


def test_toml_that_cannot_be_parsed_is_reported_with_its_layer() -> None:
    with pytest.raises(PolicyConfigError, match=r"layer 7: not valid TOML"):
        parse_layer("layer 7", "[checks")


def test_a_layer_is_validated_even_if_a_later_layer_would_override_the_mistake() -> None:
    assert "checks.ghost" in problems('[checks.ghost]\nmode = "blocking"', "")


# -- loading from files ------------------------------------------------------------------------


def test_files_are_read_by_precedence_and_missing_optional_ones_are_skipped(tmp_path: Path) -> None:
    user = tmp_path / "user.toml"
    user.write_text("[checks.complexity.limits]\nmax_files = 1\n")
    repo = tmp_path / "forja.toml"
    repo.write_text("[checks.complexity.limits]\nmax_files = 2\n")
    execution = tmp_path / "execucao.toml"
    execution.write_text("[checks.complexity.limits]\nmax_files = 3\n")
    registry = make_registry()

    assert load_policy(registry, user=user).checks["complexity"].limits == {"max_files": 1}
    assert load_policy(registry, user=user, repo=repo).checks["complexity"].limits == {
        "max_files": 2
    }
    full = load_policy(registry, user=user, repo=repo, execution=execution)
    assert full.checks["complexity"].limits == {"max_files": 3}
    assert load_policy(registry, user=tmp_path / "absent.toml", repo=None) == load_policy(registry)


def test_an_execution_file_that_was_asked_for_must_exist(tmp_path: Path) -> None:
    with pytest.raises(PolicyConfigError, match="does not exist"):
        load_policy(make_registry(), execution=tmp_path / "missing.toml")


def test_errors_from_a_file_name_the_file(tmp_path: Path) -> None:
    repo = tmp_path / "forja.toml"
    repo.write_text('[checks.ghost]\nmode = "blocking"\n')

    with pytest.raises(PolicyConfigError, match=r"repository forja\.toml \(.*forja\.toml\)"):
        load_policy(make_registry(), repo=repo)


# -- version -----------------------------------------------------------------------------------


def test_the_version_changes_with_any_value_and_not_with_formatting_or_order() -> None:
    a = policy("[checks.complexity.limits]\nmax_files = 5\n[checks.build.commands]\nbuild = ['a']")
    reordered = policy(
        "[checks.build.commands]\nbuild = ['a']\n\n[checks.complexity.limits]\nmax_files = 5"
    )
    changed = policy(
        "[checks.complexity.limits]\nmax_files = 6\n[checks.build.commands]\nbuild = ['a']"
    )

    assert a.version("1.2.3") == reordered.version("1.2.3")
    assert a.version("1.2.3") != changed.version("1.2.3")
    assert a.version("1.2.3") != a.version("1.2.4")
    version = a.version("1.2.3")
    assert version.startswith("1.2.3+") and len(version.split("+")[1]) == 12


# -- the policy drives the pipeline ------------------------------------------------------------


def test_the_effective_policy_feeds_the_gate_pipeline(tmp_path: Path) -> None:
    effective = policy('[checks.build]\nmode = "observation"\n')
    check = FakeCheck("build", 4, Approved())
    pipeline = GatePipeline([check], effective.settings(), effective.values())

    outcome = asyncio.run(pipeline.run(make_input(tmp_path), set()))

    assert outcome.verdict is Verdict.PASSED
    assert outcome.runs[0].settings.mode is Mode.OBSERVATION
    assert check.calls[0].values == effective.checks["build"].values()


# -- sensitive files and protected branches ----------------------------------------------------


def test_repositories_add_sensitive_patterns_and_protected_branches_across_layers() -> None:
    user = '[sensitive]\npaths = ["*.secret"]\n[branches]\nprotected = ["release"]\n'
    repo = '[sensitive]\npaths = ["vault/**", "*.secret"]\n[branches]\nprotected = ["trunk"]\n'

    result = policy(user, repo)

    assert result.sensitive_paths == ("*.secret", "vault/**")
    assert result.protected_branches == ("release", "trunk")
    assert policy().sensitive_paths == () and policy().protected_branches == ()


def test_these_lists_only_add_they_never_replace_or_remove_the_defaults() -> None:
    from forja.core.isolation import DEFAULT_SENSITIVE_PATTERNS, SensitivePaths

    result = policy('[sensitive]\npaths = ["*.secret"]\n')

    paths = SensitivePaths.with_defaults(result.sensitive_paths, case_insensitive=False)
    assert paths.matches("a.secret") and paths.matches(".env")
    assert set(DEFAULT_SENSITIVE_PATTERNS) <= set(paths.patterns)


@pytest.mark.parametrize(
    ("toml", "expected"),
    [
        ('[sensitive]\npaths = "*.pem"\n', "sensitive.paths: must be a list of strings"),
        ("[sensitive]\npaths = [1]\n", "sensitive.paths: must be a list of strings"),
        ('[sensitive]\npaths = ["dir/"]\n', "invalid sensitive-file pattern"),
        ('[sensitive]\npaths = ["../up"]\n', "invalid sensitive-file pattern"),
        ('[sensitive]\nfiles = ["x"]\n', "sensitive.files: unknown key"),
        ("sensitive = 3\n", "sensitive: must be a table"),
        ('[branches]\nprotected = ["has space"]\n', "invalid branch name"),
        ('[branches]\nprotected = ["-force"]\n', "invalid branch name"),
        ('[branches]\nprotect = ["x"]\n', "branches.protect: unknown key"),
    ],
)
def test_invalid_sensitive_and_branch_settings_are_reported(toml: str, expected: str) -> None:
    assert expected in problems(toml)


@pytest.mark.parametrize("section", ["sensitive", "branches", "licenses"])
def test_a_list_section_accepts_only_the_key_of_section_keys(section: str) -> None:
    (key,) = SECTION_KEYS[section]

    policy(f'[{section}]\n{key} = ["x"]\n')
    assert f"{section}.other: unknown key" in problems(f'[{section}]\nother = ["x"]\n')
    assert f"{section}: must be a table" in problems(f"{section} = 3\n")


def test_the_new_settings_change_the_policy_version() -> None:
    plain = policy().version("1.0.0")

    assert policy('[branches]\nprotected = ["trunk"]\n').version("1.0.0") != plain
    assert policy('[sensitive]\npaths = ["*.secret"]\n').version("1.0.0") != plain


# -- the repository's own commands for an adapter ----------------------------------------------


def test_a_repository_can_give_its_own_command_for_an_operation() -> None:
    result = policy(
        '[adapters.python.commands]\ntest = ["uv", "run", "pytest", "-q"]\nbuild = ["make"]\n'
    )

    assert result.adapter_commands == {
        "python": {"test": ("uv", "run", "pytest", "-q"), "build": ("make",)}
    }
    assert policy().adapter_commands == {}


def test_adapter_commands_merge_operation_by_operation_across_layers() -> None:
    user = '[adapters.python.commands]\ntest = ["pytest"]\nbuild = ["make"]\n'
    repo = (
        '[adapters.python.commands]\ntest = ["uv", "run", "pytest"]\n'
        '[adapters.go.commands]\ntest = ["go", "test"]\n'
    )

    result = policy(user, repo)

    assert result.adapter_commands == {
        "python": {"test": ("uv", "run", "pytest"), "build": ("make",)},
        "go": {"test": ("go", "test")},
    }


@pytest.mark.parametrize(
    ("toml", "expected"),
    [
        ('[adapters.cobol.commands]\ntest = ["x"]\n', "adapters.cobol: unknown language"),
        ('[adapters.python.commands]\ndeploy = ["x"]\n', "unknown operation"),
        (
            '[adapters.python.commands]\nmutation = ["x"]\n',
            "adapters.python.commands.mutation: unknown operation",
        ),
        (
            '[adapters.python.commands]\ntest = "pytest -q"\n',
            "non-empty list of strings (no shell string)",
        ),
        ("[adapters.python.commands]\ntest = []\n", "non-empty list of strings"),
        ("[adapters.python.commands]\ntest = [1]\n", "non-empty list of strings"),
        ('[adapters.python]\nflags = ["x"]\n', "adapters.python.flags: unknown key"),
        ("adapters = 3\n", "adapters: must be a table of languages"),
        ("[adapters]\npython = 3\n", "adapters.python: must be a table"),
    ],
)
def test_invalid_adapter_commands_are_reported(toml: str, expected: str) -> None:
    assert expected in problems(toml)


def test_the_operations_the_policy_accepts_are_the_ones_the_adapters_have() -> None:
    from forja.core.adapters import Operation
    from forja.core.policy.config import ADAPTER_OPERATIONS

    own = {Operation.MUTATION, Operation.TEST_LINES}
    assert {
        operation.value for operation in Operation if operation not in own
    } == ADAPTER_OPERATIONS


def test_adapter_commands_change_the_policy_version() -> None:
    assert policy('[adapters.python.commands]\ntest = ["pytest"]\n').version(
        "1.0.0"
    ) != policy().version("1.0.0")


# -- redaction settings ------------------------------------------------------------------------


def test_without_a_redaction_section_the_defaults_apply() -> None:
    from forja.core.redaction import RedactionSettings

    assert policy().redaction == RedactionSettings()


def test_a_repository_adds_patterns_and_allowances_and_picks_its_personal_data_detectors() -> None:
    from forja.core.redaction import PiiKind

    toml = r"""
[redaction]
allow = ["public-demo-value"]
allow_patterns = ["demo-.*"]
pii = ["cpf", "email"]
entropy_threshold = 3
[redaction.patterns]
internal-id = 'corp-id-(\d{8})'
"""

    settings = policy(toml).redaction

    assert settings.allow == ("public-demo-value",) and settings.allow_patterns == ("demo-.*",)
    assert settings.pii == {PiiKind.CPF, PiiKind.EMAIL}
    assert settings.entropy_threshold == 3.0
    assert settings.patterns == (("internal-id", r"corp-id-(\d{8})"),)


def test_redaction_settings_add_up_across_layers_except_the_detector_list_and_threshold() -> None:
    from forja.core.redaction import PiiKind

    user = (
        '[redaction]\nallow = ["a"]\npii = ["cpf"]\nentropy_threshold = 3\n'
        '[redaction.patterns]\none = "x1"\n'
    )
    repo = (
        '[redaction]\nallow = ["b", "a"]\npii = ["email", "phone"]\n'
        '[redaction.patterns]\ntwo = "x2"\n'
    )

    settings = policy(user, repo).redaction

    assert settings.allow == ("a", "b")
    assert dict(settings.patterns) == {"one": "x1", "two": "x2"}
    assert settings.pii == {PiiKind.EMAIL, PiiKind.PHONE}  # the later layer replaces the list
    assert settings.entropy_threshold == 3.0  # and the earlier threshold stays when not overridden


@pytest.mark.parametrize(
    ("toml", "expected"),
    [
        ("redaction = 3\n", "redaction: must be a table"),
        ("[redaction]\nsecrets = false\n", "redaction.secrets: unknown key"),
        ('[redaction]\npatterns = "x"\n', "must be a table of name = regular expression"),
        ('[redaction.patterns]\nBad_Name = "x1"\n', "invalid pattern name"),
        ('[redaction.patterns]\nok = "("\n', "not a valid regular expression"),
        ('[redaction.patterns]\nok = "(a)(b)"\n', "more than one capture group"),
        ('[redaction.patterns]\nok = "a*"\n', "matches the empty string"),
        ('[redaction]\nallow = "x"\n', "redaction.allow: must be a list of non-empty strings"),
        ('[redaction]\nallow = [""]\n', "redaction.allow: must be a list of non-empty strings"),
        ('[redaction]\nallow_patterns = ["("]\n', "is not a valid regular expression"),
        ('[redaction]\npii = ["passport"]\n', "redaction.pii: must be a list drawn from"),
        ("[redaction]\nentropy_threshold = 9\n", "must be a number between 0 and 8"),
        ("[redaction]\nentropy_threshold = true\n", "must be a number between 0 and 8"),
        ('[redaction]\nentropy_threshold = "high"\n', "must be a number between 0 and 8"),
    ],
)
def test_invalid_redaction_settings_are_reported(toml: str, expected: str) -> None:
    assert expected in problems(toml)


def test_redaction_settings_change_the_policy_version() -> None:
    assert policy('[redaction]\nallow = ["x"]\n').version("1.0.0") != policy().version("1.0.0")


def test_the_policy_builds_a_working_redactor() -> None:
    from forja.core.redaction import ItemKind, PackageItem, Redactor

    effective = policy(r"""
[redaction.patterns]
internal-id = 'corp-id-(\d{8})'
""")

    result = Redactor(effective.redaction).redact(
        [PackageItem(ItemKind.TEXT, "t", "x corp-id-12345678")]
    )

    assert result.items[0].content == "x corp-id-[REDACTED:custom-internal-id:1]"


def test_where_the_test_author_writes_comes_from_the_latest_layer_and_defaults_to_none() -> None:
    assert policy().test_root is None
    layers = [
        parse_layer("a", '[tests]\nroot = "spec"'),
        parse_layer("b", '[tests]\nroot = "t/unit"'),
    ]

    assert build_policy(make_registry(), layers).test_root == "t/unit"


def test_a_policy_without_a_test_root_keeps_its_digest_and_one_with_it_changes() -> None:
    plain = policy()
    with_root = build_policy(make_registry(), [parse_layer("a", '[tests]\nroot = "spec"')])

    assert "tests" not in plain.to_dict()
    assert with_root.to_dict()["tests"] == {"root": "spec"}
    assert with_root.digest() != plain.digest()


# -- forbidden licenses ------------------------------------------------------------------------


def test_the_forbidden_licenses_are_the_repositorys_and_add_up_across_layers() -> None:
    user = '[licenses]\nforbidden = ["GPL-3.0-only"]\n'
    repo = '[licenses]\nforbidden = ["AGPL-3.0-only", "GPL-3.0-only"]\n'

    assert policy(user, repo).forbidden_licenses == ("GPL-3.0-only", "AGPL-3.0-only")
    assert policy().forbidden_licenses == ()  # the Forja has no list of its own


@pytest.mark.parametrize(
    "text",
    [
        '[licenses]\nforbidden = "GPL"\n',
        "[licenses]\nforbidden = [1]\n",
        '[licenses]\nforbidden = ["a b"]\n',
        '[licenses]\nforbidden = [" "]\n',
        '[licenses]\nallowed = ["MIT"]\n',
        "licenses = 1\n",
    ],
)
def test_a_license_list_that_is_not_one_is_refused(text: str) -> None:
    with pytest.raises(PolicyConfigError):
        policy(text)


# -- architecture layers -----------------------------------------------------------------------

TOP = '[[architecture.layers]]\nname = "cli"\npaths = ["src/cli"]\n'
BOTTOM = '[[architecture.layers]]\nname = "core"\npaths = ["src/core", "src/shared"]\n'


def test_the_layers_add_up_across_layers_in_the_order_they_were_read() -> None:
    effective = policy(TOP, BOTTOM)

    assert [(layer.name, layer.paths) for layer in effective.architecture] == [
        ("cli", ("src/cli",)),
        ("core", ("src/core", "src/shared")),
    ]
    assert policy().architecture == ()  # the Forja deduces none


def test_the_layers_of_one_file_keep_their_order() -> None:
    effective = policy(BOTTOM + TOP)

    assert [layer.name for layer in effective.architecture] == ["core", "cli"]


def test_a_policy_without_layers_keeps_its_digest_and_one_with_them_changes() -> None:
    plain = policy()
    layered = policy(TOP)

    assert "architecture" not in plain.to_dict()
    assert layered.to_dict()["architecture"] == {"layers": [{"name": "cli", "paths": ["src/cli"]}]}
    assert layered.digest() != plain.digest()
    assert policy(TOP + BOTTOM).digest() != policy(BOTTOM + TOP).digest()


def test_a_layer_name_used_twice_is_refused_whichever_file_declared_it() -> None:
    same = '[[architecture.layers]]\nname = "cli"\npaths = ["other"]\n'

    assert "'cli' is used twice" in problems(TOP + same)
    assert "'cli' is used twice" in problems(TOP, same)


@pytest.mark.parametrize(
    "text",
    [
        "architecture = 1\n",
        "[architecture]\nlayer = []\n",
        '[architecture]\nlayers = "cli"\n',
        "[architecture]\nlayers = [1]\n",
        '[[architecture.layers]]\npaths = ["a"]\n',
        '[[architecture.layers]]\nname = " "\npaths = ["a"]\n',
        '[[architecture.layers]]\nname = 1\npaths = ["a"]\n',
        '[[architecture.layers]]\nname = "a"\n',
        '[[architecture.layers]]\nname = "a"\npaths = []\n',
        '[[architecture.layers]]\nname = "a"\npaths = "a"\n',
        '[[architecture.layers]]\nname = "a"\npaths = [1]\n',
        '[[architecture.layers]]\nname = "a"\npaths = ["../a"]\n',
        '[[architecture.layers]]\nname = "a"\npaths = ["/a"]\n',
        '[[architecture.layers]]\nname = "a"\npaths = ["a/"]\n',
        '[[architecture.layers]]\nname = "a"\npaths = ["a\\\\b"]\n',
        '[[architecture.layers]]\nname = "a"\npaths = ["."]\n',
        '[[architecture.layers]]\nname = "a"\npaths = ["a"]\nextra = 1\n',
    ],
)
def test_layers_that_are_not_well_formed_are_refused(text: str) -> None:
    with pytest.raises(PolicyConfigError):
        policy(text)


def test_a_refused_layer_says_where_it_is() -> None:
    text = problems('[[architecture.layers]]\nname = "a"\npaths = ["../a"]\n')

    assert "architecture.layers[0].paths" in text and "'../a'" in text


def test_the_license_list_is_part_of_the_digest_only_when_it_is_set() -> None:
    assert "licenses" not in policy().to_dict()
    assert policy('[licenses]\nforbidden = ["GPL-3.0-only"]\n').to_dict()["licenses"] == {
        "forbidden": ["GPL-3.0-only"]
    }
    assert policy().digest() != policy('[licenses]\nforbidden = ["GPL-3.0-only"]\n').digest()
