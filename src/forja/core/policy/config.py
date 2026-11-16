"""Policy configuration: TOML layers merged over code defaults and validated as a whole.

The TOML only sets values. Which checks exist, what each may be configured with and what it does
by default is decided in code (``CheckRegistry``). Every problem is collected, so one failed load
reports everything to fix at once.
"""

import hashlib
import math
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from forja.core.artifacts.llm_output import check_relative_path
from forja.core.events.models import canonical_json
from forja.core.gates.effects import CheckSettings, InvalidCheckSettings
from forja.core.gates.models import InconclusivePolicy, Mode
from forja.core.isolation.sensitive import compile_pattern
from forja.core.policy.differential import (
    DifferentialCase,
    merge_differential,
    validate_differential,
)
from forja.core.policy.registry import LANGUAGES, CheckRegistry, CheckSpec
from forja.core.redaction.settings import (
    PiiKind,
    RedactionSettings,
    RedactionSettingsError,
    compile_extra,
)

POLICY_SCHEMA_VERSION = 1
_CHECK_KEYS = {"mode", "on_inconclusive", "promotion", "thresholds", "commands", "limits"}
# Gate 12 measures and reports; the Forja steers the voice by instructing the agent (voice_brief),
# never by rejecting what it wrote. Owner's decision of 31/10/2026
# (docs/guia/documentacao-gerada.md).
NEVER_BLOCKING = frozenset({"voice", "comments"})
# Kept in step with adapters.language.Operation (a test compares them); importing it here would
# make the two packages import each other. ``mutation`` is left out: no adapter plans it and the
# mutation gate runs its own executor, so a command for it would never be read. So is
# ``test_lines``: its command only works with the piece the Forja puts in the run of the tests.
ADAPTER_OPERATIONS = frozenset(
    {"format", "lint", "lint_fix", "types", "build", "test", "coverage", "e2e"}
)

# The tables a forja.toml may hold and the keys each accepts (``checks`` is keyed by check id, and
# ``differential`` and ``architecture`` hold lists of tables). The user guide must name every one.
SECTION_KEYS: dict[str, frozenset[str]] = {
    "checks": frozenset(),
    "sensitive": frozenset({"paths"}),
    "branches": frozenset({"protected"}),
    "adapters": frozenset({"commands"}),
    "redaction": frozenset({"patterns", "allow", "allow_patterns", "pii", "entropy_threshold"}),
    "tests": frozenset({"root"}),
    "licenses": frozenset({"forbidden"}),
    "voice": frozenset({"markers_extra", "markers_off"}),
    "architecture": frozenset({"layers"}),
    "differential": frozenset({"cases"}),
}

Number = int | float
# The checks read these as whole numbers and drop a float without a word.
_WHOLE_LIMITS = frozenset(
    {"min_tokens", "min_lines", "workers", "line_tests", "max_files", "max_lines", "max_dashes"}
)


