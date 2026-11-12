"""Secret detectors: short, high-confidence rules, the same family gitleaks ships.

Patterns use bounded repetition and no nested quantifiers, so a hostile input cannot make them
run for long. They run over the whole text of an item, so keys that span lines are found too.
The set is versioned: change a rule, bump ``RULES_VERSION``, and the fingerprint test says so.
"""

import hashlib
import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

RULES_VERSION = "2026.11.1"


def shannon_entropy(value: str) -> float:
    """Bits per character; random keys sit near 4 or above, words and identifiers well below."""
    if not value:
        return 0.0
    total = len(value)
    return -sum(count / total * math.log2(count / total) for count in Counter(value).values())


_DOC_VALUES = frozenset(
    {
        "akiaiosfodnn7example",
        "wjalrxutnfemi/k7mdeng/bpxrficyexamplekey",
        "changeme",
        "change_me",
        "change-me",
        "your-api-key-here",
        "your_api_key",
        "your_api_key_here",
        "your-secret-here",
        "password",
        "secret",
        "null",
        "none",
        "undefined",
    }
)
_PLACEHOLDER_WORDS = ("example", "placeholder", "changeme", "dummy", "your-", "your_", "<", "xxxx")
_FILLER = re.compile(r"^(?:x+|\*+|\.+|-+|_+|0+)$", re.IGNORECASE)
_REDACTED = re.compile(r"^\[(?:REDACTED|PII):")


def is_placeholder(value: str) -> bool:
    """Values that are obviously not real secrets: documentation keys, fillers, our own masks."""
    lowered = value.lower()
    return (
        lowered in _DOC_VALUES
        or bool(_FILLER.match(value))
        or bool(_REDACTED.match(value))
        or any(word in lowered for word in _PLACEHOLDER_WORDS)
    )


def _looks_like_a_secret_value(value: str) -> bool:
    """A value after ``name = `` that is more than an identifier or a plain number."""
    if value.isdigit():
        return False
    return any(ch.isdigit() for ch in value) or any(ch in "+/=!@#$%^&*-" for ch in value)


@dataclass(frozen=True)
class SecretRule:
    """One detector. ``group`` is the part of the match that is the secret (0 = all of it).

    ``keywords`` is a cheap prefilter: the rule only runs if one of them occurs in the text.
    ``uses_threshold`` applies the repository's entropy threshold instead of a fixed one.
    """

    id: str
    pattern: re.Pattern[str]
    group: int = 0
    keywords: tuple[str, ...] = ()
    min_entropy: float | None = None
    uses_threshold: bool = False
    check: Callable[[str], bool] | None = None


_KEY_NAMES = (
    r"(?:secret|token|passw(?:or)?d|pwd|api[_\-]?key|auth[_\-]?key|private[_\-]?key|credential)"
)

