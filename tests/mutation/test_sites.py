import ast
from pathlib import Path

import pytest

from forja.mutation import Site, apply_site, discover
from forja.mutation.sites import function_spans

SRC = Path(__file__).resolve().parents[2] / "src" / "forja"


def sites_of(body: str, header: str = "def f(a, b, c=None):") -> list[Site]:
    indented = "".join(f"    {line}\n" for line in body.strip().splitlines())
    return discover(f"{header}\n{indented}", "m.py")


def changes(body: str) -> list[str]:
    return [site.description for site in sites_of(body)]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("return a + b", ["+ -> -"]),
        ("return a - b", ["- -> +"]),
        ("return a * b", ["* -> /"]),
        ("return a / b", ["/ -> *"]),
        ("return a < b", ["< -> <="]),
        ("return a <= b", ["<= -> <"]),
        ("return a > b", ["> -> >="]),
        ("return a >= b", [">= -> >"]),
        ("return a == b", ["== -> !="]),
        ("return a != b", ["!= -> =="]),
        ("return a and b", ["and -> or"]),
        ("return a or b", ["or -> and"]),
        ("return not a", ["delete not"]),
        ("return -a", ["delete -"]),
        ("return True", ["True -> False"]),
        ("return False", ["False -> True"]),
        ("return 0", ["0 -> 1"]),
        ("return 1", ["1 -> 0"]),
    ],
)
def test_each_operator_has_one_change_worded_as_the_mutator_words_it(
    body: str, expected: list[str]
) -> None:
    assert changes(body) == expected


@pytest.mark.parametrize(
    "body",
    [
        "return a // b",
        "return a % b",
        "return a ** b",
        "return a & b",
        "return a | b",
        "return a in b",
        "return a is b",
        "return +a",
        "return ~a",
        "return 2",
        "return 1.0",
        "return 0x1",
        "return 1_0",
        "return 00",
        "return None",
        'return "a + b and True 1"',
        "return a  # + - and not 1",
        "return f(*b, **c)",
        "a += b",
        "a -= 2",
    ],
)
def test_what_the_mutator_does_not_change_is_not_a_site(body: str) -> None:
    assert changes(body) == []


def test_code_outside_a_function_has_no_site() -> None:
    source = "LIMIT = 1\nclass C:\n    FLAG = True\n    def m(self):\n        return 0\n"
    assert [(s.line, s.function) for s in discover(source, "m.py")] == [(5, "C.m")]


def test_the_name_is_the_qualified_name_of_the_innermost_function() -> None:
    source = (
        "class A:\n"
        "    class B:\n"
        "        def m(self):\n"
        "            def inner():\n"
        "                return 1\n"
        "            return 0\n"
        "def top():\n"
        "    async def work():\n"
        "        return True\n"
    )
    assert [(s.line, s.function) for s in discover(source, "m.py")] == [
        (5, "A.B.m.inner"),
        (6, "A.B.m"),
        (9, "top.work"),
    ]


def test_decorators_are_outside_the_function_and_defaults_are_inside() -> None:
    source = "@wrap(1)\ndef f(a=1):\n    pass\n"
    assert [(s.line, s.function) for s in discover(source, "m.py")] == [(2, "f")]


def test_a_lambda_belongs_to_the_function_that_holds_it() -> None:
    assert changes("return lambda x: x + 1") == ["+ -> -", "1 -> 0"]


def test_a_negative_one_has_two_sites() -> None:
    assert changes("return -1") == ["delete -", "1 -> 0"]


def test_every_operator_of_a_chain_is_a_site() -> None:
    assert changes("return a < b <= c") == ["< -> <=", "<= -> <"]
    assert changes("return a and b or c") == ["and -> or", "or -> and"]
    assert changes("return a and b and c") == ["and -> or", "and -> or"]


@pytest.mark.parametrize("body", ["return a not in b", "return a is not b"])
def test_negated_membership_and_identity_are_no_site_as_in_the_mutator(body: str) -> None:
    assert changes(body) == []


def test_the_operator_is_found_through_parentheses_comments_and_line_breaks() -> None:
    body = "return (\n    (a)  # - and\n    +\n    (b)\n)"
    assert [(s.line, s.description) for s in sites_of(body)] == [(4, "+ -> -")]


def test_a_binary_minus_before_a_unary_one_is_two_different_sites() -> None:
    assert changes("return a - -b") == ["- -> +", "delete -"]


def test_an_f_string_expression_is_code_and_its_text_is_not() -> None:
    assert changes('return f"a + b {a + b} 1"') == ["+ -> -"]


