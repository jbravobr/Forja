"""Find the mutation sites of a source file, with the rules of unclebob/mutator.

The rules are the mutator's: Java's set is the base, and Python, Go, TypeScript (JavaScript uses
its rules) and C# are that set spelled in their language. An operator changes only inside a binary
expression; ``!``/``not`` and a unary ``-`` are deleted; ``true``/``false`` swap; of the numbers
only ``0`` and ``1`` swap. Strings and comments hold no site, and neither does code outside a
function. Go changes ``*`` into ``/`` only. TypeScript also changes ``===``, ``!==``, ``??`` and
``?.``. C# is the one language the mutator lacks: its sites follow the Java rule, with the unary
node of C# named in ``UNARY_PARENTS``.
"""

from collections.abc import Iterator
from functools import cache

from tree_sitter import Language, Node, Parser

from forja.checks.structure import Dialect, dialect_of
from forja.mutation.model import Site

BINARY = {
    "+": "-",
    "-": "+",
    "*": "/",
    "/": "*",
    ">": ">=",
    ">=": ">",
    "<": "<=",
    "<=": "<",
    "==": "!=",
    "!=": "==",
    "===": "!==",
    "!==": "===",
    "&&": "||",
    "||": "&&",
    "??": "||",
    "and": "or",
    "or": "and",
}
BINARY_PARENTS = frozenset(
    {"binary_expression", "binary_operator", "comparison_operator", "boolean_operator"}
)
UNARY_PARENTS = frozenset(
    {"unary_expression", "unary_operator", "not_operator", "prefix_unary_expression"}
)
BOOLEANS = {"true": "false", "false": "true", "True": "False", "False": "True"}
NUMBERS = frozenset(
    {"decimal_integer_literal", "integer_literal", "int_literal", "integer", "number"}
)

TYPE_NODES = frozenset(
    {
        "type_annotation",
        "type_alias_declaration",
        "interface_declaration",
        "type_arguments",
        "type_parameters",
        "literal_type",
    }
)

FUNCTIONS = {
    "python": frozenset({"function_definition"}),
    "go": frozenset({"function_declaration", "method_declaration"}),
    "java": frozenset({"method_declaration"}),  # constructors are not entries
    "csharp": frozenset({"method_declaration", "local_function_statement"}),
    "javascript": frozenset(
        {
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
            "arrow_function",
            "function_expression",
        }
    ),
}
FUNCTIONS["typescript"] = FUNCTIONS["javascript"]
CLASSES = frozenset(
    {
        "class_definition",
        "class_declaration",
        "interface_declaration",
        "struct_declaration",
        "record_declaration",
    }
)


def _category(token: str) -> str:
    if token in {"+", "-", "*", "/"}:
        return "arithmetic"
    if token in {">", ">=", "<", "<="}:
        return "comparison"
    if token in {"==", "!=", "===", "!=="}:
        return "equality"
    return "logical"


def _text(node: Node) -> str:
    return node.text.decode("utf-8") if node.text is not None else ""


def _binary_mutant(language: str, token: str) -> str | None:
    if language == "go" and token == "/":
        return None
    if token == "??" and language != "typescript":
        return None
    return BINARY.get(token)


def _optional_mutant(node: Node) -> str | None:
    """``a?.b`` becomes ``a.b``; a call or index drops ``?.``, so ``a?.()`` becomes ``a()``."""
    parent = node.parent
    if parent is None:
        return None
    host = parent.parent if parent.type == "optional_chain" else parent
    if host is None:
        return None
    if host.type == "member_expression":
        return "."
    if host.type in {"call_expression", "subscript_expression"}:
        return ""
    return None


def _in_type(node: Node) -> bool:
    """Whether the leaf is part of a TypeScript type: a literal such as the ``true`` of
    ``{ ok: true }`` in a return type never runs, so the mutator leaves it alone."""
    current = node.parent
    while current is not None:
        if current.type in TYPE_NODES:
            return True
        current = current.parent
    return False


def _change(node: Node, language: str) -> tuple[str, str, str] | None:
    """The one change a leaf allows as ``(original, mutant, category)``, if any."""
    if language == "typescript" and _in_type(node):
        return None
    token = _text(node)
    parent = node.parent.type if node.parent is not None else ""
    if parent in BINARY_PARENTS:
        mutant = _binary_mutant(language, token)
        if mutant is not None:
            return token, mutant, _category(token)
    if language == "typescript" and token == "?.":
        mutant = _optional_mutant(node)
        if mutant is not None:
            return token, mutant, "optional"
    if token in {"!", "not"} and parent in UNARY_PARENTS | {"comparison_operator"}:
        return token, "", "unary"
    if token == "-" and parent in UNARY_PARENTS:
        return token, "", "unary"
    if node.type in {"true", "false"} and token in BOOLEANS:
        return token, BOOLEANS[token], "boolean"
    if node.type in NUMBERS and token in {"0", "1"}:
        return token, "1" if token == "0" else "0", "constant"
    return None


