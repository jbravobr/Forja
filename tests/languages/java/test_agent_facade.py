"""``forja agente testar|lint`` on a Java project, for real, with Maven and with Gradle.

Needs a JDK and the build tool (and, the first time, the network for their plugins); the tests
are skipped without them.
"""

import os
from pathlib import Path

import pytest

from tests.cli.test_facade import run_facade
from tests.languages.java import test_probe_runs as probe
from tests.languages.java.test_probe_runs import PRODUCTION

tool = probe.tool  # Maven, then Gradle
project = probe.project

pytestmark = pytest.mark.slow


def test_the_tests_of_a_java_project_pass_for_the_agent(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_facade(["testar"], project, dict(os.environ)) == 0
    assert "falhou:" not in capsys.readouterr().out


def test_a_java_test_that_fails_is_named_to_the_agent(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = (project / PRODUCTION).read_text(encoding="utf-8").replace("a + b", "a * b")
    (project / PRODUCTION).write_text(text, encoding="utf-8", newline="\n")

    assert run_facade(["testar"], project, dict(os.environ)) != 0
    failed = [line for line in capsys.readouterr().out.splitlines() if line.startswith("falhou: ")]
    assert failed
    assert all(line.startswith("falhou: com.acme.") for line in failed)


def test_a_project_that_declares_no_linter_has_no_lint_to_show(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_facade(["lint"], project, dict(os.environ)) == 0
    assert capsys.readouterr().out.splitlines() == [
        "lint não se aplica a este projeto (java): the project does not declare that tool"
    ]
