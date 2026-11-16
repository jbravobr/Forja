"""The manifest of external tools: what the Forja installs and where each platform gets it.

One entry per tool; per platform, the exact artifact of the tool's own release, its
size and its checksum. The Forja installs nothing that is not listed here, and what is listed is
checked for completeness before anything is downloaded. Git is not in the manifest: the user
installs it.
"""

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

SCHEMA_VERSION = 1

Platform = Literal["windows-x64", "linux-x64"]
Language = Literal["python", "java", "go", "csharp", "javascript", "typescript"]
# ``binary``: the release is the executable itself, saved under ``executable_path``.
# ``wheel``: a PyPI wheel installed in a virtual environment of its own; ``executable_path`` is
# then relative to that environment.
# ``nupkg``: a NuGet package, which is a zip; ``executable_path`` is the assembly of a .NET tool,
# which ``dotnet`` runs.
ArtifactKind = Literal["zip", "tar.gz", "tar.xz", "wheel", "binary", "nupkg"]
Algorithm = Literal["sha256", "sha512"]

DIGEST_LENGTH: dict[Algorithm, int] = {"sha256": 64, "sha512": 128}
_SUFFIX: dict[str, str] = {
    "zip": ".zip",
    "tar.gz": ".tar.gz",
    "tar.xz": ".tar.xz",
    "wheel": ".whl",
    "nupkg": ".nupkg",
}
_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
_SPDX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+() -]*$")


def _frozen() -> ConfigDict:
    return ConfigDict(frozen=True, extra="forbid")


class Artifact(BaseModel):
    """What one platform downloads, and how to know it is the right file."""

    model_config = _frozen()

    url: StrictStr  # the exact file, on the tool's official origin
    kind: ArtifactKind
    size: StrictInt = Field(gt=0)  # bytes
    algorithm: Algorithm
    checksum: StrictStr  # lowercase hexadecimal, as the vendor publishes it
    checksum_source: StrictStr  # the vendor file or page the checksum was read from
    executable_path: StrictStr  # inside the extracted artifact (see ``ArtifactKind``)

    @field_validator("url", "checksum_source")
    @classmethod
    def _https(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError(f"{value!r} is not an https URL")
        return value

    @field_validator("executable_path")
    @classmethod
    def _relative(cls, value: str) -> str:
        segments = value.split("/")
        if not value or "\\" in value or any(s in ("", ".", "..") for s in segments):
            raise ValueError(f"{value!r} is not a relative path with / separators")
        if re.match(r"^[A-Za-z]:", value):
            raise ValueError(f"{value!r} is not a relative path with / separators")
        return value

    def model_post_init(self, __context: object) -> None:
        want = DIGEST_LENGTH[self.algorithm]
        if len(self.checksum) != want or re.fullmatch(r"[0-9a-f]+", self.checksum) is None:
            raise ValueError(
                f"{self.algorithm} checksum must be {want} lowercase hexadecimal characters"
            )
        suffix = _SUFFIX.get(self.kind)
        if suffix is not None and not urlsplit(self.url).path.endswith(suffix):
            raise ValueError(f"{self.url!r} is not a {self.kind} file")


class Tool(BaseModel):
    """One external tool: what it is for, why it is needed and where each platform gets it."""

    model_config = _frozen()

    name: StrictStr
    executable: StrictStr  # the command, without extension
    purpose: StrictStr  # short, in Portuguese: the interface shows it
    license: StrictStr  # SPDX expression
    version: StrictStr  # the pinned version
    needed_by: Literal["always"] | tuple[Language, ...]
    artifacts: dict[Platform, Artifact]

    @field_validator("name", "executable")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if _NAME.fullmatch(value) is None:
            raise ValueError(f"{value!r} is not a lowercase identifier")
        return value

    @field_validator("purpose", "version")
    @classmethod
    def _text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("license")
    @classmethod
    def _license(cls, value: str) -> str:
        if _SPDX.fullmatch(value) is None:
            raise ValueError(f"{value!r} is not an SPDX license expression")
        return value

    @field_validator("artifacts")
    @classmethod
    def _some_platform(cls, value: dict[Platform, Artifact]) -> dict[Platform, Artifact]:
        if not value:
            raise ValueError("lists no platform")
        return value

    @field_validator("needed_by")
    @classmethod
    def _languages(cls, value: object) -> object:
        if isinstance(value, tuple) and (not value or len(set(value)) != len(value)):
            raise ValueError("list each language once, or use 'always'")
        return value


class Manifest(BaseModel):
    model_config = _frozen()

    schema_version: Literal[1]
    tools: tuple[Tool, ...]

    @field_validator("tools")
    @classmethod
    def _unique(cls, value: tuple[Tool, ...]) -> tuple[Tool, ...]:
        if not value:
            raise ValueError("lists no tool")
        names = [tool.name for tool in value]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"tools listed twice: {', '.join(repeated)}")
        return value
