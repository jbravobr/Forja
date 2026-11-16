"""Makes the tools the Forja installed visible to the processes it launches.

The folder of each installed tool goes in front of the ``PATH`` the Forja hands to its children;
the user's own ``PATH`` is never changed. A tool that lives in a virtual environment (a wheel)
goes after the rest instead: its folder also holds a ``python``, which must not hide the one the
repository's tests need.
"""

import os
from collections.abc import Mapping
from pathlib import Path

from forja.toolchain.install import find_installed, tools_dir
from forja.toolchain.load import (
    NoArtifactForPlatform,
    UnsupportedPlatform,
    artifact_for,
    load_manifest,
)
from forja.toolchain.manifest import Manifest, Platform


def installed_directories(
    manifest: Manifest, root: Path, *, target: Platform | None = None
) -> tuple[list[Path], list[Path]]:
    """The folders of the installed tools: those for the front of the PATH, those for the back."""
    front: list[Path] = []
    back: list[Path] = []
    for tool in manifest.tools:
        try:
            found = find_installed(manifest, tool.name, root, target=target)
        except NoArtifactForPlatform:
            continue
        if found is None:
            continue
        in_environment = artifact_for(manifest, tool.name, target).kind == "wheel"
        (back if in_environment else front).append(found.executable.parent)
    return front, back


def with_installed_tools(
    environ: Mapping[str, str],
    *,
    manifest: Manifest | None = None,
    root: Path | None = None,
    target: Platform | None = None,
) -> dict[str, str]:
    """A copy of ``environ`` whose PATH reaches the tools the Forja installed. Applying it twice
    changes nothing; on a platform the Forja installs nothing for, the copy is the same."""
    try:
        front, back = installed_directories(
            load_manifest() if manifest is None else manifest,
            tools_dir(environ=environ) if root is None else root,
            target=target,
        )
    except UnsupportedPlatform:
        return dict(environ)
    key = next((name for name in environ if name.upper() == "PATH"), "PATH")
    current = [part for part in environ.get(key, "").split(os.pathsep) if part]
    ahead = [str(d) for d in front if str(d) not in current]
    behind = [str(d) for d in back if str(d) not in current]
    return {**environ, key: os.pathsep.join([*ahead, *current, *behind])}


def installed_by_forja(executable: Path, root: Path) -> bool:
    """Whether ``executable`` is inside the folder where the Forja installs its tools."""
    return root.resolve() in executable.resolve().parents
