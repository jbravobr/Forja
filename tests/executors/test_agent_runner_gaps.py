"""What the runner must not do: wipe git, write through a link, or demand what was not asked."""

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from forja.core.artifacts.models import Producer
from forja.core.redaction import Redactor
from forja.orchestrator.agent_runner import _refuse_links_on_the_way, _wipe, build_package
from forja.orchestrator.ports import RejectedWork
from tests.executors.test_agent_runner import USAGE, go, rig, settle, work
from tests.executors.test_package import context


def test_clearing_a_directory_leaves_git_and_the_kept_paths_and_nothing_else(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (tmp_path / "keep.txt").write_text("k", encoding="utf-8")
    (tmp_path / "gone.txt").write_text("g", encoding="utf-8")

    _wipe(tmp_path, frozenset({"keep.txt"}))

    assert sorted(p.name for p in tmp_path.iterdir()) == [".git", "keep.txt"]
    assert (tmp_path / ".git" / "HEAD").is_file()


def test_a_git_directory_below_the_root_is_cleared_like_any_other(tmp_path: Path) -> None:
    (tmp_path / "sub" / ".git").mkdir(parents=True)
    (tmp_path / "sub" / ".git" / "HEAD").write_text("ref", encoding="utf-8")

    _wipe(tmp_path, frozenset())

    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32", reason="junctions exist on Windows")
def test_clearing_a_directory_removes_a_junction_without_entering_it(tmp_path: Path) -> None:
    import _winapi  # noqa: PLC0415

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("p", encoding="utf-8")
    cleared = tmp_path / "cleared"
    cleared.mkdir()
    _winapi.CreateJunction(str(outside), str(cleared / "jn"))  # type: ignore[attr-defined,unused-ignore]

    _wipe(cleared, frozenset())

    assert list(cleared.iterdir()) == []
    assert (outside / "precious.txt").read_text(encoding="utf-8") == "p"


@pytest.mark.skipif(sys.platform != "win32", reason="junctions exist on Windows")
def test_a_junction_is_refused_wherever_it_is_in_the_path(tmp_path: Path) -> None:
    import _winapi  # noqa: PLC0415

    outside = tmp_path / "outside"
    outside.mkdir()
    worktree = tmp_path / "wt"
    (worktree / "src").mkdir(parents=True)
    _winapi.CreateJunction(str(outside), str(worktree / "src" / "jn"))  # type: ignore[attr-defined,unused-ignore]
    _winapi.CreateJunction(str(outside), str(worktree / "top"))  # type: ignore[attr-defined,unused-ignore]

    for path in ("src/jn", "src/jn/x.txt", "top", "top/x.txt"):
        with pytest.raises(RuntimeError, match="through a link"):
            _refuse_links_on_the_way(worktree, path)
    _refuse_links_on_the_way(worktree, "src/other.txt")


def test_what_only_the_documenter_is_given_stays_out_of_the_other_roles_package(
    tmp_path: Path,
) -> None:
    ctx = replace(
        context(tmp_path, ("src/a.py",)),
        readme="README-TEXT",
        adr="ADR-TEXT",
        findings="FINDINGS-TEXT",
    )

    text = build_package(ctx, Redactor())

    for given in ("README-TEXT", "ADR-TEXT", "FINDINGS-TEXT"):
        assert given not in text


def test_the_documenter_gets_only_what_it_was_given(tmp_path: Path) -> None:
    base = context(tmp_path, ("src/a.py",))
    documenter = replace(base, task=replace(base.task, role=Producer.DOCUMENTER))

    empty = build_package(documenter, Redactor())
    full = build_package(
        replace(documenter, readme="README-TEXT", adr="ADR-TEXT", findings="FINDINGS-TEXT"),
        Redactor(),
    )

    for given in ("README-TEXT", "ADR-TEXT", "FINDINGS-TEXT"):
        assert given not in empty
        assert given in full


AUTHOR_OUTPUT = {
    "actions": [
        {
            "summary": "a test",
            "reason": "it shows the defect",
            "files": [{"path": "tests/test_a.py", "change": "created"}],
        }
    ],
}


def _author(tmp_path: Path, reviews: str, characterizes: bool = False):  # type: ignore[no-untyped-def]
    script = {
        "writes": {"tests/test_a.py": "def test_a():\n    assert True\n"},
        "saida": AUTHOR_OUTPUT,
        "lines": [USAGE],
    }
    r = rig(tmp_path, script, role=Producer.TEST_AUTHOR)
    task = replace(
        r.ctx.task,
        role=Producer.TEST_AUTHOR,
        test_root="tests",
        reviews=reviews,
        characterizes=characterizes,
    )
    return replace(r, ctx=replace(r.ctx, task=task))


def test_a_test_author_that_declares_no_tests_is_refused_on_its_own_stage(tmp_path: Path) -> None:
    r = _author(tmp_path, reviews="")

    go(work(r))

    with pytest.raises(RejectedWork, match="declared tests are not valid"):
        go(settle(r.runner.apply_diff(r.ctx)))


def test_a_test_author_that_reviews_another_stage_does_not_declare_a_manifest(
    tmp_path: Path,
) -> None:
    r = _author(tmp_path, reviews="t1-s")

    go(work(r))
    go(settle(r.runner.apply_diff(r.ctx)))


def test_a_test_author_that_writes_before_a_refactoring_does_not_declare_a_manifest(
    tmp_path: Path,
) -> None:
    r = _author(tmp_path, reviews="", characterizes=True)

    go(work(r))
    go(settle(r.runner.apply_diff(r.ctx)))
