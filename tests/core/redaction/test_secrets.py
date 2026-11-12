import pytest

from forja.core.redaction import (
    ItemKind,
    PackageItem,
    PiiKind,
    RedactionResult,
    RedactionSettings,
    Redactor,
    rules_fingerprint,
    rules_version,
)
from tests.core.redaction.corpus import INNOCENT, personal_data, secrets

PLANTED = secrets()


def run(
    text: str, *, settings: RedactionSettings | None = None, name: str = "note"
) -> RedactionResult:
    return Redactor(settings).redact([PackageItem(ItemKind.TEXT, name, text)])


def out(text: str, **kwargs: object) -> str:
    return run(text, **kwargs).items[0].content  # type: ignore[arg-type]


def everything_that_could_leak(result: RedactionResult) -> str:
    """Every piece of a result except the redacted text itself."""
    return " ".join(
        [str(result.findings), str(result.dropped), str(result.assumptions), result.rules_version]
    )


# -- every rule finds its secret ---------------------------------------------------------------


@pytest.mark.parametrize("planted", PLANTED, ids=lambda p: p.rule)
def test_each_secret_is_masked_and_named_by_its_rule(planted) -> None:  # type: ignore[no-untyped-def]
    text = "a = 1\n" + planted.text + "\nb = 2\n"

    result = run(text)

    content = result.items[0].content
    assert planted.secret not in content
    assert f"[REDACTED:{planted.rule}:1]" in content
    assert [f.rule for f in result.findings] == [planted.rule]
    assert result.findings[0].line == 2
    assert content.startswith("a = 1\n") and content.endswith("\nb = 2\n")


@pytest.mark.parametrize("planted", PLANTED, ids=lambda p: p.rule)
def test_nothing_but_the_masked_content_ever_holds_the_secret(planted) -> None:  # type: ignore[no-untyped-def]
    result = run(planted.text)

    assert planted.secret not in everything_that_could_leak(result)


def test_every_planted_secret_and_personal_datum_disappears_from_a_whole_package() -> None:
    """The acceptance test: a known set of fixtures, all of it gone from everything sent."""
    items = [
        PackageItem(ItemKind.FILE, f"src/part{i}.py", f"# {i}\n" + p.text + "\n")
        for i, p in enumerate(PLANTED)
    ]
    items += [
        PackageItem(ItemKind.EVIDENCE, f"ev{i}", p.text.format(value=p.value))
        for i, p in enumerate(personal_data())
    ]

    result = Redactor().redact(items)

    sent = "\n".join(item.content for item in result.items) + everything_that_could_leak(result)
    for planted in PLANTED:
        assert planted.secret not in sent, planted.rule
    for datum in personal_data():
        assert datum.value not in sent, datum.kind
    assert len(result.items) == len(items) and result.dropped == ()
    assert {f.rule for f in result.findings} >= {p.rule for p in PLANTED}


# -- text that must stay exactly as it is ------------------------------------------------------


@pytest.mark.parametrize(("name", "text"), INNOCENT, ids=[n for n, _ in INNOCENT])
def test_ordinary_text_is_left_exactly_alone(name: str, text: str) -> None:
    result = run(text)

    assert result.items[0].content == text
    assert result.findings == ()


