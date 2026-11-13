"""The versioned list of patterns that mark text a model wrote on autopilot (gate 12).

Nobody can prove that text sounds human; what the Forja can do is refuse what is shown to help no
one: openings that greet, a model naming itself, inflated vocabulary, offers to help further, and
sentences that only announce that something matters. Every entry says why it is on the list, so a
person who disagrees knows what they are turning off. The list grows only with a new ``VERSION``.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

VERSION = 2  # 2: emoji left the list; it is part of the voice (see ``voice_brief``)
MAX_DASHES = 3  # em dashes in the text one file adds before it reads as excess


# The patterns are written with the plain apostrophe; text typed in an editor or pasted from a
# model often has the curly ones, and ``it’s worth noting`` says what ``it's worth noting`` says.
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'"})


@dataclass(frozen=True)
class Marker:
    id: str
    pattern: re.Pattern[str]
    why: str

    def found_in(self, text: str) -> bool:
        """Whether ``text`` has the marker, whatever apostrophe it was written with."""
        return self.pattern.search(text.translate(_APOSTROPHES)) is not None


def _marker(id: str, pattern: str, why: str) -> Marker:
    return Marker(id, re.compile(pattern, re.IGNORECASE), why)


def build_markers(extra: Iterable[str] = (), off: Iterable[str] = ()) -> tuple[Marker, ...]:
    """The Forja's list without the markers the repository turned off, then the repository's own
    patterns. An id nobody has is ignored: the list stays as strict as it was."""
    turned_off = set(off)
    added = tuple(
        _marker(f"repository-{number}", pattern, "a pattern the repository forbids")
        for number, pattern in enumerate(extra, start=1)
    )
    return (*(m for m in MARKERS if m.id not in turned_off), *added)


MARKERS = (
    _marker(
        "greeting",
        r"^\W*(?:sure|certainly|of course|absolutely|great question|claro|certamente|com certeza"
        r"|ótima pergunta)\b\s*[,!.]",
        "an opening that greets instead of saying something",
    ),
    _marker(
        "self-reference",
        r"\bas an ai\b|\bas a language model\b"
        r"|\bcomo (?:uma )?(?:ia|inteligência artificial|modelo de linguagem)\b",
        "the model talks about itself",
    ),
    _marker(
        "inflated",
        r"\bdelv(?:e|es|ed|ing) (?:in|into)\b|\btapestry\b|\btestament to\b|\bever-evolving\b"
        r"|\bin today's (?:fast-paced|digital)\b|\bgame-changer\b|\bparadigm shift\b"
        r"|\bno cenário (?:atual|digital)\b|\bdivisor de águas\b",
        "vocabulary that inflates and says nothing",
    ),
    _marker(
        "announcement",
        r"\bit(?:'s| is) (?:important|worth) (?:to )?not(?:e|ing) that\b"
        r"|\b(?:vale (?:a pena )?|é importante )(?:ressaltar|destacar|notar) que\b",
        "a sentence that announces that something matters instead of saying it",
    ),
    _marker(
        "offer",
        r"\bi hope this helps\b|\blet me know if you (?:need|have|want)\b"
        r"|\bespero que (?:isso|isto) ajude\b|\bfico à disposição\b",
        "an offer of more help, to a reader who is a repository",
    ),
)