def test_offsets_are_bytes_of_utf_8_and_lines_are_one_based() -> None:
    source = 'def f(a, b):\n    x = "ção"; return a + b\n'
    (site,) = discover(source, "m.py")
    assert site.line == 2
    assert source.encode()[site.start : site.end] == b"+"
    assert apply_site(source, site) == 'def f(a, b):\n    x = "ção"; return a - b\n'


def test_a_site_knows_the_line_its_statement_starts_on() -> None:
    source = (
        "def f(a, b=0):\n"  # a default is in no statement: its own line
        "    if (a\n"
        "            > 0):\n"  # the condition belongs to the if
        "        return (\n"
        "            a\n"
        "            + 1\n"
        "        )\n"
        "    return a - b\n"
    )

    found = [(s.description, s.line, s.anchor_line, s.lines) for s in discover(source, "m.py")]

    assert found == [
        ("0 -> 1", 1, 1, (1,)),
        ("> -> >=", 3, 2, (3, 2)),
        ("0 -> 1", 3, 2, (3, 2)),
        ("+ -> -", 6, 4, (6, 4)),
        ("1 -> 0", 6, 4, (6, 4)),
        ("- -> +", 8, 8, (8,)),
    ]
    # the same default further down the file: still its own line, not the one before
    (lower,) = discover("\n\ndef f(a, b=0):\n    pass\n", "m.py")
    assert (lower.line, lower.anchor_line) == (3, 3)


def test_the_statement_of_a_site_is_found_in_every_language() -> None:
    java = "class C {\n  int m(int a) {\n    return a\n      + 1;\n  }\n}\n"
    go = "package p\n\nfunc F(a int) int {\n\treturn a +\n\t\t1\n}\n"
    script = "function f(a) {\n  return a\n    + 1;\n}\n"
    csharp = "class C {\n  int M(int a) {\n    return a\n      + 1;\n  }\n}\n"

    def anchors(source: str, path: str) -> list[tuple[int, int]]:
        return [(site.line, site.anchor_line) for site in discover(source, path)]

    assert anchors(java, "C.java") == [(4, 3), (4, 3)]
    assert anchors(go, "p.go") == [(4, 4), (5, 4)]
    assert anchors(script, "f.ts") == [(3, 2), (3, 2)]
    assert anchors(csharp, "C.cs") == [(4, 3), (4, 3)]


def test_a_site_built_without_a_statement_line_is_anchored_on_its_own() -> None:
    site = Site(
        line=7, start=0, end=1, original="+", mutant="-", category="arithmetic", function="f"
    )

    assert site.anchor_line == 7
    assert site.lines == (7,)


def test_sites_come_in_file_order() -> None:
    sites = sites_of("if a > 0:\n    return b + 1\nreturn c")
    assert [(s.line, s.start) for s in sites] == sorted((s.line, s.start) for s in sites)
    assert [s.description for s in sites] == ["> -> >=", "0 -> 1", "+ -> -", "1 -> 0"]


def test_source_that_is_not_python_raises_syntax_error() -> None:
    with pytest.raises(SyntaxError):
        discover("def f(:\n", "m.py")


def test_apply_refuses_a_site_that_does_not_belong_to_the_source() -> None:
    (site,) = sites_of("return a + b")
    with pytest.raises(ValueError, match="no longer has"):
        apply_site("def f(a, b):\n    return a * b\n", site)