def test_forjas_own_source_is_clean(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "src"
    items = [
        PackageItem(
            ItemKind.FILE,
            str(path.relative_to(root)).replace("\\", "/"),
            path.read_text(encoding="utf-8"),
        )
        for path in sorted(root.rglob("*.py"))
    ]

    result = Redactor().redact(items)

    assert result.dropped == ()
    assert [(f.item, f.rule, f.line) for f in result.findings] == []


# -- how the masks are numbered ----------------------------------------------------------------


def test_the_same_secret_gets_the_same_mask_and_new_secrets_the_next_number() -> None:
    first, second = PLANTED[2].secret, PLANTED[3].secret
    text = f"a {first}\nb {second}\nc {first}\n"

    content = out(text)

    assert content.splitlines()[0].count("[REDACTED:") == 1
    assert content.splitlines()[0] == content.splitlines()[2].replace("c", "a")
    assert "[REDACTED:github-token:1]" in content
    assert "[REDACTED:github-fine-grained-token:2]" in content


def test_numbers_run_on_across_the_items_of_one_package() -> None:
    one, two = PLANTED[2].secret, PLANTED[4].secret
    items = [
        PackageItem(ItemKind.TEXT, "a", f"x {one}"),
        PackageItem(ItemKind.TEXT, "b", f"y {two} and {one}"),
    ]

    result = Redactor().redact(items)

    assert result.items[0].content == "x [REDACTED:github-token:1]"
    assert result.items[1].content == "y [REDACTED:gitlab-token:2] and [REDACTED:github-token:1]"


def test_masks_do_not_depend_on_the_value_so_a_run_cannot_guess_a_weak_secret() -> None:
    a = out("password=Sunshine2024;")
    b = out("password=Moonlight2024;")

    assert a == b == "password=[REDACTED:connection-string-password:1];"


def test_redaction_is_deterministic_and_idempotent() -> None:
    text = (
        "\n".join(p.text for p in PLANTED)
        + "\n"
        + "\n".join(d.text.format(value=d.value) for d in personal_data())
    )

    once = out(text)

    assert out(text) == once
    assert out(once) == once
    assert run(once).findings == ()


# -- the generic key = value detector ----------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        'password = "hunter22"',
        "password: Summer2024!",
        'DB_PASSWORD="p4ssw0rd-long"',
        "export API_KEY=abc123def456",
        '{"client_secret": "Zx81Qp0LmN4v"}',
        "api-key => 'k3y-v4lue-9x'",
    ],
)
def test_human_made_credentials_are_caught_by_the_generic_rule(line: str) -> None:
    result = run(line)

    assert [f.rule for f in result.findings] == ["generic-credential"]


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        ("DB_PASSWORD=supersecret", "environment-credential"),
        ("export API_TOKEN=correcthorsebattery", "environment-credential"),
    ],
)
def test_a_credential_of_letters_only_is_caught_in_an_environment_file(
    line: str, rule: str
) -> None:
    result = run("x = 1" + chr(10) + line + chr(10) + "y = 2" + chr(10))

    assert [f.rule for f in result.findings] == [rule]
    assert "supersecret" not in result.items[0].content
    assert "correcthorse" not in result.items[0].content


@pytest.mark.parametrize(
    "line",
    [
        'password = "password"',  # a documentation value
        "API_TOKEN=aaaaaaaaaa",  # no entropy
        'SECRETS = "no_secrets"',  # in quotes: the value of an enumeration
        "db_password=supersecret",  # lower case with no quotes: a keyword argument
        "DB_PASSWORD=supersecret and more",  # not an environment line
    ],
)
def test_letters_that_are_not_a_typed_credential_are_left(line: str) -> None:
    assert out(line) == line


@pytest.mark.parametrize(
    "line",
    [
        "token = abcdefghijkl",  # only letters: an identifier
        "secret = 12345678901234",  # only digits: a number
        "password = some_function_name",
        "password == other_value",
    ],
)
def test_words_and_numbers_after_a_credential_name_are_not_credentials(line: str) -> None:
    assert out(line) == line


def test_the_entropy_threshold_is_adjustable() -> None:
    line = "token = aaaa1111"

    assert out(line) == line
    assert "[REDACTED:" in out(line, settings=RedactionSettings(entropy_threshold=0))
    assert out("password = hunter22", settings=RedactionSettings(entropy_threshold=4.5)) == (
        "password = hunter22"
    )


# -- keys that span lines, formats and neighbours ----------------------------------------------