RULES: tuple[SecretRule, ...] = (
    SecretRule(
        "private-key-block",
        re.compile(
            r"-----BEGIN (?:[A-Z0-9]{1,20} ){0,3}PRIVATE KEY(?: BLOCK)?-----[\s\S]{0,16384}?"
            r"-----END (?:[A-Z0-9]{1,20} ){0,3}PRIVATE KEY(?: BLOCK)?-----"
        ),
        keywords=("private key",),
    ),
    SecretRule(
        "private-key-header",
        re.compile(
            r"-----BEGIN (?:[A-Z0-9]{1,20} ){0,3}PRIVATE KEY(?: BLOCK)?-----"
            r"[A-Za-z0-9+/=\s]{0,4096}"
        ),
        keywords=("private key",),
    ),
    SecretRule(
        "aws-access-key",
        re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16}\b"),
        keywords=("akia", "asia", "abia", "acca"),
    ),
    SecretRule(
        "aws-secret-key",
        re.compile(
            r"aws.{0,30}?(?:secret|private).{0,30}?[:=]\s{0,4}[\"']?([A-Za-z0-9/+=]{40})(?![A-Za-z0-9/+=])",
            re.IGNORECASE,
        ),
        group=1,
        keywords=("aws",),
    ),
    SecretRule(
        "github-token",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,255}\b"),
        keywords=("gh",),
    ),
    SecretRule(
        "github-fine-grained-token",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b"),
        keywords=("github_pat_",),
    ),
    SecretRule(
        "gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,255}\b"), keywords=("glpat-",)
    ),
    SecretRule(
        "slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,250}\b"), keywords=("xox",)
    ),
    SecretRule(
        "stripe-key",
        re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,247}\b"),
        keywords=("_live_", "_test_"),
    ),
    SecretRule("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), keywords=("aiza",)),
    SecretRule(
        "azure-storage-key",
        re.compile(r"accountkey=([A-Za-z0-9+/]{86}==)", re.IGNORECASE),
        group=1,
        keywords=("accountkey",),
    ),
    SecretRule("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"), keywords=("npm_",)),
    SecretRule(
        "pypi-token",
        re.compile(r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_\-]{50,}\b"),
        keywords=("pypi-",),
    ),
    SecretRule(
        "anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,255}\b"), keywords=("sk-ant-",)
    ),
    SecretRule(
        "openai-key",
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,255}\b"),
        keywords=("sk-",),
        min_entropy=3.5,
    ),
    SecretRule(
        "jwt",
        re.compile(
            r"\beyJ[A-Za-z0-9_\-]{10,2000}\.eyJ[A-Za-z0-9_\-]{10,2000}\.[A-Za-z0-9_\-]{10,2000}\b"
        ),
        keywords=("eyj",),
    ),
    SecretRule(
        "url-credentials",
        re.compile(r"\b[a-z][a-z0-9+.\-]{1,20}://[^\s/:@\"']{1,64}:([^\s/@\"']{3,128})@"),
        group=1,
        keywords=("://",),
        check=lambda value: not is_placeholder(value) and not value.startswith(("$", "{", "%")),
    ),
    SecretRule(
        "connection-string-password",
        re.compile(
            r"(?<![\w.])(?:password|pwd)=([^;\s\"'<>{}$%()]{3,128});",
            re.IGNORECASE,
        ),
        group=1,
        keywords=("password", "pwd"),
        check=lambda value: not is_placeholder(value),
    ),
    SecretRule(
        "authorization-header",
        re.compile(
            r"authorization[\"']?\s{0,4}[:=]\s{0,4}[\"']?(?:bearer|basic|token)\s{1,4}"
            r"([A-Za-z0-9_\-.=+/]{16,2000})",
            re.IGNORECASE,
        ),
        group=1,
        keywords=("authorization",),
        check=lambda value: not is_placeholder(value),
    ),
    SecretRule(
        "generic-credential",
        re.compile(
            r"\b[A-Za-z0-9_.\-]{0,32}" + _KEY_NAMES + r"[A-Za-z0-9_.\-]{0,32}[\"']?\s{0,4}"
            r"(?::=|=>|[:=])\s{0,4}[\"']?([^\s\"'`;,(){}\[\]<>]{8,200})",
            re.IGNORECASE,
        ),
        group=1,
        keywords=("secret", "token", "passw", "pwd", "key", "credential"),
        uses_threshold=True,
        check=lambda value: _looks_like_a_secret_value(value) and not is_placeholder(value),
    ),
    # A value of letters only is an identifier in code (``password = some_function``, and in
    # quotes the value of an enumeration), so the rule above leaves it. In an environment file
    # (``NAME=value`` alone on its line, the name in capitals) it is a value somebody typed.
    SecretRule(
        "environment-credential",
        re.compile(
            r"^(?:export[ 	]+)?[A-Z0-9_]{0,32}"
            r"(?:SECRET|TOKEN|PASSW(?:OR)?D|PWD|API_?KEY|AUTH_?KEY|PRIVATE_?KEY|CREDENTIAL)"
            r"[A-Z0-9_]{0,32}=([A-Za-z_]{8,200})[ 	]*$",
            re.MULTILINE,
        ),
        group=1,
        keywords=("secret", "token", "passw", "pwd", "key", "credential"),
        uses_threshold=True,
        check=lambda value: not is_placeholder(value),
    ),
)


def rules_fingerprint() -> str:
    """Hash of every rule definition: a test pins it so a change cannot go unversioned."""
    parts = [
        f"{rule.id}|{rule.pattern.pattern}|{rule.pattern.flags}|{rule.group}|{rule.min_entropy}"
        f"|{rule.uses_threshold}|{','.join(rule.keywords)}"
        for rule in RULES
    ]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def rules_version() -> str:
    return f"{RULES_VERSION}+{rules_fingerprint()[:8]}"
