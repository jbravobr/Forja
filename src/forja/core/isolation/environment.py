"""The environment an agent or repository tool runs with: an allow-list, never a deny-list."""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

BASE_ENVIRONMENT_ALLOW: frozenset[str] = frozenset(
    {"PATH", "HOME", "USERPROFILE", "TEMP", "TMP", "SYSTEMROOT", "LANG", "TERM"}
)
_LOCALE_PREFIX = "LC_"
_SECRET_LIKE = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL", re.IGNORECASE)


@dataclass(frozen=True)
class FilteredEnvironment:
    values: dict[str, str]
    # Names (never values) of secret-looking variables that were present and kept out.
    withheld: tuple[str, ...]


def looks_secret(name: str) -> bool:
    return bool(_SECRET_LIKE.search(name))


def filter_environment(
    host: Mapping[str, str],
    *,
    adapter_allow: Iterable[str] = (),
    base_allow: Iterable[str] = BASE_ENVIRONMENT_ALLOW,
    extra: Mapping[str, str] | None = None,
) -> FilteredEnvironment:
    """Keep only the variables the process needs.

    ``base_allow`` is the generic set. ``adapter_allow`` is what one executor adapter declares for
    its own CLI (gateway URL, the CLI's credential); those pass even though they look secret,
    because the credential of the CLI is the CLI's business. A secret-looking name that comes only
    from the base list is held back, whoever put it there. ``extra`` is set by Forja itself and
    added last. Names compare case-insensitively (Windows), and the host's spelling is kept.
    """
    declared = {name.upper() for name in adapter_allow}
    generic = {name.upper() for name in base_allow}
    values: dict[str, str] = {}
    withheld: list[str] = []
    for name, value in host.items():
        key = name.upper()
        if key in declared:
            values[name] = value
        elif key in generic or key.startswith(_LOCALE_PREFIX):
            if looks_secret(name):
                withheld.append(name)
            else:
                values[name] = value
    values.update(extra or {})
    return FilteredEnvironment(values, tuple(sorted(withheld)))


def git_blocking_environment(workcopy: Path) -> dict[str, str]:
    """Variables that make `git` fail inside the agent's copy even if a repository lies above it.

    ``GIT_CEILING_DIRECTORIES`` stops git from searching upward from the copy. ``GIT_DIR`` points
    to a path that does not exist, so git reports "not a git repository" instead of discovering one.
    """
    return {
        "GIT_CEILING_DIRECTORIES": str(workcopy.parent),
        "GIT_DIR": str(workcopy.parent / ".forja-no-git-repository"),
    }


INSTALL_ENVIRONMENT: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USERPROFILE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "APPDATA",
        "LOCALAPPDATA",
        "LANG",
        "LC_ALL",
        "PATHEXT",
        "COMSPEC",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "PIP_CERT",
        "PIP_NO_INDEX",
        "PIP_FIND_LINKS",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    }
)  # fmt: skip  what pip needs; the installer runs package code, so no keys or tokens


def install_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """The variables a package installer runs with. Names compare case-insensitively."""
    return {name: value for name, value in environ.items() if name.upper() in INSTALL_ENVIRONMENT}