def test_a_private_key_is_removed_whole_with_its_body_and_the_lines_after_keep_their_numbers() -> (
    None
):
    key = PLANTED[14]
    text = "x = 1\n" + key.text + "\nsecret_after = hunter2024!\n"

    result = run(text)

    assert key.secret.splitlines()[0] not in result.items[0].content
    assert [(f.rule, f.line) for f in result.findings] == [
        ("private-key-block", 2),
        ("generic-credential", 10),
    ]


def test_a_cut_off_private_key_is_still_removed() -> None:
    body = "A1b2C3d4" * 8
    text = "-----BEGIN RSA PRIVATE" + " KEY-----\n" + body + "\n" + body + "\n"

    content = out(text)

    assert body not in content and "[REDACTED:private-key-header:1]" in content


def test_punctuation_around_a_secret_is_kept() -> None:
    secret = PLANTED[2].secret

    assert out(f'("{secret}",)') == '("[REDACTED:github-token:1]",)'
    assert out(f"<{secret}>.") == "<[REDACTED:github-token:1]>."


def test_a_password_inside_a_url_is_masked_and_the_rest_of_the_address_is_not_left_dangling() -> (
    None
):
    planted = PLANTED[15]

    content = out(planted.text)

    assert planted.secret not in content
    assert content.startswith("DATABASE_URL=postgres://app:[REDACTED:url-credentials:1]")
    assert "@" not in content.split("[REDACTED")[0]


def test_windows_line_endings_and_unicode_are_handled() -> None:
    secret = PLANTED[2].secret
    text = f"linha 1 — ação\r\ntoken: {secret}\r\nfim ✓\r\n"

    result = run(text)

    assert (
        result.items[0].content == "linha 1 — ação\r\ntoken: [REDACTED:github-token:1]\r\nfim ✓\r\n"
    )
    assert result.findings[0].line == 2


# -- what a repository may and may not change --------------------------------------------------


def test_a_repository_can_allow_exact_values_and_patterns() -> None:
    secret = PLANTED[2].secret
    other = PLANTED[4].secret

    assert out(secret, settings=RedactionSettings(allow=(secret,))) == secret
    assert out(other, settings=RedactionSettings(allow=(secret,))) != other
    assert out(other, settings=RedactionSettings(allow_patterns=("gl.*",))) == other


def test_a_repository_can_add_its_own_secret_patterns() -> None:
    settings = RedactionSettings(
        patterns=(("internal-id", r"corp-id-(\d{8})"), ("whole", r"ZZ\d{6}"))
    )

    result = run("a corp-id-12345678 b ZZ123456", settings=settings)

    assert result.items[0].content == (
        "a corp-id-[REDACTED:custom-internal-id:1] b [REDACTED:custom-whole:2]"
    )
    assert [f.rule for f in result.findings] == ["custom-internal-id", "custom-whole"]


def test_secret_detectors_cannot_be_switched_off_by_any_setting() -> None:
    settings = RedactionSettings(pii=frozenset(), entropy_threshold=8)
    secret = PLANTED[2].secret

    assert secret not in out(secret, settings=settings)
    assert not hasattr(settings, "secrets")


def test_personal_data_detectors_can_be_chosen_one_by_one() -> None:
    cpf = next(d for d in personal_data() if d.kind == "cpf")
    mail = next(d for d in personal_data() if d.kind == "email")
    text = f"{cpf.value} {mail.value}"
    only_mail = RedactionSettings(pii=frozenset({PiiKind.EMAIL}))

    content = out(text, settings=only_mail)

    assert cpf.value in content and mail.value not in content


# -- the rules are versioned -------------------------------------------------------------------


def test_the_rules_fingerprint_is_pinned_so_a_change_cannot_go_unversioned() -> None:
    """Changed a rule? Bump RULES_VERSION in rules.py and update this value with it."""
    assert rules_fingerprint()[:8] == "4d077c3f"
    assert rules_version() == "2026.11.1+4d077c3f"
