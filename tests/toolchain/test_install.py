"""The installer: unpacks only what the manifest names, whole or not at all, with no network."""

import asyncio
import hashlib
import io
import json
import shutil
import sys
import tarfile
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from forja.core.process import ProcessSpec
from forja.toolchain import install as install_module
from forja.toolchain.install import (
    MARKER,
    Installed,
    InstallError,
    _venv_python,
    https_fetch,
    install_tool,
    last_lines,
    tools_dir,
)
from forja.toolchain.load import NoArtifactForPlatform, UnknownTool, parse_manifest
from forja.toolchain.manifest import Manifest, Platform

URL = "https://example.org/tool"
WINDOWS = sys.platform == "win32"
HERE: Platform = "windows-x64" if WINDOWS else "linux-x64"


def run(manifest: Manifest, name: str, root: Path, **options: Any) -> Installed:
    return asyncio.run(install_tool(manifest, name, root, **options))


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def tar_bytes(files: dict[str, bytes], mode: str = "w:gz") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as archive:  # type: ignore[call-overload]
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def manifest_for(
    content: bytes,
    kind: str = "zip",
    executable_path: str = "bin/tool",
    *,
    size: int | None = None,
    checksum: str | None = None,
    algorithm: str = "sha256",
    version: str = "1.2.3",
) -> Manifest:
    suffixes = {"zip": ".zip", "tar.gz": ".tar.gz", "tar.xz": ".tar.xz", "wheel": ".whl"}
    suffix = suffixes.get(kind, "")
    name = "toolpkg-1.0-py3-none-any" if kind == "wheel" else "tool"
    digest = hashlib.new(algorithm, content).hexdigest()
    return parse_manifest(
        f"""
schema_version = 1

[[tools]]
name = "tool"
executable = "tool"
purpose = "Faz algo."
license = "MIT"
version = "{version}"
needed_by = "always"

[tools.artifacts.{HERE}]
url = "https://example.org/{name}{suffix}"
kind = "{kind}"
size = {len(content) if size is None else size}
algorithm = "{algorithm}"
checksum = "{checksum or digest}"
checksum_source = "https://example.org/checksums.txt"
executable_path = "{executable_path}"
"""
    )


class Origin:
    """A fake origin: serves one content, and counts the downloads."""

    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls: list[str] = []

    def __call__(self, url: str, destination: Path, limit: int) -> None:
        self.calls.append(url)
        destination.write_bytes(self.content)


