"""Gate 10: the changed dependencies bring no new vulnerability and no forbidden license.

Only the files OSV-Scanner reads (lockfiles, and the manifests it understands) are audited, and
only when one of them changed. The old version is audited from the parent commit's text in a
private copy of the worktree, and what was already there never reproves: a vulnerability counts
per (package, version, advisory), a license per (package, version, license). The forbidden
licenses are the repository's own list; without one only vulnerabilities are audited.
A tool that cannot run, or a package whose license is unknown, is inconclusive, never a pass.
"""

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from forja.checks.base import Undecided, decided, rejected
from forja.checks.changes import Changes
from forja.checks.oldtree import old_tree
from forja.checks.tooling import Tools
from forja.core.adapters import CommandPlan
from forja.core.gates import (
    Approved,
    Finding,
    GateInput,
    GateResult,
    Inconclusive,
    InconclusiveReason,
)

# What OSV-Scanner reads (google.github.io/osv-scanner, "Supported languages and lockfiles") for
# the six languages. ``package.json`` is not read; a change there is ``new_dependency``'s.
ARTIFACTS = frozenset(
    {
        "requirements.txt", "Pipfile.lock", "poetry.lock", "pdm.lock", "uv.lock", "pylock.toml",
        "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock",
        "go.mod",
        "pom.xml", "gradle.lockfile", "buildscript-gradle.lockfile",
        "packages.lock.json", "packages.config", "deps.json",
    }
)  # fmt: skip
NOTHING_TO_AUDIT = 128  # OSV-Scanner: no package found
FOUND = 1  # OSV-Scanner: something was found; the report says what
Package = tuple[str, str, str]  # (ecosystem, name, version)
LISTED_ADVISORIES = 3  # advisories named in a finding; the rest are counted


# SPDX ids that osv-scanner accepts and that (nearly) no package uses; it refuses ids that are
# not SPDX, so an invented name will not do
UNUSED_LICENSES = ("Glulxe", "xlock", "Wsuipa")
UNKNOWN_LICENSES = frozenset({"unknown", "non-standard", "noassertion", "none"})
EXPRESSION_WORDS = frozenset({"and", "or", "with"})


def _vulnerability_message(package: Package, advisories: list[str]) -> str:
    where = f"{package[1]} {package[2]}"
    if len(advisories) == 1:
        return f"new vulnerability {advisories[0]} in {where}"
    shown = ", ".join(advisories[:LISTED_ADVISORIES])
    more = len(advisories) - LISTED_ADVISORIES
    tail = f" and {more} more" if more > 0 else ""
    return f"{len(advisories)} new vulnerabilities in {where}: {shown}{tail}"


def _identifiers(license_id: str) -> set[str]:
    """The license ids an SPDX expression names, lowercase: ``(MIT OR GPL-3.0-only)`` names both.
    A forbidden id anywhere in the expression counts, even where an ``OR`` leaves a choice: the
    audit errs on the side of reproving."""
    words = license_id.replace("(", " ").replace(")", " ").lower().split()
    return {word for word in words if word not in EXPRESSION_WORDS}


def _known(found: frozenset[str]) -> bool:
    """Whether the report names a license: something other than nothing or a placeholder."""
    return any(_identifiers(item) - UNKNOWN_LICENSES for item in found)


def is_artifact(path: str) -> bool:
    return PurePosixPath(path.replace("\\", "/")).name in ARTIFACTS


