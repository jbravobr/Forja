"""The repository's mypy configuration, as the Forja lets mypy read it.

mypy takes its settings from the first of ``mypy.ini``, ``.mypy.ini``, ``pyproject.toml`` and
``setup.cfg`` that has them. The Forja hands mypy a copy of those settings, never the file: a
``plugins`` entry is code of the repository that mypy would import and run, and the attempt may
have written it. The copy leaves that entry out, and the entries that make mypy write a report
into the worktree. Everything else is the repository's to decide.
"""

import configparser
import hashlib
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

INI_FILES = ("mypy.ini", ".mypy.ini")
PYPROJECT = "pyproject.toml"
SETUP_CFG = "setup.cfg"
SOURCES = (*INI_FILES, PYPROJECT, SETUP_CFG)  # the order mypy looks in
MAIN = "mypy"
PLUGINS = "plugins"
CONFIG_DIR = "$MYPY_CONFIG_FILE_DIR"
_MULTILINE = frozenset({"exclude"})  # a list of patterns, one per line; the others take commas


@dataclass(frozen=True)
class MypyConfig:
    """``source`` is the file the settings came from, relative to the project's directory;
    ``sections`` are they as an INI file has them (``mypy`` and ``mypy-<modules>``)."""

    source: str
    sections: Mapping[str, Mapping[str, str]]

    @property
    def plugins(self) -> bool:
        """Whether the repository asks mypy to load plugins."""
        return bool(self.sections.get(MAIN, {}).get(PLUGINS, "").strip())

    def text(self, root: Path) -> str:
        """The settings mypy is given: no plugins, no reports, and the directory of the
        repository's file written out where the settings name it (the copy lives elsewhere)."""
        lines: list[str] = []
        for name, options in self.sections.items():
            lines.append(f"[{name}]")
            for key, value in options.items():
                if _left_out(key):
                    continue
                kept = value.replace(CONFIG_DIR, root.as_posix()).replace("\n", "\n    ")
                lines.append(f"{key} = {kept}")
            lines.append("")
        return "\n".join(lines)

    def write(self, root: Path, directory: Path) -> Path:
        """Write the settings under ``directory``, in a file named after their content, and
        answer where: the same settings always land in the same file."""
        text = self.text(root)
        name = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        path = directory / "mypy" / f"{name}.ini"
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        return path


def _left_out(key: str) -> bool:
    return key == PLUGINS or key.endswith("_report") or key == "junit_xml"


def _ini(path: Path) -> dict[str, dict[str, str]] | None:
    parser = configparser.RawConfigParser()
    try:
        parser.read_string(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, configparser.Error):
        return None
    found = {
        name: dict(parser.items(name))
        for name in parser.sections()
        if name == MAIN or name.startswith(f"{MAIN}-")
    }
    return found if MAIN in found else None


def _value(key: str, value: object) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, list):
        return ("\n" if key in _MULTILINE else ", ").join(str(item) for item in value)
    return str(value)


def _pyproject(path: Path) -> dict[str, dict[str, str]] | None:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    table = data.get("tool", {}).get(MAIN) if isinstance(data.get("tool"), dict) else None
    if not isinstance(table, dict):
        return None
    main = {key: _value(key, value) for key, value in table.items() if key != "overrides"}
    found = {MAIN: main}
    overrides = table.get("overrides")
    for entry in overrides if isinstance(overrides, list) else ():
        if not isinstance(entry, dict):
            continue
        module = entry.get("module")
        modules = module if isinstance(module, list) else [module]
        names = ",".join(str(name) for name in modules if name)
        if names:
            found[f"{MAIN}-{names}"] = {
                key: _value(key, value) for key, value in entry.items() if key != "module"
            }
    return found


def read(root: Path) -> MypyConfig | None:
    """The mypy settings of the project at ``root``; ``None`` when it has none, or when the
    file that should hold them cannot be read (mypy then runs with its defaults)."""
    for name in SOURCES:
        path = root / name
        if not path.is_file():
            continue
        sections = _pyproject(path) if name == PYPROJECT else _ini(path)
        if sections is not None:
            return MypyConfig(name, sections)
        if name in INI_FILES:  # mypy stops at the first of these, whatever is in it
            return None
    return None