def listing(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*"))


def test_a_zip_is_unpacked_under_the_name_and_version(tmp_path: Path) -> None:
    content = zip_bytes({"bin/tool": b"run", "README": b"read"})
    origin = Origin(content)

    done = run(manifest_for(content), "tool", tmp_path, target=HERE, fetch=origin)

    assert done.directory == tmp_path / "tool" / "1.2.3"
    assert done.executable == done.directory / "bin" / "tool"
    assert done.executable.read_bytes() == b"run"
    assert (done.directory / "README").read_bytes() == b"read"
    assert (done.tool, done.version, done.already) == ("tool", "1.2.3", False)
    assert origin.calls == [URL + ".zip"]


@pytest.mark.parametrize("kind, mode", [("tar.gz", "w:gz"), ("tar.xz", "w:xz")])
def test_a_tarball_is_unpacked(tmp_path: Path, kind: str, mode: str) -> None:
    content = tar_bytes({"tool-1/bin/tool": b"run"}, mode)
    manifest = manifest_for(content, kind, "tool-1/bin/tool")

    done = run(manifest, "tool", tmp_path, target=HERE, fetch=Origin(content))

    assert done.executable.read_bytes() == b"run"


def test_a_binary_is_saved_under_its_executable_path(tmp_path: Path) -> None:
    content = b"\x7fELF"
    manifest = manifest_for(content, "binary", "tool.bin")

    done = run(manifest, "tool", tmp_path, target=HERE, fetch=Origin(content))

    assert done.executable == tmp_path / "tool" / "1.2.3" / "tool.bin"
    assert done.executable.read_bytes() == content


@pytest.mark.skipif(WINDOWS, reason="the execute bit is POSIX")
def test_the_executable_is_marked_executable_on_linux(tmp_path: Path) -> None:
    content = zip_bytes({"bin/tool": b"run"})  # a zip keeps no mode

    done = run(manifest_for(content), "tool", tmp_path, target=HERE, fetch=Origin(content))

    assert done.executable.stat().st_mode & 0o111 == 0o111


def test_installing_again_downloads_nothing(tmp_path: Path) -> None:
    content = zip_bytes({"bin/tool": b"run"})
    manifest = manifest_for(content)
    origin = Origin(content)
    first = run(manifest, "tool", tmp_path, target=HERE, fetch=origin)

    second = run(manifest, "tool", tmp_path, target=HERE, fetch=origin)

    assert second.already is True
    assert second.executable == first.executable
    assert len(origin.calls) == 1


def test_another_version_is_installed_beside_the_first(tmp_path: Path) -> None:
    old = zip_bytes({"bin/tool": b"old"})
    run(manifest_for(old), "tool", tmp_path, target=HERE, fetch=Origin(old))
    new = zip_bytes({"bin/tool": b"new"})
    newer = manifest_for(new, version="2.0.0")

    done = run(newer, "tool", tmp_path, target=HERE, fetch=Origin(new))

    assert done.already is False
    assert done.executable.read_bytes() == b"new"
    assert (tmp_path / "tool" / "1.2.3" / "bin" / "tool").read_bytes() == b"old"


def test_a_tool_without_the_marker_is_installed_again(tmp_path: Path) -> None:
    content = zip_bytes({"bin/tool": b"run"})
    manifest = manifest_for(content)
    done = run(manifest, "tool", tmp_path, target=HERE, fetch=Origin(content))
    (done.directory / MARKER).unlink()
    origin = Origin(content)

    again = run(manifest, "tool", tmp_path, target=HERE, fetch=origin)

    assert again.already is False
    assert len(origin.calls) == 1
    assert (again.directory / MARKER).is_file()


def test_a_marker_of_another_checksum_is_installed_again(tmp_path: Path) -> None:
    content = zip_bytes({"bin/tool": b"run"})
    manifest = manifest_for(content)
    done = run(manifest, "tool", tmp_path, target=HERE, fetch=Origin(content))
    (done.directory / MARKER).write_text(
        json.dumps({"version": "1.2.3", "checksum": "0" * 64}), encoding="utf-8"
    )

    again = run(manifest, "tool", tmp_path, target=HERE, fetch=Origin(content))

    assert again.already is False


def test_a_marked_tool_whose_executable_is_gone_is_installed_again(tmp_path: Path) -> None:
    content = zip_bytes({"bin/tool": b"run"})
    manifest = manifest_for(content)
    done = run(manifest, "tool", tmp_path, target=HERE, fetch=Origin(content))
    done.executable.unlink()

    again = run(manifest, "tool", tmp_path, target=HERE, fetch=Origin(content))

    assert again.already is False
    assert again.executable.is_file()


def test_a_wrong_checksum_installs_nothing(tmp_path: Path) -> None:
    served = zip_bytes({"bin/tool": b"evil"})
    expected = zip_bytes({"bin/tool": b"good"})
    assert len(served) == len(expected)
    manifest = manifest_for(expected)

    with pytest.raises(InstallError) as raised:
        run(manifest, "tool", tmp_path, target=HERE, fetch=Origin(served))

    assert raised.value.tool == "tool"
    assert raised.value.url == URL + ".zip"
    assert "checksum" in raised.value.reason
    assert listing(tmp_path) == ["tool"]


def test_a_sha512_checksum_is_compared_too(tmp_path: Path) -> None:
    content = zip_bytes({"bin/tool": b"run"})
    manifest = manifest_for(content, algorithm="sha512")

    done = run(manifest, "tool", tmp_path, target=HERE, fetch=Origin(content))
    assert done.executable.is_file()

    other = zip_bytes({"bin/tool": b"xxx"})
    with pytest.raises(InstallError, match="sha512"):
        run(manifest, "tool", tmp_path / "b", target=HERE, fetch=Origin(other))


def test_a_wrong_size_installs_nothing(tmp_path: Path) -> None:
    content = zip_bytes({"bin/tool": b"run"})
    manifest = manifest_for(content, size=len(content) + 1)

    with pytest.raises(InstallError) as raised:
        run(manifest, "tool", tmp_path, target=HERE, fetch=Origin(content))

    assert f"{len(content)} bytes" in raised.value.reason
    assert f"says {len(content) + 1}" in raised.value.reason
    assert listing(tmp_path) == ["tool"]


def test_a_failed_download_installs_nothing(tmp_path: Path) -> None:
    content = zip_bytes({"bin/tool": b"run"})

    def offline(url: str, destination: Path, limit: int) -> None:
        destination.write_bytes(b"part")
        raise OSError("no network")

    with pytest.raises(InstallError) as raised:
        run(manifest_for(content), "tool", tmp_path, target=HERE, fetch=offline)

    assert "no network" in raised.value.reason
    assert raised.value.url == URL + ".zip"
    assert listing(tmp_path) == ["tool"]


def test_a_failure_removes_a_partial_install_that_was_there(tmp_path: Path) -> None:
    partial = tmp_path / "tool" / "1.2.3" / "bin"
    partial.mkdir(parents=True)
    (partial / "tool").write_bytes(b"half")
    content = zip_bytes({"bin/tool": b"run"})

    def offline(url: str, destination: Path, limit: int) -> None:
        raise OSError("no network")

    with pytest.raises(InstallError):
        run(manifest_for(content), "tool", tmp_path, target=HERE, fetch=offline)

    assert listing(tmp_path) == ["tool"]


def test_an_archive_without_the_executable_installs_nothing(tmp_path: Path) -> None:
    content = zip_bytes({"README": b"read"})

    with pytest.raises(InstallError, match="bin/tool is not in it"):
        run(manifest_for(content), "tool", tmp_path, target=HERE, fetch=Origin(content))

    assert listing(tmp_path) == ["tool"]


def test_a_zip_that_writes_outside_its_folder_installs_nothing(tmp_path: Path) -> None:
    content = zip_bytes({"bin/tool": b"run", "../escape": b"x"})
    root = tmp_path / "tools"

    with pytest.raises(InstallError, match="outside its folder"):
        run(manifest_for(content), "tool", root, target=HERE, fetch=Origin(content))

    assert not (root / "tool" / "escape").exists()
    assert not (root / "escape").exists()
    assert listing(root) == ["tool"]


def test_a_tarball_that_writes_outside_its_folder_installs_nothing(tmp_path: Path) -> None:
    content = tar_bytes({"bin/tool": b"run", "../escape": b"x"})
    root = tmp_path / "tools"
    manifest = manifest_for(content, "tar.gz")

    with pytest.raises(InstallError, match="cannot unpack"):
        run(manifest, "tool", root, target=HERE, fetch=Origin(content))

    assert not (root / "tool" / "escape").exists()
    assert listing(root) == ["tool"]


def test_a_file_that_is_not_an_archive_installs_nothing(tmp_path: Path) -> None:
    content = b"not a zip"

    with pytest.raises(InstallError, match="cannot unpack"):
        run(manifest_for(content), "tool", tmp_path, target=HERE, fetch=Origin(content))

    assert listing(tmp_path) == ["tool"]


def test_an_unknown_tool_and_a_missing_platform_are_refused(tmp_path: Path) -> None:
    content = zip_bytes({"bin/tool": b"run"})
    manifest = manifest_for(content)
    other: Platform = "linux-x64" if WINDOWS else "windows-x64"

    with pytest.raises(UnknownTool):
        run(manifest, "nope", tmp_path, target=HERE, fetch=Origin(content))
    with pytest.raises(NoArtifactForPlatform):
        run(manifest, "tool", tmp_path, target=other, fetch=Origin(content))

    assert listing(tmp_path) == []


def wheel_bytes() -> bytes:
    """A wheel with a console script ``tool`` and no dependency."""
    files = {
        "toolpkg/__init__.py": b"def main():\n    print('ran')\n",
        "toolpkg-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: toolpkg\nVersion: 1.0\n",
        "toolpkg-1.0.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        "toolpkg-1.0.dist-info/entry_points.txt": b"[console_scripts]\ntool = toolpkg:main\n",
        "toolpkg-1.0.dist-info/RECORD": b"",
    }
    return zip_bytes(files)


def test_a_wheel_gets_a_virtual_environment_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    content = wheel_bytes()
    script = "Scripts/tool.exe" if WINDOWS else "bin/tool"
    manifest = manifest_for(content, "wheel", script)

    done = run(manifest, "tool", tmp_path, target=HERE, fetch=Origin(content))

    assert done.executable == tmp_path / "tool" / "1.2.3" / script
    assert done.executable.is_file()
    assert (done.directory / MARKER).is_file()
    assert [p.name for p in (tmp_path / "tool").iterdir()] == ["1.2.3"]


def test_pip_runs_without_the_keys_and_tokens_of_the_person(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    seen: list[Mapping[str, str]] = []

    def recorded(*args: Any, **kwargs: Any) -> ProcessSpec:
        spec = ProcessSpec(*args, **kwargs)
        seen.append(spec.env)
        return spec

    monkeypatch.setattr(install_module, "ProcessSpec", recorded)
    content = wheel_bytes()
    script = "Scripts/tool.exe" if WINDOWS else "bin/tool"

    run(
        manifest_for(content, "wheel", script), "tool", tmp_path, target=HERE, fetch=Origin(content)
    )

    assert len(seen) == 2  # the environment and pip
    for env in seen:
        assert "PATH" in {name.upper() for name in env}
        assert env.get("PIP_NO_INDEX") == "1"
        assert "ANTHROPIC_API_KEY" not in env
        assert "GITHUB_TOKEN" not in env


def test_a_wheel_that_pip_refuses_installs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    content = b"not a wheel"
    manifest = manifest_for(content, "wheel", "bin/tool")

    with pytest.raises(InstallError, match=r"failed: .+"):
        run(manifest, "tool", tmp_path, target=HERE, fetch=Origin(content))

    assert listing(tmp_path) == ["tool"]


def test_the_tools_live_inside_the_forja_home(tmp_path: Path) -> None:
    assert tools_dir(environ={"FORJA_HOME": str(tmp_path)}) == tmp_path / "tools"


class Response(io.BytesIO):
    def __init__(self, data: bytes, announced: str | None) -> None:
        super().__init__(data)
        self.headers = {} if announced is None else {"Content-Length": announced}

    def __enter__(self) -> "Response":
        return self


def serve(monkeypatch: pytest.MonkeyPatch, data: bytes, announced: str | None) -> None:
    monkeypatch.setattr(
        "forja.toolchain.install.urllib.request.urlopen",
        lambda url, timeout: Response(data, announced),
    )


def test_the_fetch_saves_a_file_that_fits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, b"12345", "5")
    https_fetch(URL, tmp_path / "f", 5)
    assert (tmp_path / "f").read_bytes() == b"12345"


def test_the_fetch_refuses_a_file_announced_too_long(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, b"12345", "6")
    with pytest.raises(OSError, match="announces 6"):
        https_fetch(URL, tmp_path / "f", 5)
    assert not (tmp_path / "f").exists()


def test_the_fetch_stops_a_stream_longer_than_it_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, b"123456", None)
    with pytest.raises(OSError, match="more than the 5"):
        https_fetch(URL, tmp_path / "f", 5)