class PolicyConfigError(Exception):
    """The configuration is invalid; ``problems`` lists everything wrong, with its source."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("invalid policy configuration:\n- " + "\n- ".join(self.problems))


@dataclass(frozen=True)
class Layer:
    """One parsed configuration source. Later layers override earlier ones, value by value."""

    name: str
    data: dict[str, Any]


@dataclass(frozen=True)
class ArchitectureLayer:
    """One layer the repository declares for gate 9: a name and the directories it holds."""

    name: str
    paths: tuple[str, ...]  # repository-relative POSIX directories


@dataclass(frozen=True)
class Promotion:
    """The recorded decision that took a check from observation to blocking."""

    date: str
    reason: str


@dataclass(frozen=True)
class EffectiveCheck:
    id: str
    settings: CheckSettings
    promotion: Promotion | None = None
    thresholds: Mapping[str, Mapping[str, Number]] = field(default_factory=dict)
    commands: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    limits: Mapping[str, Number] = field(default_factory=dict)

    def values(self) -> dict[str, JsonValue]:
        """The part of the policy a check receives as ``GateInput.values``."""
        return {
            "thresholds": {lang: dict(names) for lang, names in self.thresholds.items()},
            "commands": {name: list(argv) for name, argv in self.commands.items()},
            "limits": dict(self.limits),
        }

    def to_dict(self) -> dict[str, Any]:
        """Same shape as a configuration layer, so a frozen policy can be loaded as one."""
        data: dict[str, Any] = {
            "mode": self.settings.mode.value,
            **self.values(),
        }
        if self.settings.on_inconclusive is not None:
            data["on_inconclusive"] = self.settings.on_inconclusive.value
        if self.promotion is not None:
            data["promotion"] = {"date": self.promotion.date, "reason": self.promotion.reason}
        return data


@dataclass(frozen=True)
class EffectivePolicy:
    """The merged policy. The lists are what the repository adds; Forja's built-in sensitive
    patterns are not repeated here and cannot be removed."""

    checks: Mapping[str, EffectiveCheck]
    sensitive_paths: tuple[str, ...] = ()
    protected_branches: tuple[str, ...] = ()
    # language -> operation -> the repository's own command line for it
    adapter_commands: Mapping[str, Mapping[str, tuple[str, ...]]] = field(default_factory=dict)
    redaction: RedactionSettings = field(default_factory=RedactionSettings)
    test_root: str | None = None  # where the test author may write; None: the adapter's own
    forbidden_licenses: tuple[str, ...] = ()  # SPDX ids the repository forbids; no default list
    voice_extra: tuple[str, ...] = ()  # regular expressions the repository adds to the voice list
    voice_off: tuple[str, ...] = ()  # ids of the Forja's voice markers the repository turns off
    architecture: tuple[ArchitectureLayer, ...] = ()  # top to bottom; none, no layer rules
    differential: tuple[DifferentialCase, ...] = ()  # none, gate 11 does not run

    def settings(self) -> dict[str, CheckSettings]:
        return {check_id: check.settings for check_id, check in self.checks.items()}

    def values(self) -> dict[str, dict[str, JsonValue]]:
        return {check_id: check.values() for check_id, check in self.checks.items()}

    def to_dict(self) -> dict[str, Any]:
        data = self._to_dict()
        if self.test_root is not None:  # absent when unset, so older policies keep their digest
            data["tests"] = {"root": self.test_root}
        if self.forbidden_licenses:  # absent when unset, for the same reason
            data["licenses"] = {"forbidden": list(self.forbidden_licenses)}
        if self.voice_extra or self.voice_off:
            data["voice"] = {
                "markers_extra": list(self.voice_extra),
                "markers_off": list(self.voice_off),
            }
        if self.architecture:
            data["architecture"] = {
                "layers": [
                    {"name": layer.name, "paths": list(layer.paths)} for layer in self.architecture
                ]
            }
        if self.differential:
            data["differential"] = {"cases": [case.to_dict() for case in self.differential]}
        return data

    def _to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": POLICY_SCHEMA_VERSION,
            "checks": {
                check_id: check.to_dict() for check_id, check in sorted(self.checks.items())
            },
            "sensitive": {"paths": list(self.sensitive_paths)},
            "branches": {"protected": list(self.protected_branches)},
            "redaction": self.redaction.to_dict(),
            "adapters": {
                language: {"commands": {op: list(argv) for op, argv in sorted(commands.items())}}
                for language, commands in sorted(self.adapter_commands.items())
            },
        }

    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    def version(self, forja_version: str) -> str:
        """``<forja version>+<12 hex of the policy content>``: changes with code or with values."""
        return f"{forja_version}+{self.digest()[:12]}"


def parse_layer(name: str, text: str) -> Layer:
    try:
        return Layer(name, tomllib.loads(text))
    except tomllib.TOMLDecodeError as error:
        raise PolicyConfigError([f"{name}: not valid TOML: {error}"]) from None


def load_policy(
    registry: CheckRegistry,
    *,
    user: Path | None = None,
    repo: Path | None = None,
    execution: Path | None = None,
) -> EffectivePolicy:
    """Merge the layers by precedence: Forja defaults < user profile < repository < execution.

    The user and repository files are optional and skipped when absent. An execution file that
    was asked for must exist.
    """
    layers: list[Layer] = []
    for name, path, required in (
        ("user profile", user, False),
        ("repository forja.toml", repo, False),
        ("execution file", execution, True),
    ):
        if path is None:
            continue
        if not path.is_file():
            if required:
                raise PolicyConfigError([f"{name}: {path} does not exist"])
            continue
        text = path.read_text(encoding="utf-8")  # UTF-8 is the Linux default
        layers.append(parse_layer(f"{name} ({path})", text))
    return build_policy(registry, layers)


def build_policy(registry: CheckRegistry, layers: Sequence[Layer]) -> EffectivePolicy:
    """Validate every layer, merge them over the defaults, then validate the result."""
    problems: list[str] = []
    for layer in layers:
        _validate_layer(registry, layer, problems)
    if problems:
        raise PolicyConfigError(problems)

    checks = {spec.id: _merge(spec, layers, problems) for spec in registry}
    architecture = _merge_architecture(layers, problems)
    differential = merge_differential([layer.data for layer in layers], problems)
    if problems:
        raise PolicyConfigError(problems)
    return EffectivePolicy(
        {i: check for i, check in checks.items() if check is not None},
        _collect(layers, "sensitive", "paths"),
        _collect(layers, "branches", "protected"),
        _merge_adapter_commands(layers),
        _merge_redaction(layers),
        _merge_test_root(layers),
        _collect(layers, "licenses", "forbidden"),
        _collect(layers, "voice", "markers_extra"),
        _collect(layers, "voice", "markers_off"),
        architecture,
        differential,
    )


# -- validation of one layer -------------------------------------------------------------------


def _validate_layer(registry: CheckRegistry, layer: Layer, problems: list[str]) -> None:
    def bad(where: str, message: str) -> None:
        problems.append(f"{layer.name}: {where}: {message}")

    unknown_top = set(layer.data) - set(SECTION_KEYS)
    for key in sorted(unknown_top):
        bad(key, "unknown section")
    _validate_architecture(layer.data.get("architecture"), bad)
    validate_differential(layer.data.get("differential"), bad)
    for name, check_one in (
        ("licenses", _check_license),
        ("sensitive", _check_pattern),
        ("branches", _check_branch),
    ):
        _validate_string_list(layer.data.get(name), name, _only_key(name), bad, check_one)
    _validate_voice(layer.data.get("voice"), bad)
    _validate_adapters(layer.data.get("adapters"), bad)
    _validate_redaction(layer.data.get("redaction"), bad)
    _validate_tests(layer.data.get("tests"), bad)
    checks = layer.data.get("checks", {})
    if not isinstance(checks, dict):
        bad("checks", "must be a table")
        return
    for check_id, raw in checks.items():
        where = f"checks.{check_id}"
        if check_id not in registry:
            bad(where, "unknown check")
            continue
        if not isinstance(raw, dict):
            bad(where, "must be a table")
            continue
        spec = registry.get(check_id)
        for key in sorted(set(raw) - _CHECK_KEYS):
            bad(f"{where}.{key}", "unknown key")
        _validate_enum(raw, "mode", Mode, where, bad)
        if check_id in NEVER_BLOCKING and raw.get("mode") == Mode.BLOCKING.value:
            bad(
                f"{where}.mode",
                "the voice never blocks: the Forja instructs the agent and reports what it "
                "measured (see docs/guia/documentacao-gerada.md)",
            )
        _validate_enum(raw, "on_inconclusive", InconclusivePolicy, where, bad)
        _validate_promotion(raw.get("promotion"), where, bad)
        _validate_thresholds(raw.get("thresholds"), spec, where, bad)
        _validate_commands(raw.get("commands"), spec, where, bad)
        _validate_limits(raw.get("limits"), spec, where, bad)


def _merge_architecture(
    layers: Sequence[Layer], problems: list[str]
) -> tuple[ArchitectureLayer, ...]:
    """The layers of every configuration layer, in the order they were read (top to bottom).
    A name used twice is a problem, whichever file declared it."""
    found: dict[str, ArchitectureLayer] = {}
    for layer in layers:
        for raw in layer.data.get("architecture", {}).get("layers", []):
            name = raw["name"].strip()
            if name in found:
                problems.append(f"architecture.layers: the layer name {name!r} is used twice")
            found[name] = ArchitectureLayer(name, tuple(raw["paths"]))
    return tuple(found.values())


def _validate_architecture(section: Any, bad: Any) -> None:
    if section is None:
        return
    if not isinstance(section, dict):
        bad("architecture", "must be a table")
        return
    for key in sorted(set(section) - SECTION_KEYS["architecture"]):
        bad(f"architecture.{key}", "unknown key")
    layers = section.get("layers")
    if layers is None:
        return
    if not isinstance(layers, list) or not all(isinstance(item, dict) for item in layers):
        bad("architecture.layers", "must be a list of { name, paths } tables")
        return
    for position, item in enumerate(layers):
        where = f"architecture.layers[{position}]"
        for key in sorted(set(item) - {"name", "paths"}):
            bad(f"{where}.{key}", "unknown key")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            bad(f"{where}.name", "must be a non-empty string")
        paths = item.get("paths")
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
            bad(f"{where}.paths", "must be a non-empty list of directories")
            continue
        for path in paths:
            try:
                check_relative_path(path)
            except ValueError:
                bad(
                    f"{where}.paths", f"{path!r} is not a relative POSIX path inside the repository"
                )


def _merge_test_root(layers: Sequence[Layer]) -> str | None:
    """The latest layer that sets where the test author writes."""
    found = None
    for layer in layers:
        section = layer.data.get("tests", {})
        if "root" in section:
            found = str(section["root"])
    return found


def _validate_tests(section: Any, bad: Any) -> None:
    if section is None:
        return
    if not isinstance(section, dict):
        bad("tests", "must be a table")
        return
    for key in sorted(set(section) - SECTION_KEYS["tests"]):
        bad(f"tests.{key}", "unknown key")
    if "root" in section:
        root = section["root"]
        try:
            if not isinstance(root, str):
                raise ValueError("must be a string")
            check_relative_path(root)
        except ValueError:
            bad("tests.root", "must be a directory inside the repository, as a POSIX path")


def _merge_redaction(layers: Sequence[Layer]) -> RedactionSettings:
    """Patterns and allowances add up across layers; the detector list and the threshold are
    replaced by the latest layer that sets them."""
    patterns: dict[str, str] = {}
    allow: dict[str, None] = {}
    allow_patterns: dict[str, None] = {}
    pii = set(PiiKind)
    entropy = RedactionSettings().entropy_threshold
    for layer in layers:
        section = layer.data.get("redaction", {})
        patterns.update(section.get("patterns", {}))
        allow.update(dict.fromkeys(section.get("allow", [])))
        allow_patterns.update(dict.fromkeys(section.get("allow_patterns", [])))
        if "pii" in section:
            pii = {PiiKind(value) for value in section["pii"]}
        if "entropy_threshold" in section:
            entropy = float(section["entropy_threshold"])
    return RedactionSettings(
        tuple(patterns.items()), tuple(allow), tuple(allow_patterns), frozenset(pii), entropy
    )


def _validate_redaction(section: Any, bad: Any) -> None:
    if section is None:
        return
    if not isinstance(section, dict):
        bad("redaction", "must be a table")
        return
    for key in sorted(set(section) - SECTION_KEYS["redaction"]):
        bad(f"redaction.{key}", "unknown key")

    _validate_redaction_patterns(section.get("patterns"), bad)
    for key in ("allow", "allow_patterns"):
        _validate_redaction_allow(section.get(key), key, bad)
    _validate_redaction_pii(section.get("pii"), bad)
    if "entropy_threshold" in section:
        threshold = section["entropy_threshold"]
        if not _is_number(threshold) or not 0 <= threshold <= 8:
            bad("redaction.entropy_threshold", "must be a number between 0 and 8")


def _validate_redaction_patterns(patterns: Any, bad: Any) -> None:
    if patterns is None:
        return
    if not isinstance(patterns, dict) or not all(isinstance(v, str) for v in patterns.values()):
        bad("redaction.patterns", "must be a table of name = regular expression")
        return
    for name, regex in patterns.items():
        try:
            compile_extra(name, regex)
        except RedactionSettingsError as error:
            bad(f"redaction.patterns.{name}", str(error))


def _validate_redaction_allow(values: Any, key: str, bad: Any) -> None:
    if values is None:
        return
    if not (
        isinstance(values, list) and all(isinstance(v, str) and 0 < len(v) <= 500 for v in values)
    ):
        bad(f"redaction.{key}", "must be a list of non-empty strings, each up to 500 characters")
        return
    if key != "allow_patterns":
        return
    for value in values:
        try:
            re.compile(value)
        except re.error as error:
            bad(
                "redaction.allow_patterns",
                f"{value!r} is not a valid regular expression: {error}",
            )


def _validate_redaction_pii(kinds: Any, bad: Any) -> None:
    if kinds is None:
        return
    allowed = sorted(kind.value for kind in PiiKind)
    if not (isinstance(kinds, list) and all(k in allowed for k in kinds)):
        bad("redaction.pii", f"must be a list drawn from: {', '.join(allowed)}")


def _merge_adapter_commands(layers: Sequence[Layer]) -> dict[str, dict[str, tuple[str, ...]]]:
    merged: dict[str, dict[str, tuple[str, ...]]] = {}
    for layer in layers:
        for language, section in layer.data.get("adapters", {}).items():
            for operation, argv in section.get("commands", {}).items():
                merged.setdefault(language, {})[operation] = tuple(argv)
    return merged


def _validate_adapters(section: Any, bad: Any) -> None:
    if section is None:
        return
    if not isinstance(section, dict):
        bad("adapters", "must be a table of languages")
        return
    for language, body in section.items():
        where = f"adapters.{language}"
        if language == "javascript":  # no adapter has this name: its commands would never run
            bad(where, "no adapter has this name; use typescript, which serves JavaScript")
            continue
        if language not in LANGUAGES:
            bad(where, f"unknown language; use: {', '.join(sorted(LANGUAGES))}")
            continue
        if not isinstance(body, dict):
            bad(where, "must be a table")
            continue
        for key in sorted(set(body) - SECTION_KEYS["adapters"]):
            bad(f"{where}.{key}", "unknown key")
        commands = body.get("commands", {})
        if not isinstance(commands, dict):
            bad(f"{where}.commands", "must be a table")
            continue
        for operation, argv in commands.items():
            path = f"{where}.commands.{operation}"
            if operation not in ADAPTER_OPERATIONS:
                bad(path, f"unknown operation; use: {', '.join(sorted(ADAPTER_OPERATIONS))}")
            elif not (
                isinstance(argv, list) and argv and all(isinstance(a, str) and a for a in argv)
            ):
                bad(path, "must be a non-empty list of strings (no shell string)")


def _collect(layers: Sequence[Layer], section: str, key: str) -> tuple[str, ...]:
    """Values of a list setting from every layer: they add up, they never replace."""
    seen: dict[str, None] = {}
    for layer in layers:
        for value in layer.data.get(section, {}).get(key, []):
            seen.setdefault(value.strip(), None)  # None is the default
    return tuple(seen)


def _check_pattern(value: str) -> None:
    # Only validity matters here, and case folding never changes whether a pattern compiles.
    compile_pattern(value, case_insensitive=False)


def _check_marker_pattern(value: str) -> None:
    try:
        compiled = re.compile(value)
    except re.error as error:
        raise ValueError(f"invalid voice pattern {value!r}: {error}") from None
    if compiled.match(""):
        raise ValueError(f"the voice pattern {value!r} matches the empty string")


def _check_marker_id(value: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9-]*", value):
        raise ValueError(f"invalid voice marker id {value!r}")


def _validate_voice(section: Any, bad: Any) -> None:
    if section is None:
        return
    if not isinstance(section, dict):
        bad("voice", "must be a table")
        return
    for extra in sorted(set(section) - SECTION_KEYS["voice"]):
        bad(f"voice.{extra}", "unknown key")
    for key, check_one in (
        ("markers_extra", _check_marker_pattern),
        ("markers_off", _check_marker_id),
    ):
        _validate_string_list(
            {key: section[key]} if key in section else None, "voice", key, bad, check_one
        )


def _check_license(value: str) -> None:
    name = value.strip()
    if not name or any(c.isspace() for c in name):
        raise ValueError(f"invalid license identifier {value!r}")


def _check_branch(value: str) -> None:
    name = value.strip()
    if not name or any(c.isspace() for c in name) or name.startswith("-"):
        raise ValueError(f"invalid branch name {value!r}")


def _only_key(name: str) -> str:
    """The one key of a section that holds a single list."""
    (key,) = SECTION_KEYS[name]
    return key


def _validate_string_list(section: Any, name: str, key: str, bad: Any, check_one: Any) -> None:
    if section is None:
        return
    if not isinstance(section, dict):
        bad(name, "must be a table")
        return
    for extra in sorted(set(section) - SECTION_KEYS[name]):
        bad(f"{name}.{extra}", "unknown key")
    values = section.get(key)
    if values is None:
        return
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        bad(f"{name}.{key}", "must be a list of strings")
        return
    for value in values:
        try:
            check_one(value)
        except ValueError as error:
            bad(f"{name}.{key}", str(error))


def _validate_enum(raw: dict[str, Any], key: str, enum: Any, where: str, bad: Any) -> None:
    if key in raw and raw[key] not in {member.value for member in enum}:
        allowed = ", ".join(member.value for member in enum)
        bad(f"{where}.{key}", f"{raw[key]!r} is not one of: {allowed}")


def _validate_promotion(raw: Any, where: str, bad: Any) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict) or set(raw) != {"date", "reason"}:
        bad(f"{where}.promotion", "must be a table with exactly 'date' and 'reason'")
        return
    if not isinstance(raw["date"], date | str) or not _is_iso_date(raw["date"]):
        bad(f"{where}.promotion.date", "must be a date such as 2026-10-10")
    if not isinstance(raw["reason"], str) or not raw["reason"].strip():
        bad(f"{where}.promotion.reason", "must say why the check was promoted")


def _is_iso_date(value: date | str) -> bool:
    if isinstance(value, date):
        return True
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _validate_thresholds(raw: Any, spec: CheckSpec, where: str, bad: Any) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict):
        bad(f"{where}.thresholds", "must be a table of languages")
        return
    for language, names in raw.items():
        if language not in LANGUAGES:
            bad(
                f"{where}.thresholds.{language}",
                f"unknown language; use: {', '.join(sorted(LANGUAGES))}",
            )
            continue
        if not isinstance(names, dict):
            bad(f"{where}.thresholds.{language}", "must be a table")
            continue
        for name, value in names.items():
            path = f"{where}.thresholds.{language}.{name}"
            if name not in spec.thresholds:
                bad(path, "this check has no such threshold")
            elif not _is_number(value):
                bad(path, "must be a finite number")


def _validate_commands(raw: Any, spec: CheckSpec, where: str, bad: Any) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict):
        bad(f"{where}.commands", "must be a table")
        return
    for name, value in raw.items():
        path = f"{where}.commands.{name}"
        if name not in spec.commands:
            bad(path, "this check has no such command")
        elif not (
            isinstance(value, list)
            and value
            and all(isinstance(part, str) and part for part in value)
        ):
            bad(path, "must be a non-empty list of strings (no shell string)")


def _validate_limits(raw: Any, spec: CheckSpec, where: str, bad: Any) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict):
        bad(f"{where}.limits", "must be a table")
        return
    for name, value in raw.items():
        path = f"{where}.limits.{name}"
        if name not in spec.limits:
            bad(path, "this check has no such limit")
        elif not _is_number(value) or value < 0:
            bad(path, "must be a finite number, zero or more")
        elif name in _WHOLE_LIMITS and not isinstance(value, int):
            bad(path, "must be a whole number")


# -- merge and validation of the result --------------------------------------------------------


def _merge_values(
    raw: dict[str, Any],
    promotion: dict[str, str],
    thresholds: dict[str, dict[str, Number]],
    commands: dict[str, tuple[str, ...]],
    limits: dict[str, Number],
) -> None:
    for key, value in raw.get("promotion", {}).items():
        promotion[key] = value.isoformat() if isinstance(value, date) else value.strip()
    for language, names in raw.get("thresholds", {}).items():
        thresholds.setdefault(language, {}).update(names)
    for name, argv in raw.get("commands", {}).items():
        commands[name] = tuple(argv)
    limits.update(raw.get("limits", {}))


def _promotion_problem(where: str, promotion: dict[str, str], promoted: bool) -> str | None:
    if promoted and set(promotion) != {"date", "reason"}:
        return (
            f"{where}: promoting a check from observation to blocking needs a recorded decision: "
            "promotion = { date = ..., reason = ... }"
        )
    if promotion and not promoted:
        return f"{where}: promotion is only valid on a check promoted to blocking"
    return None


def _merge(spec: CheckSpec, layers: Sequence[Layer], problems: list[str]) -> EffectiveCheck | None:
    mode = spec.default_mode
    on_inconclusive = spec.default_on_inconclusive
    promotion: dict[str, str] = {}
    thresholds: dict[str, dict[str, Number]] = {}
    commands: dict[str, tuple[str, ...]] = {}
    limits: dict[str, Number] = {}
    for layer in layers:
        raw = layer.data.get("checks", {}).get(spec.id)
        if raw is None:
            continue
        if "mode" in raw:
            mode = Mode(raw["mode"])
        if "on_inconclusive" in raw:
            on_inconclusive = InconclusivePolicy(raw["on_inconclusive"])
        _merge_values(raw, promotion, thresholds, commands, limits)

    where = f"checks.{spec.id}"
    try:
        settings = CheckSettings(mode, on_inconclusive)
    except InvalidCheckSettings:
        problems.append(f"{where}: a blocking check needs on_inconclusive = 'block' or 'premise'")
        return None

    promoted = mode is Mode.BLOCKING and spec.default_mode is Mode.OBSERVATION
    promotion_problem = _promotion_problem(where, promotion, promoted)
    if promotion_problem is not None:
        problems.append(promotion_problem)
        return None
    return EffectiveCheck(
        spec.id,
        settings,
        Promotion(promotion["date"], promotion["reason"]) if promotion else None,
        thresholds,
        commands,
        limits,
    )
