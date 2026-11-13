"""The repository's mypy configuration, as the Forja lets mypy read it.

mypy takes its settings from the first of ``mypy.ini``, ``.mypy.ini``, ``pyproject.toml`` and
``setup.cfg`` that has them. The Forja hands mypy a copy of those settings, never the file: a
``plugins`` entry is code of the repository that mypy would import and run, and the attempt may
have written it. The copy leaves that entry out, and the entries that make mypy write a report
into the worktree. Everything else is the repository's to decide.

The copy is text the Forja writes, so a name or a value of the repository's file must not be able
to add a line to it: that line could be ``plugins``. A copy is only used when it reads back, with
the reader mypy uses, as the options it was made of and no other.
"""

import configparser
import hashlib
import re
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
# An option of mypy is a word. A section is ``mypy``, or ``mypy-`` and patterns of module names.
_OPTION = re.compile(r"[a-z_][a-z0-9_]*")
_SECTION = re.compile(r"mypy(-[A-Za-z0-9_.*,\- ]+)?")
# In a value, a line feed is a new line of the same value and a tab is a blank. Any other control
# character is refused: a carriage return alone is read as the end of a line.
_UNWRITABLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\x85  ]")


@dataclass(frozen=True)
class MypyConfig:
    """``source`` is the file the settings came from, relative to the project's directory;
    ``sections`` are they as an INI file has them (``mypy`` and ``mypy-<modules>``), the names
    of the options in lower case, as mypy's reader of INI files takes them."""

    source: str
    sections: Mapping[str, Mapping[str, str]]

    @property
    def plugins(self) -> bool:
        """Whether the repository asks mypy to load plugins."""
        return bool(self.sections.get(MAIN, {}).get(PLUGINS, "").strip())

    def text(self, root: Path) -> str | None:
        """The settings mypy is given: no plugins, no reports, and the directory of the
        repository's file written out where the settings name it (the copy lives elsewhere).

        ``None`` when the copy cannot be trusted to say only that: a name or a value that could
        add a line of its own, or a copy that does not read back as the options it was made of.
        """
        kept = {
            name: {key: value for key, value in options.items() if not _left_out(key)}
            for name, options in self.sections.items()
        }
        lines: list[str] = []
        for name, options in kept.items():
            if not _SECTION.fullmatch(name):
                return None
            lines.append(f"[{name}]")
            for key, value in options.items():
                if not _OPTION.fullmatch(key) or _UNWRITABLE.search(value):
                    return None
                written = value.replace(CONFIG_DIR, root.as_posix()).replace("\n", "\n    ")
                lines.append(f"{key} = {written}")
            lines.append("")
        text = "\n".join(lines)
        return text if _reads_as(text, kept) else None

    def write(self, root: Path, directory: Path) -> Path | None:
        """Write the settings under ``directory``, in a file named after their content, and
        answer where: the same settings always land in the same file. ``None`` when there is no
        copy to trust (``text``)."""
        text = self.text(root)
        if text is None:
            return None
        name = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        path = directory / "mypy" / f"{name}.ini"
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        return path


def _left_out(key: str) -> bool:
    return key == PLUGINS or key.endswith("_report") or key == "junit_xml"


def _option(key: object) -> str:
    """The name of an option as mypy's reader of INI files takes it: no case, no blanks around.
    ``Plugins`` in a TOML file would otherwise go by the filter and be read as ``plugins``."""
    return str(key).strip().lower()


def _reads_as(text: str, wanted: Mapping[str, Mapping[str, str]]) -> bool:
    """Whether the reader mypy uses finds in ``text`` the sections and options of ``wanted``, no
    more and no fewer, and none of those the copy leaves out."""
    parser = configparser.RawConfigParser()
    try:
        parser.read_string(text)
    except configparser.Error:
        return False
    found = {name: set(parser.options(name)) for name in parser.sections()}
    if any(_left_out(key) for keys in found.values() for key in keys):
        return False
    return found == {name: set(options) for name, options in wanted.items()}


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


def _options(table: Mapping[str, object], skipped: str) -> dict[str, str]:
    return {
        _option(key): _value(_option(key), value) for key, value in table.items() if key != skipped
    }


def _pyproject(path: Path) -> dict[str, dict[str, str]] | None:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    table = data.get("tool", {}).get(MAIN) if isinstance(data.get("tool"), dict) else None
    if not isinstance(table, dict):
        return None
    found = {MAIN: _options(table, "overrides")}
    overrides = table.get("overrides")
    for entry in overrides if isinstance(overrides, list) else ():
        if not isinstance(entry, dict):
            continue
        module = entry.get("module")
        modules = module if isinstance(module, list) else [module]
        names = ",".join(str(name) for name in modules if name)
        if names:
            found[f"{MAIN}-{names}"] = _options(entry, "module")
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
