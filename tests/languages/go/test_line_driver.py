"""The line driver with a fake Go tool: how it lists, places and reports the tests, and its exit
codes. The real tool is in ``test_test_lines.py``."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from forja.languages.go import line_driver
from forja.languages.go.adapter import LINE_DRIVER

FAKE_GO = """
import os, sys
argv = sys.argv[1:]
if argv[:1] == ["list"]:
    sys.stdout.write(open(os.environ["FAKE_DIRS"], encoding="utf-8").read())
    sys.exit(int(os.environ.get("FAKE_LIST_CODE", "0")))
if "-c" in argv:
    sys.exit(int(os.environ.get("FAKE_BUILD_CODE", "0")))
sys.stdout.write(open(os.environ["FAKE_EVENTS"], encoding="utf-8").read())
sys.exit(int(os.environ.get("FAKE_LIST_CODE", "0")))
"""
MODULE = "example.com/calc"


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, ...]:
    tool = tmp_path / "fake_go.py"
    tool.write_text(FAKE_GO, encoding="utf-8")
    events = tmp_path / "events.txt"
    dirs = tmp_path / "dirs.txt"
    dirs.write_text(f"{MODULE}\t{tmp_path}\n{MODULE}/a\t{tmp_path / 'a'}\n", encoding="utf-8")
    monkeypatch.setenv("FAKE_EVENTS", str(events))
    monkeypatch.setenv("FAKE_DIRS", str(dirs))
    monkeypatch.delenv("FAKE_LIST_CODE", raising=False)
    monkeypatch.delenv("FAKE_BUILD_CODE", raising=False)
    return (sys.executable, str(tool))


def events(tmp_path: Path, *lines: dict[str, object]) -> None:
    (tmp_path / "events.txt").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")


def run_driver(project: Path, go: tuple[str, ...]) -> int:
    done = subprocess.run(
        [sys.executable, str(LINE_DRIVER), ".forja-test-lines", *go],
        cwd=project,
        capture_output=True,
        check=False,
    )
    return done.returncode


def test_a_test_is_its_package_and_its_names_and_a_leaf_is_one_without_subtests() -> None:
    packages = {MODULE: Path("/m"), f"{MODULE}/a": Path("/m/a")}

    assert line_driver.split(f"{MODULE}/a/TestX/sub", packages) == (
        f"{MODULE}/a",
        ["TestX", "sub"],
    )
    assert line_driver.split(f"{MODULE}/TestRoot", packages) == (MODULE, ["TestRoot"])
    assert line_driver.split("other/TestX", packages) is None


def test_the_ids_are_listed_once_each_in_the_order_of_the_tool(
    tmp_path: Path, fake: tuple[str, ...]
) -> None:
    events(
        tmp_path,
        {"Action": "pass", "Package": f"{MODULE}/a", "Test": "TestX"},
        {"Action": "pass", "Package": f"{MODULE}/a", "Test": "TestX/sub"},
        {"Action": "skip", "Package": f"{MODULE}/a", "Test": "TestX"},
        {"Action": "pass", "Package": f"{MODULE}/a"},
    )

    assert line_driver.list_ids(list(fake), tmp_path) == [
        f"{MODULE}/a/TestX",
        f"{MODULE}/a/TestX/sub",
    ]


def test_a_package_that_failed_before_any_test_ran_is_not_listed(
    tmp_path: Path, fake: tuple[str, ...]
) -> None:
    events(tmp_path, {"Action": "fail", "Package": f"{MODULE}/a"})

    assert line_driver.list_ids(list(fake), tmp_path) is None


def test_the_listing_that_names_no_test_and_fails_is_not_measured(
    tmp_path: Path, fake: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    events(tmp_path)
    monkeypatch.setenv("FAKE_LIST_CODE", "1")

    assert run_driver(tmp_path, fake) == line_driver.EXIT_LIST


def test_a_test_binary_that_does_not_build_is_the_build_exit_code(
    tmp_path: Path, fake: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    events(tmp_path, {"Action": "pass", "Package": f"{MODULE}/a", "Test": "TestX"})
    monkeypatch.setenv("FAKE_BUILD_CODE", "1")

    assert run_driver(tmp_path, fake) == line_driver.EXIT_BUILD


def test_the_driver_needs_its_output_directory_and_a_go_command(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(LINE_DRIVER)], cwd=tmp_path, capture_output=True, check=False
    )

    assert done.returncode == line_driver.EXIT_USAGE


def test_the_quoting_is_regexp_quotemeta_for_the_special_characters() -> None:
    assert line_driver.quote("a.b*c") == r"a\.b\*c"
    assert line_driver.quote("plain_name1") == "plain_name1"