@dataclass(frozen=True)
class Audit:
    vulnerabilities: dict[tuple[Package, str], str]  # (package, advisory) -> the file it is in
    licenses: dict[Package, frozenset[str]]
    # how many packages the whole scan has under each license id (lowercase), and how many of
    # the packages the report lists: the two differ when a package was left out of the list
    summary: Mapping[str, int] = field(default_factory=dict)
    listed: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class DependencyAuditCheck:
    tools: Tools
    worktree: Path
    changes: Changes
    before: dict[str, str]  # the parent commit's text of the changed artifacts, by old path
    reports: Path  # a directory outside the worktree for the reports
    forbidden: frozenset[str] = frozenset()  # lowercase SPDX ids; none, no license audit
    osv_scanner: tuple[str, ...] = ("osv-scanner",)
    id: str = "dependency_audit"
    gate: int = 10
    requires: frozenset[str] = frozenset()

    async def evaluate(self, data: GateInput) -> GateResult:
        return await decided(self._judge())

    async def _judge(self) -> GateResult:
        files = self.changes.current(is_artifact)
        if not files:
            return Approved()
        self.reports.mkdir(parents=True, exist_ok=True)
        now = await self._audit(self.worktree, files, "dependency_audit")
        was = await self._before()
        findings = [
            *self._vulnerabilities(now, was),
            *self._licenses(now, was),
        ]
        if findings:
            return rejected(findings)
        hidden = self._unlisted(now)
        if hidden:
            raise Undecided(
                Inconclusive(
                    reason=InconclusiveReason.TOOL_FAILED,
                    detail=f"dependency_audit: {', '.join(sorted(hidden))} is in the scan "
                    "but in no package the report lists",
                )
            )
        unknown = sorted(
            f"{package[1]} {package[2]}"
            for package, found in now.licenses.items()
            if self.forbidden and not _known(found) and package not in was.licenses
        )
        if unknown:
            raise Undecided(
                Inconclusive(
                    reason=InconclusiveReason.TOOL_FAILED,
                    detail=f"dependency_audit: the license of {', '.join(unknown[:3])} is unknown",
                )
            )
        return Approved()

    @staticmethod
    def _vulnerabilities(now: Audit, was: Audit) -> list[Finding]:
        """One finding per package and file: a package with dozens of advisories is one line that
        names the first few and counts the rest."""
        grouped: dict[tuple[Package, str], list[str]] = {}
        for (package, advisory), source in sorted(now.vulnerabilities.items()):
            if (package, advisory) not in was.vulnerabilities:
                grouped.setdefault((package, source), []).append(advisory)
        return [
            Finding(message=_vulnerability_message(package, advisories), target=source)
            for (package, source), advisories in grouped.items()
        ]

    def _licenses(self, now: Audit, was: Audit) -> list[Finding]:
        findings = []
        for package, found in sorted(now.licenses.items()):
            old = was.licenses.get(package, frozenset())
            for license_id in sorted(found - old):
                if _identifiers(license_id) & self.forbidden:
                    _, name, version = package
                    message = f"forbidden license {license_id} in {name} {version}"
                    findings.append(Finding(message=message, target=name))
        return findings

    def _unlisted(self, now: Audit) -> frozenset[str]:
        """Forbidden licenses the scan counts (``license_summary``) in more packages than the
        report lists: a package osv-scanner left out of its list, which must not pass as one
        without the license."""
        return frozenset(
            word for word in self.forbidden if now.summary.get(word, 0) > now.listed.get(word, 0)
        )

    def _allowed(self) -> str:
        """OSV-Scanner only has a list of allowed licenses, and it lists the licenses of a package
        only when the package has a vulnerability or breaks that list (seen with osv-scanner
        2.7.0: with the plain ``--licenses`` a package without vulnerabilities is not listed). So
        the list given is one that no package is expected to meet, and every package is then
        listed with its licenses; the forbidden ones are compared here."""
        return next(name for name in UNUSED_LICENSES if name.lower() not in self.forbidden)

    async def _before(self) -> Audit:
        if not self.before:
            return Audit({}, {})
        with old_tree(self.worktree, self.changes, self.before, "forja-audit-before-") as root:
            return await self._audit(root, tuple(sorted(self.before)), "dependency_audit_before")

    async def _audit(self, root: Path, files: tuple[str, ...], name: str) -> Audit:
        report = self.reports / f"{name}.json"
        report.unlink(missing_ok=True)
        plan = CommandPlan(
            (
                *self.osv_scanner,
                "scan",
                "--format",
                "json",
                "--output-file",
                str(report),
                *((f"--licenses={self._allowed()}",) if self.forbidden else ()),
                *(arg for path in files for arg in ("-L", _argument(path))),
            ),
            root,
        )
        execution = await self.tools.run(name, plan)
        if not execution.finished:
            raise Undecided(execution.inconclusive(name))
        code = execution.result.exit_code
        if code == NOTHING_TO_AUDIT:
            return Audit({}, {})
        audit = _read(report, root) if code in (0, FOUND) else None
        if audit is None:
            detail = (
                f"{name}: osv-scanner ended with {code}"
                if code not in (0, FOUND)
                else f"{name}: the osv-scanner report cannot be read"
            )
            raise Undecided(
                Inconclusive(
                    reason=InconclusiveReason.TOOL_FAILED,
                    detail=detail,
                    tool_run_ids=(execution.operation_id,),
                )
            )
        return audit


def _argument(path: str) -> str:
    """A path that no option can be mistaken for."""
    return f"./{path}" if path.startswith("-") else path


def _relative(source: str, root: Path) -> str:
    """Where a report says a file is, as a path inside ``root`` when it is one."""
    path = Path(source)
    if path.is_absolute():
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return path.as_posix()
    return path.as_posix().removeprefix("./")


def _read(report: Path, root: Path) -> Audit | None:
    """The vulnerabilities and licenses of an OSV-Scanner JSON report, ``None`` if it is not one."""
    vulnerabilities: dict[tuple[Package, str], str] = {}
    licenses: dict[Package, frozenset[str]] = {}
    try:
        document = json.loads(report.read_text(encoding="utf-8"))
        results = document.get("results", [])
        summary: Counter[str] = Counter()
        for item in document.get("license_summary") or []:
            for word in _identifiers(item["name"]):
                summary[word] += int(item["count"])
        listed: Counter[str] = Counter()
        for result in results:
            source = _relative(str(result["source"]["path"]), root)
            for entry in result["packages"]:
                info = entry["package"]
                package = (str(info["ecosystem"]), str(info["name"]), str(info["version"]))
                for vulnerability in entry.get("vulnerabilities") or []:
                    vulnerabilities.setdefault((package, str(vulnerability["id"])), source)
                found = frozenset(str(item) for item in entry.get("licenses") or [])
                licenses[package] = licenses.get(package, frozenset()) | found
                listed.update({word for item in found for word in _identifiers(item)})
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return Audit(vulnerabilities, licenses, summary, listed)