def _statement_line(node: Node) -> int:
    """The line the statement or declaration around ``node`` starts on; its own line if none."""
    current = node.parent
    while current is not None:
        if current.type.endswith(("_statement", "_declaration")):
            return current.start_point[0] + 1
        current = current.parent
    return node.start_point[0] + 1


def _leaves(root: Node) -> Iterator[Node]:
    pending = [root]
    while pending:
        node = pending.pop()
        if node.child_count == 0:
            yield node
        else:
            pending.extend(reversed(node.children))


def _name(node: Node) -> str:
    found = node.child_by_field_name("name")
    if found is not None:
        return _text(found)
    parent = node.parent
    if parent is not None and parent.type in {
        "variable_declarator",
        "pair",
        "assignment_expression",
    }:
        label = parent.child_by_field_name("name") or parent.child_by_field_name("key")
        if label is not None:
            return _text(label)
    return "<anonymous>"


def _owner(node: Node, functions: frozenset[str]) -> str | None:
    """The qualified name of the innermost function that holds ``node``, or ``None``."""
    names: list[str] = []
    inside = False
    current = node.parent
    while current is not None:
        if current.type in functions:
            inside = True
            names.append(_name(current))
        elif current.type in CLASSES:
            names.append(_name(current))
        current = current.parent
    return ".".join(reversed(names)) if inside else None


def _qualified(node: Node, functions: frozenset[str]) -> str:
    """The qualified name of a function node: the classes and functions around it, then itself."""
    names = []
    current: Node | None = node
    while current is not None:
        if current.type in functions or current.type in CLASSES:
            names.append(_name(current))
        current = current.parent
    return ".".join(reversed(names))


@cache
def _parser(dialect: Dialect) -> Parser:
    parser = Parser()
    parser.language = Language(dialect.grammar())
    return parser


def supports(path: str) -> bool:
    return dialect_of(path) is not None


def discover(source: str, path: str) -> list[Site]:
    """The sites of ``source`` in file order, in the language of ``path``.

    Raises ``ValueError`` for a file of a language without rules and ``SyntaxError`` if the text
    does not parse.
    """
    dialect = dialect_of(path)
    if dialect is None:
        raise ValueError(f"no mutation rules for {path}")
    tree = _parser(dialect).parse(source.encode("utf-8"))
    if tree.root_node.has_error:
        raise SyntaxError(f"{path} does not parse")
    language = "typescript" if dialect.language == "javascript" else dialect.language  # same rules
    functions = FUNCTIONS[language]
    found: list[Site] = []
    for leaf in _leaves(tree.root_node):
        change = _change(leaf, language)
        owner = _owner(leaf, functions) if change is not None else None
        if change is None or owner is None:
            continue
        original, mutant, category = change
        found.append(
            Site(
                line=leaf.start_point[0] + 1,
                start=leaf.start_byte,
                end=leaf.end_byte,
                original=original,
                mutant=mutant,
                category=category,
                function=owner,
                anchor_line=_statement_line(leaf),
            )
        )
    return sorted(found, key=lambda site: (site.line, site.start, site.end))


def function_spans(source: str, path: str) -> list[tuple[str, int, int]]:
    """Every function of ``source`` as ``(qualified name, first line, last line)``, in file order.

    The name is the one ``Site.function`` carries, so a site belongs to the functions of its name.
    Same errors as ``discover``.
    """
    dialect = dialect_of(path)
    if dialect is None:
        raise ValueError(f"no mutation rules for {path}")
    tree = _parser(dialect).parse(source.encode("utf-8"))
    if tree.root_node.has_error:
        raise SyntaxError(f"{path} does not parse")
    language = "typescript" if dialect.language == "javascript" else dialect.language
    functions = FUNCTIONS[language]
    found: list[tuple[str, int, int]] = []
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        if node.type in functions:
            found.append(
                (_qualified(node, functions), node.start_point[0] + 1, node.end_point[0] + 1)
            )
        pending.extend(node.children)
    return sorted(found, key=lambda span: (span[1], span[2]))
