"""What a run is asked to do and how it should look: ``execucao.toml`` and the user's profile.

Everything the preparation asks is written to ``execucao.toml``, and ``forja --config`` reads it
back without asking anything, for CI. The user's own preferences (where the CLIs are, the theme)
live in their profile, never in the repository.
"""

import os
import re
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from forja.core.adapters.executor import CommandTemplate, template_adapter
from forja.ui.theme import THEMES

DEFAULT_TOKENS = 2_000_000
Mode = Literal["auto", "completa", "linha", "texto"]


class SettingsError(Exception):
    """A settings file that cannot be used; the message says what to fix."""


class Interface(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modo: Mode = "auto"
    tema: str = ""  # empty: follow the background of the terminal
    ascii: bool = False
    redimensionar: bool = True

    @field_validator("tema")
    @classmethod
    def _known_theme(cls, value: str) -> str:
        if value and value.casefold() not in THEMES:
            raise ValueError(f"tema desconhecido {value!r}; os temas são {', '.join(THEMES)}")
        return value


class ModeloCli(BaseModel):
    """``[clis.<nome>]``: a CLI the Forja does not know, described by a command template."""

    model_config = ConfigDict(extra="forbid")

    comando: list[str] = Field(min_length=1)  # {executable}, {package}, {cwd} and {role}
    pacote_por_stdin: bool = False  # the package is piped instead of named by {package}
    executaveis: list[str] = Field(default_factory=list)  # empty: the name of the CLI
    versao: list[str] = Field(default_factory=lambda: ["--version"])
    ambiente: list[str] = Field(default_factory=list)  # environment variables the CLI may see

    @model_validator(mode="after")
    def _usable_template(self) -> "ModeloCli":
        template_adapter("cli", self.template())  # raises ValueError when the template is wrong
        return self

    def template(self) -> CommandTemplate:
        return CommandTemplate(
            argv=tuple(self.comando),
            via_stdin=self.pacote_por_stdin,
            executables=tuple(self.executaveis),
            version_args=tuple(self.versao),
            env_allow=tuple(self.ambiente),
        )


class Execucao(BaseModel):
    """``execucao.toml``: the answers of the preparation."""

    model_config = ConfigDict(extra="forbid")

    objetivo: Literal["refatorar", "corrigir", "investigar", "criar"] = "refatorar"
    descricao: str = ""  # corrigir: the defect; investigar: the question; criar: the idea
    arquivo: str = ""
    criterios: str = ""  # corrigir: the criteria file (CriteriaBody as JSON)
    cli: str = ""
    executavel: str = ""
    cli_testes: str = ""  # corrigir: the CLI of the test author; empty is the implementer's
    executavel_testes: str = ""
    modelo: str = ""  # empty: the model configured in the CLI
    modelo_testes: str = ""  # corrigir: empty is the implementer's
    escopo: list[str] = Field(default_factory=list)
    tokens: int = Field(default=DEFAULT_TOKENS, ge=1)
    agente_roda_testes: bool = True
    tarefas: int = Field(default=5, ge=1)  # how many of the worst functions one run takes on
    paralelismo: int = Field(default=1, ge=1)  # tasks of disjoint scope that run at once
    semgrep_config: str = ""  # the user's Semgrep rules (a file or directory); empty: none named
    linguagem: str = ""  # criar: the language of the new project
    nome: str = ""  # criar: its name (lower-case words joined by hyphens)
    fatos: str = ""  # the facts file the person keeps (see ``orchestrator.facts``); empty: none
    instalar_ferramentas: bool = False  # without a terminal, install the missing tools
    clis: dict[str, ModeloCli] = Field(default_factory=dict)  # the generic CLIs this run uses
    interface: Interface = Field(default_factory=Interface)


class Perfil(BaseModel):
    """``config.toml`` of the user: what is theirs and not the repository's."""

    model_config = ConfigDict(extra="forbid")

    cli: str = ""
    executaveis: dict[str, str] = Field(default_factory=dict)
    clis: dict[str, ModeloCli] = Field(default_factory=dict)
    interface: Interface = Field(default_factory=Interface)


def profile_dir(environ: Mapping[str, str] | None = None, platform: str | None = None) -> Path:
    env = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if override := env.get("FORJA_CONFIG_HOME"):
        return Path(override)
    if platform == "win32":
        return Path(env.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "forja"
    base = env.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "forja"


def _read(path: Path) -> dict[str, object]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise SettingsError(f"cannot read {path}: {error.strerror}") from None
    except tomllib.TOMLDecodeError as error:
        raise SettingsError(f"{path} is not valid TOML: {error}") from None


def _explain(path: Path, error: ValidationError) -> SettingsError:
    problems = "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
    )
    return SettingsError(f"{path}: {problems}")


def load_execucao(path: Path) -> Execucao:
    data = _read(path)
    try:
        return Execucao.model_validate(data)
    except ValidationError as error:
        raise _explain(path, error) from None


def load_perfil(directory: Path) -> Perfil:
    path = directory / "config.toml"
    if not path.exists():
        return Perfil()
    try:
        return Perfil.model_validate(_read(path))
    except ValidationError as error:
        raise _explain(path, error) from None


def _value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_value(item) for item in value) + "]"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else _value(name)


def dumps(data: Mapping[str, object]) -> str:
    """TOML for the flat values, one level of tables and tables of tables these files hold."""
    lines = [
        f"{key} = {_value(value)}" for key, value in data.items() if not isinstance(value, dict)
    ]
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        if value and all(isinstance(item, dict) for item in value.values()):
            for name, table in value.items():
                lines += ["", f"[{key}.{_key(name)}]"]
                lines += [f"{field} = {_value(item)}" for field, item in table.items()]
            continue
        lines += ["", f"[{key}]"]
        lines += [f"{name} = {_value(item)}" for name, item in value.items()]
    return "\n".join(lines) + "\n"


def save_execucao(path: Path, config: Execucao) -> None:
    path.write_text(dumps(config.model_dump()), encoding="utf-8", newline="\n")


def save_perfil(directory: Path, profile: Perfil) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "config.toml"
    path.write_text(dumps(profile.model_dump()), encoding="utf-8", newline="\n")
    return path
