"""Which packages and which ``-run`` pattern run a list of test ids, from the ids alone.

An id is ``<import path>/<TestName>[/<subtest>...]``, as ``go test -json`` names a test. The
module of the project (from ``go.mod``) tells where the import path becomes a directory. A test name
starts with ``Test`` and is not followed by a lower-case letter, which is how Go itself names them.
"""

import re
from collections.abc import Iterable

_TEST_NAME = re.compile(r"Test(?![a-z])")
# regexp.QuoteMeta: the characters that mean something to the regular expression of -run
_SPECIAL = frozenset("\\.+*?()|[]{}^$")
_MODULE = re.compile(r"^module\s+\"?([^\s\"]+)", re.MULTILINE)


def quote(name: str) -> str:
    return "".join("\\" + c if c in _SPECIAL else c for c in name)


def module_of(manifest: str) -> str | None:
    found = _MODULE.search(manifest)
    return found[1] if found else None


def selection(ids: Iterable[str], module: str) -> tuple[tuple[str, ...], str] | None:
    """The package arguments (in the order of the ids, each once) and the ``-run`` pattern that
    run the tests ``ids``, a superset of them, or ``None`` when an id is not a test of a package
    of ``module``.

    The pattern has one alternation per level of the names: the tests of every id at that level,
    so a test the ids do not ask for may run too, never one they ask for not run.
    """
    packages: dict[str, None] = {}
    levels: list[set[str]] = []
    for test_id in ids:
        parts = test_id.split("/")
        start = next((i for i in range(1, len(parts)) if _TEST_NAME.match(parts[i])), None)
        if start is None:
            return None
        path = "/".join(parts[:start])
        if any(part in ("", ".", "..") for part in parts[:start]):
            return None  # an id from a report of the project never names a folder outside it
        if path == module:
            packages["."] = None
        elif path.startswith(module + "/"):
            packages[f"./{path[len(module) + 1 :]}"] = None
        else:
            return None
        for depth, name in enumerate(parts[start:]):
            if depth == len(levels):
                levels.append(set())
            levels[depth].add(name)
    pattern = "/".join(
        "^(" + "|".join(quote(name) for name in sorted(names)) + ")$" for names in levels
    )
    return tuple(packages), pattern
