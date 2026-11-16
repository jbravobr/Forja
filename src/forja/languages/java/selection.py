"""The ids of the tests a Java run selects, one form for Maven, Gradle and the Forja's own reports.

A test id is ``<binary class name>.<method>``: ``com.acme.CalcTest.soma``, and
``com.acme.CalcTest$Nested.soma`` for a nested class. The tools run whole methods, so a case of a
parameterized method (Surefire names it ``soma(int, int)[2]``, Gradle ``[2] 1, 2``) answers to its
method's id: ``selector`` gives it.
"""

import re
from collections.abc import Sequence

_CASE_OF_MAVEN = re.compile(r"\(.*\)\[\d+\]$")
_CLASS = re.compile(r"[A-Za-z_][\w$]*(\.[A-Za-z_][\w$]*)*")
_METHOD = re.compile(r"[A-Za-z_][\w$]*")


def selector(test_id: str) -> str:
    """The method id that runs the test a tool reports as ``test_id``. Gradle does not name the
    method of a case at all: its id is the class, a superset that runs the case."""
    if ".[" in test_id:
        return test_id.split(".[", 1)[0]
    return _CASE_OF_MAVEN.sub("", test_id)


def split(test_id: str) -> tuple[str, str] | None:
    """The class and the method of a method id, or ``None`` when it is not one."""
    klass, dot, method = test_id.rpartition(".")
    if not dot or _CLASS.fullmatch(klass) is None or _METHOD.fullmatch(method) is None:
        return None
    return klass, method


def all_methods(test_ids: Sequence[str]) -> bool:
    return all(split(test_id) is not None for test_id in test_ids)


def maven_filter(test_ids: Sequence[str]) -> str:
    """Surefire's ``-Dtest``: ``class#method`` for each id, the nested class with its ``$``."""
    return ",".join(f"{klass}#{method}" for klass, method in map(_method, test_ids))


def gradle_filter(test_ids: Sequence[str]) -> tuple[str, ...]:
    """Gradle's ``--tests``, one for each id: ``class.method``, the nested class with its ``$``."""
    return tuple(map(_checked, test_ids))


def order_arguments(test_ids: Sequence[str]) -> tuple[str, ...]:
    """The ``-D`` properties that make the probe's orderers run the classes and the methods in the
    order of ``test_ids`` (first appearance); a class or a method not named runs after them."""
    classes = dict.fromkeys(_method(test_id)[0] for test_id in test_ids)
    methods = dict.fromkeys(_checked(test_id) for test_id in test_ids)
    return (
        f"-Dforja.ordem={','.join(classes)}",
        f"-Dforja.ordemMetodos={','.join(methods)}",
        "-Djunit.jupiter.testclass.order.default=com.forja.probe.OrdemDeClasses",
        "-Djunit.jupiter.testmethod.order.default=com.forja.probe.OrdemDeMetodos",
    )


def _method(test_id: str) -> tuple[str, str]:
    found = split(test_id)
    if found is None:
        raise ValueError(f"not a method id: {test_id!r}")
    return found


def _checked(test_id: str) -> str:
    _method(test_id)
    return test_id