def test_a_failed_install_leaves_the_temporary_folder_gone_even_if_it_resists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = zip_bytes({"bin/tool": b"run"})
    real = shutil.rmtree

    def stubborn(path: Path, ignore_errors: bool = False) -> None:
        if not ignore_errors:
            raise OSError("in use")
        real(path, ignore_errors=True)

    monkeypatch.setattr(shutil, "rmtree", stubborn)

    done = run(manifest_for(content), "tool", tmp_path, target=HERE, fetch=Origin(content))

    assert done.executable.is_file()


def test_a_failure_is_reported_even_if_the_partial_folder_resists_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = zip_bytes({"bin/other": b"run"})  # not the executable the manifest names
    real = shutil.rmtree

    def stubborn(path: Path, ignore_errors: bool = False) -> None:
        if not ignore_errors:
            raise OSError("in use")
        real(path, ignore_errors=True)

    monkeypatch.setattr(shutil, "rmtree", stubborn)

    with pytest.raises(InstallError, match="is not in it"):
        run(manifest_for(content), "tool", tmp_path, target=HERE, fetch=Origin(content))


def test_the_executable_bit_is_set_only_where_the_system_has_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = zip_bytes({"bin/tool": b"run"})
    modes: list[int] = []
    real = Path.chmod

    def spy(self: Path, mode: int, **kwargs: Any) -> None:
        modes.append(mode)
        real(self, mode, **kwargs)

    monkeypatch.setattr(Path, "chmod", spy)

    run(manifest_for(content), "tool", tmp_path, target=HERE, fetch=Origin(content))

    assert bool(modes) is (sys.platform != "win32")


def test_the_environment_python_of_a_wheel_is_the_one_of_this_system(tmp_path: Path) -> None:
    expected = (
        tmp_path / ("Scripts" if WINDOWS else "bin") / ("python.exe" if WINDOWS else "python")
    )

    assert _venv_python(tmp_path) == expected


def test_only_the_last_lines_of_the_output_go_into_the_error() -> None:
    lines = [f"line {n}" for n in range(1, 11)]

    assert last_lines(lines) == "line 6 | line 7 | line 8 | line 9 | line 10"
    assert last_lines(lines[:2]) == "line 1 | line 2"
