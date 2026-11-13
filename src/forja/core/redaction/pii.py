"""Personal-data detectors (LGPD): Brazilian documents and phones, e-mail, payment cards.

Every detector validates what its pattern finds (check digits, Luhn, area codes, reserved domains)
so ordinary numbers such as versions, ids and timestamps are left alone.
"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from forja.core.redaction.settings import PiiKind

_ALL_DDD = frozenset(
    {
        *range(11, 20),
        21, 22, 24, 27, 28,
        *range(31, 36), 37, 38,
        *range(41, 50),
        51, *range(53, 56),
        *range(61, 70),
        71, 73, 74, 75, 77, 79,
        *range(81, 90),
        *range(91, 100),
    }
)  # fmt: skip
_RESERVED_DOMAINS = frozenset({"example.com", "example.org", "example.net", "localhost"})
_RESERVED_SUFFIXES = (".test", ".invalid", ".example", ".localhost", ".local")
_TECHNICAL_LOCAL_PARTS = frozenset({"git", "hg", "svn", "ssh"})


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def cpf_valid(text: str) -> bool:
    digits = _digits(text)
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for size in (9, 10):
        total = sum(int(digits[i]) * (size + 1 - i) for i in range(size))
        check = (total * 10 % 11) % 10
        if check != int(digits[size]):
            return False
    return True


def cnpj_valid(text: str) -> bool:
    digits = _digits(text)
    if len(digits) != 14 or len(set(digits)) == 1:
        return False
    for size, weights in (
        (12, (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)),
        (13, (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)),
    ):
        total = sum(int(digits[i]) * weights[i] for i in range(size))
        rest = total % 11
        check = 0 if rest < 2 else 11 - rest
        if check != int(digits[size]):
            return False
    return True


def _luhn_terms(digits: str) -> Iterator[int]:
    for index, char in enumerate(reversed(digits)):
        value = int(char) * (2 if index % 2 else 1)
        yield value - 9 if value > 9 else value


def luhn_valid(text: str) -> bool:
    digits = _digits(text)
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    return sum(_luhn_terms(digits)) % 10 == 0 and "2" <= digits[0] <= "6"


def email_valid(text: str) -> bool:
    local, _, domain = text.rpartition("@")
    domain = domain.lower()
    if local.lower() in _TECHNICAL_LOCAL_PARTS:
        return False
    if domain in _RESERVED_DOMAINS or domain.endswith(_RESERVED_SUFFIXES):
        return False
    return not any(domain.endswith("." + reserved) for reserved in _RESERVED_DOMAINS)


def phone_valid(text: str) -> bool:
    digits = _digits(text)
    if text.lstrip().startswith("+"):
        if digits.startswith("55"):
            national = digits[2:]
            return len(national) in (10, 11) and int(national[:2]) in _ALL_DDD
        return 8 <= len(digits) <= 15 and digits[0] != "0"
    return len(digits) in (10, 11) and int(digits[:2]) in _ALL_DDD


@dataclass(frozen=True)
class PiiDetector:
    kind: PiiKind
    patterns: tuple[re.Pattern[str], ...]
    valid: Callable[[str], bool]


# Lookarounds keep a match from starting or ending inside a longer run of digits or separators.
_NOT_BEFORE = r"(?<![\d.\-/])"
_NOT_AFTER = r"(?![\d])"

DETECTORS: tuple[PiiDetector, ...] = (
    PiiDetector(
        PiiKind.CPF,
        # with its dots and hyphen, with blanks in their place, or with nothing
        (re.compile(_NOT_BEFORE + r"\d{3}[. ]?\d{3}[. ]?\d{3}[- ]?\d{2}" + _NOT_AFTER),),
        cpf_valid,
    ),
    PiiDetector(
        PiiKind.CNPJ,
        (re.compile(_NOT_BEFORE + r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}" + _NOT_AFTER),),
        cnpj_valid,
    ),
    PiiDetector(
        PiiKind.EMAIL,
        (
            re.compile(
                r"(?<![\w.+\-])[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}"
                r"(?:\.[A-Za-z0-9\-]{1,63}){1,8}(?![\w@])"
            ),
        ),
        email_valid,
    ),
    PiiDetector(
        PiiKind.PHONE,
        (
            # +55 with or without separators
            re.compile(r"(?<![\w+])\+55[\s.\-]?\(?\d{2}\)?[\s.\-]?9?\d{4}[\s.\-]?\d{4}(?!\d)"),
            # (DD) 9NNNN-NNNN, with the area code in parentheses
            re.compile(r"(?<![\w])\(\d{2}\)\s?9?\d{4}[\s\-]?\d{4}(?!\d)"),
            # DD 9NNNN-NNNN, with a space or a dot after the area code
            re.compile(r"(?<![\w.\-])\d{2}[\s.]9?\d{4}[\s.\-]\d{4}(?![\d\-])"),
            # DD9NNNNNNNN, a mobile number with nothing between its digits
            re.compile(r"(?<![\w.\-+])[1-9]\d9\d{8}(?!\d)"),
            # other countries, E.164
            re.compile(r"(?<![\w+])\+(?!55)[1-9]\d{7,14}(?!\d)"),
        ),
        phone_valid,
    ),
    PiiDetector(
        PiiKind.CARD,
        (re.compile(r"(?<![\d\-])\d(?:[ \-]?\d){12,18}(?![\d\-])"),),
        luhn_valid,
    ),
)
