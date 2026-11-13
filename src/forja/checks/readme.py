"""The README, judged without a model: is it there, does it say what a reader needs, are its
commands the project's and its links alive (gate 14).

The model is called only for what fails here, and only those parts of the file may change. This
module holds the judgment (``evaluate``) and the reading of the file in sections (``chunks``) that
the gate uses to prove that an approved section was not touched.

A command in the README is the project's when its program, and its subcommand if it names one,
are the ones the adapter runs for the same operation: ``pytest -q`` and ``python -m pytest`` are
both ``pytest``; ``go build ./...`` is ``go build``. A README can say ``npm test`` or ``make``; if
the adapter does not run it, the reader is sent somewhere the Forja never went.
"""

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

README_NAMES = ("README.md", "README.rst")
NO_README = "there is no README"
# what a README must answer, in the order a reader asks it; a heading satisfies the first it fits
REQUIRED = (
    ("what", r"o que|what|sobre|about|vis[aã]o|overview|descri"),
    ("build", r"constru|build|compil|instala"),
    ("test", r"test"),
    ("run", r"execu|rodar|run|uso|usage|como usar|getting started|come[cç]ar"),
    ("configure", r"configur"),
    ("dependencies", r"depend|requisit|requirement"),
)
# The sections of the README model the owner chose (gist KAYOKG/176fc612c1f8cc476c1c4ff558453dec),
# in its order, with what each one answers of ``REQUIRED`` (``None``: the gate does not ask for it).
# The emoji are the gist's (its shortcodes as characters); a name without one has none there.
# The gist has no section for the build, which the gate requires, so "Instalação" is added to it.
MODEL: tuple[tuple[str, str | None], ...] = (
    ("Tópicos", None),
    ("Descrição do projeto", "what"),
    ("Funcionalidades", None),
    ("Layout ou Deploy da Aplicação 💨", None),
    ("Pré-requisitos", "dependencies"),
    ("Instalação", "build"),
    ("Como rodar a aplicação ▶️", "run"),
    ("Como rodar os testes", "test"),
    ("Casos de uso", None),
    ("Configuração", "configure"),
    ("Linguagens, dependências e libs utilizadas 📚", None),
    ("Resolvendo problemas ❗", None),
    ("Tarefas em aberto", None),
    ("Desenvolvedores e contribuintes 🐙", None),
    ("Licença", None),
)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```+|~~~+)")
_SPAN = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_WORD = re.compile(r"[A-Za-z][\w-]*")
_PROMPT = re.compile(r"^\s*(?:\$|>|PS [^>]*>)\s+")
_PYTHONS = frozenset({"python", "python3", "py"})
_WRAPPERS = {"uv": "run", "poetry": "run", "pipenv": "run", "pdm": "run"}
_ALIASES = {"gradlew": "gradle", "mvnw": "mvn"}
# programs whose first word is the operation (``go build``); for the rest it is an argument
SUBCOMMANDED = frozenset(
    {"go", "dotnet", "mvn", "gradle", "npm", "yarn", "pnpm", "bun", "cargo", "make"}
)

Signature = tuple[str, str]  # (program, subcommand or "")


@dataclass(frozen=True)
class Chunk:
    """A heading and what follows it up to the next heading; the text before the first heading is
    a chunk with no heading."""

    heading: str | None
    text: str


@dataclass(frozen=True)
class Problem:
    section: str  # one of ``REQUIRED``, ``links``, ``readme``
    message: str
    heading: str | None = None  # the chunk it is about; ``None``: the file as a whole


def chunks(text: str) -> list[Chunk]:
    found: list[Chunk] = []
    heading: str | None = None
    lines: list[str] = []
    fenced = ""
    for line in text.split("\n"):
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)[0]
            fenced = "" if fenced == marker else fenced or marker
        match = None if fenced else _HEADING.match(line)
        if match:
            found.append(Chunk(heading, "\n".join(lines).rstrip()))
            heading, lines = match.group(2), [line]
        else:
            lines.append(line)
    found.append(Chunk(heading, "\n".join(lines).rstrip()))
    return [chunk for chunk in found if chunk.heading is not None or chunk.text.strip()]


def _program(word: str) -> str:
    name = PurePosixPath(word.replace("\\", "/")).name.lower()
    name = re.sub(r"\.(exe|cmd|bat|ps1)$", "", name)
    return _ALIASES.get(name, name)


def _unwrapped(program: str, words: list[str]) -> list[str] | None:
    """What a launcher runs (``python -m X``, ``uv run X``), or ``None`` for a plain program."""
    second = words[1] if len(words) > 1 else None
    if program in _PYTHONS and second == "-m" and len(words) > 2:
        return words[2:]
    if second is not None and _WRAPPERS.get(program) == second:
        return words[2:]
    return None


def signature(tokens: Sequence[str]) -> Signature | None:
    """Program and subcommand of a command line, looking through launchers."""
    words = list(tokens)
    while words:
        program = _program(words[0])
        inner = _unwrapped(program, words)
        if inner is not None:
            words = inner
            continue
        rest = [w for w in words[1:] if not w.startswith("-")]
        word = rest[0] if rest and _WORD.fullmatch(rest[0]) else ""
        return program, word if program in SUBCOMMANDED else ""
    return None


def cited_commands(chunk: str) -> list[Signature]:
    """The commands a section tells the reader to run: the lines of its code blocks."""
    found: list[Signature] = []
    fenced = ""
    for line in chunk.split("\n"):
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)[0]
            fenced = "" if fenced == marker else fenced or marker
            continue
        text = _PROMPT.sub("", line).strip()
        if fenced and text and not text.startswith("#"):
            known = signature(text.split())
            if known:
                found.append(known)
    return found


def _matches(cited: Signature, run: Signature) -> bool:
    return cited[0] == run[0] and (not cited[1] or not run[1] or cited[1] == run[1])


def _satisfied(chunk: Chunk, name: str, pattern: str) -> bool:
    """Whether a word of the heading starts with one of the stems of ``pattern``: "Running" and
    "Como rodar" are about ``run``; "Trunk" and "Latest" hold a stem and are about nothing."""
    if chunk.heading is None:
        return False
    return re.search(rf"(?<![^\W\d_])(?:{pattern})", chunk.heading, re.IGNORECASE) is not None


def evaluate(
    text: str | None,
    exists: Callable[[str], bool],
    build: Iterable[Signature] = (),
    test: Iterable[Signature] = (),
) -> list[Problem]:
    """What is wrong with the README. ``exists`` says whether a path relative to the README's
    folder is there; ``build`` and ``test`` are the commands the adapters run for those operations
    (none: the commands of that section are not compared)."""
    if text is None:
        return [Problem("readme", NO_README)]
    parts = chunks(text)
    problems: list[Problem] = []
    matched: dict[str, Chunk] = {}
    for part in parts:
        for name, pattern in REQUIRED:
            if name not in matched and _satisfied(part, name, pattern):
                matched[name] = part
                break
    opening = parts[0] if parts else None
    if "what" not in matched and opening is not None and _says_something(opening):
        matched["what"] = opening
    for name, _ in REQUIRED:
        if name not in matched:
            problems.append(Problem(name, f"the README has no section for {name}"))
    for name, runs in (("build", set(build)), ("test", set(test))):
        section = matched.get(name)
        if section is not None and runs:
            problems += _commands(name, section, runs)
    return problems + _links(parts, exists)


def _says_something(chunk: Chunk) -> bool:
    """Whether the opening of the README (the title, or text before any heading) has a body."""
    lines = chunk.text.split("\n")
    body = lines[1:] if chunk.heading is not None else lines
    return any(line.strip() for line in body)


def _commands(name: str, part: Chunk, runs: set[Signature]) -> list[Problem]:
    cited = cited_commands(part.text)
    shown = ", ".join(sorted(" ".join(filter(None, run)) for run in runs))
    if not cited:
        return [
            Problem(
                name,
                f"the {name} section cites no command (the project runs {shown})",
                part.heading,
            )
        ]
    if not any(_matches(c, r) for c in cited for r in runs):
        said = ", ".join(sorted({" ".join(filter(None, c)) for c in cited}))
        return [
            Problem(
                name, f"the {name} section cites {said}; the project runs {shown}", part.heading
            )
        ]
    return []


def _links(parts: Sequence[Chunk], exists: Callable[[str], bool]) -> list[Problem]:
    problems = []
    anchors = {_slug(p.heading) for p in parts if p.heading}
    for part in parts:
        for target in _LINK.findall(_without_code(part.text)):
            if _SCHEME.match(target):
                continue
            path, _, anchor = target.partition("#")
            if path and not exists(path):
                problems.append(
                    Problem("links", f"the link to {path} points at nothing", part.heading)
                )
            elif not path and anchor and anchor.lower() not in anchors:
                problems.append(
                    Problem("links", f"the link to #{anchor} points at no heading", part.heading)
                )
    return problems


def _without_code(text: str) -> str:
    """The text with fenced blocks and code spans taken out: a link in an example is not a link."""
    kept, fenced = [], ""
    for line in text.split("\n"):
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)[0]
            fenced = "" if fenced == marker else fenced or marker
        elif not fenced:
            kept.append(_SPAN.sub("", line))
    return "\n".join(kept)


def _slug(heading: str) -> str:
    """How a Markdown host turns a heading into an anchor."""
    words = re.sub(r"[^\w\s-]", "", heading.lower())
    return re.sub(r"\s", "-", words.strip())


def touched_chunks(old: str, new: str, problems: Sequence[Problem]) -> list[str]:
    """The headings of sections that were fine and are not the same any more.

    A section that has a problem may change; one that has none may not. A section the new text
    adds is allowed only when the old README lacked a required section.
    """
    allowed = {p.heading for p in problems if p.heading is not None}
    kept = {c.text for c in chunks(new)}
    changed = [
        c.heading or "(start of the file)"
        for c in chunks(old)
        if c.heading not in allowed and c.text not in kept
    ]
    old_headings = {c.heading for c in chunks(old)}
    adds_allowed = any(p.heading is None and p.section != "links" for p in problems)
    changed += [
        f"{c.heading} (new)"
        for c in chunks(new)
        if c.heading is not None and c.heading not in old_headings and not adds_allowed
    ]
    return changed