@pytest.mark.parametrize(
    "path", sorted(SRC.rglob("*.py")), ids=lambda p: p.relative_to(SRC).as_posix()
)
def test_every_mutant_of_the_project_is_python_that_differs_from_the_original(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    for site in discover(source, "m.py"):
        mutant = apply_site(source, site)
        assert mutant != source
        ast.parse(mutant)


SUPPRESSED = [
    ("src/forja/core/redaction/engine.py", "_shorten_long_lines", "<= -> <"),
    ("src/forja/core/redaction/engine.py", "_merge_overlaps", "delete -"),
    ("src/forja/core/redaction/engine.py", "_merge_overlaps", "1 -> 0"),
    ("src/forja/core/redaction/engine.py", "_merge_overlaps", "- -> +"),
    ("src/forja/core/redaction/engine.py", "_merge_overlaps", "> -> >="),
    ("src/forja/ui/plain.py", "PlainRenderer.__init__", "0 -> 1"),
    ("src/forja/core/gitguard/rules.py", "check", "== -> !="),
    ("src/forja/core/policy/config.py", "_check_pattern", "False -> True"),
    ("src/forja/ui/theme.py", "_luminance.channel", "<= -> <"),
    ("src/forja/languages/python/parsers.py", "_summary_lines", "1 -> 0"),
    ("src/forja/ui/plain.py", "PlainRenderer._update_feed", "> -> >="),
    ("src/forja/ui/plain.py", "PlainRenderer._update_rejections", "> -> >="),
    ("src/forja/orchestrator/agent_runner.py", "build_package", "True -> False"),
    ("src/forja/core/process/runner.py", "ProcessRunner._supervise", "0 -> 1"),
    ("src/forja/core/locking/index_locks.py", "remove_orphan_index_locks", "False -> True"),
    ("src/forja/core/gitguard/guarded.py", "GuardedGit.run", "or -> and"),
]


@pytest.mark.parametrize(("file", "function", "change"), SUPPRESSED)
def test_the_changes_the_suppressions_name_are_sites_of_the_engine(
    file: str, function: str, change: str
) -> None:
    source = (SRC.parents[1] / file).read_text(encoding="utf-8")
    assert (function, change) in {(s.function, s.description) for s in discover(source, "m.py")}


def lines(source: str, path: str) -> list[str]:
    return [site.description for site in discover(source, path)]


def test_go_changes_a_star_into_a_slash_and_never_a_slash() -> None:
    source = """package p

func f(a, b int) int {
	return a * b + a / b
}
"""
    assert lines(source, "p.go") == ["* -> /", "+ -> -"]


def test_java_has_the_set_and_no_site_in_a_constructor() -> None:
    source = """class C {
  C(int a) { int x = a + 1; }
  boolean m(int a, boolean b) { return !b && a >= 0; }
}
"""
    assert lines(source, "C.java") == ["delete !", "&& -> ||", ">= -> >", "0 -> 1"]


def test_typescript_adds_strict_equality_nullish_and_optional_chaining() -> None:
    source = "function f(a, b) { return a === b ?? a?.c; }"
    assert lines(source, "f.ts") == ["=== -> !==", "?? -> ||", "?. -> ."]


@pytest.mark.parametrize(
    ("body", "without"),
    [("return a?.();", "return a();"), ("return a?.[b];", "return a[b];")],
)
def test_an_optional_call_or_index_loses_the_chain_and_nothing_takes_its_place(
    body: str, without: str
) -> None:
    source = f"function f(a, b) {{ {body} }}"

    (site,) = discover(source, "f.ts")

    assert site.description == "delete ?."
    assert apply_site(source, site) == f"function f(a, b) {{ {without} }}"


def test_a_name_spelled_like_a_boolean_is_not_one() -> None:
    assert discover("def f(true):\n    return true\n", "m.py") == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # the return type and the parameter type hold literals; only the body runs
        ("function f(a: 0 | 1): { ok: true } { return { ok: a > 0 }; }", ["> -> >=", "0 -> 1"]),
        ("function f() { type T = { n: 1; b: false }; return 1; }", ["1 -> 0"]),
        ("function f() { interface I { n: 0 } return 0; }", ["0 -> 1"]),
        ("function f() { return g<1>(0); }", ["0 -> 1"]),
        ("function f<T extends 1>(a: T) { return a === 1; }", ["=== -> !==", "1 -> 0"]),
        ("function f() { const a: true = true; return a; }", ["true -> false"]),
        ("function f() { return (x as 1) + 1; }", ["+ -> -", "1 -> 0"]),
    ],
)
def test_a_literal_inside_a_typescript_type_is_not_a_site(source: str, expected: list[str]) -> None:
    assert lines(source, "f.ts") == expected


def test_javascript_uses_the_typescript_rules() -> None:
    assert lines("function f(a, b) { return a === b; }", "f.js") == ["=== -> !=="]


def test_csharp_follows_the_java_set() -> None:
    source = "class C { bool M(int a, bool b) { return !b && a < 1; } }"
    assert lines(source, "C.cs") == ["delete !", "&& -> ||", "< -> <=", "1 -> 0"]


def test_a_file_of_a_language_without_rules_is_refused() -> None:
    with pytest.raises(ValueError, match="no mutation rules"):
        discover("x", "notes.txt")


def test_the_spans_of_the_functions_are_their_exact_lines_in_file_order() -> None:
    source = (
        "def zeta():\n"
        "    return 1\n"
        "\n"
        "class K:\n"
        "    def beta(self):\n"
        "        def inner():\n"
        "            return 2\n"
        "        return inner\n"
        "\n"
        "def alpha():\n"
        "    return 3\n"
    )
    spans = function_spans(source, "m.py")
    assert [(first, last) for _, first, last in spans] == [(1, 2), (5, 8), (6, 7), (10, 11)]
    assert spans[0][0] == "zeta"
    assert spans[-1][0] == "alpha"
