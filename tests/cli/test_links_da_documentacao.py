"""Every link from one Markdown file of the repository to another leads to a file that exists."""

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[2]
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)


def _documents() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "README.md", "docs"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    return sorted(name for name in listed.split("\0") if name.endswith(".md"))


def broken_links(document: Path) -> list[str]:
    """The targets of the links of ``document`` to files that do not exist.

    A target with a scheme (``https:``) and a target inside the page (``#section``) are left
    out, and so is everything inside a fenced block."""
    text = _FENCE.sub("", document.read_text(encoding="utf-8"))
    broken = []
    for target in _LINK.findall(text):
        path = unquote(target.split("#")[0])
        if not path or re.match(r"[a-z][a-z0-9+.-]*:", target):
            continue
        if not (document.parent / path).exists():
            broken.append(target)
    return broken


def test_there_are_documents_to_check() -> None:
    assert "README.md" in _documents()
    assert "docs/guia/README.md" in _documents()


@pytest.mark.parametrize("name", _documents())
def test_every_link_to_a_file_leads_to_a_file_that_exists(name: str) -> None:
    assert broken_links(ROOT / name) == []


def test_a_link_to_a_missing_file_is_reported_and_the_others_are_not(tmp_path: Path) -> None:
    (tmp_path / "here.md").write_text("x", encoding="utf-8")
    (tmp_path / "with space.md").write_text("x", encoding="utf-8")
    document = tmp_path / "page.md"
    document.write_text(
        "[a](here.md) [b](gone.md) [c](https://example.com/gone.md) [d](#top)\n"
        "[e](here.md#part) [f](with%20space.md) [g](sub/gone.md#part)\n"
        "```\n[h](in-a-block.md)\n```\n",
        encoding="utf-8",
    )

    assert broken_links(document) == ["gone.md", "sub/gone.md#part"]
