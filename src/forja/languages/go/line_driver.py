"""Measures which tests run each line of a Go module: every test alone, with its own coverage.

It is run as a script by the interpreter of the Forja, with the output directory and then the Go
command. It imports nothing of Forja. Usage:

    python line_driver.py <output directory> <go command...>

The tests are listed by ``go test -json ./...``. Each package is built once as a test binary with
the coverage of the whole module, and each test is run alone from that binary with a coverage
profile of its own. The profiles, the binaries and ``index.json`` (``{"<profile>": "<test id>"}``)
go in the output directory. The exit code is 0 when every test was measured; otherwise it is not,
and whoever asked chooses no test for any line.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

EXIT_USAGE = 1
EXIT_LIST = 2  # the tests cannot be listed: a package does not compile, or the Go tool fails
EXIT_BUILD = 3  # a package's test binary does not build
EXIT_MEASURE = 4  # a test was not measured: its profile was not written
_SPECIAL = frozenset("\\.+*?()|[]{}^$")  # regexp.QuoteMeta
_OUTCOMES = frozenset({"pass", "fail", "skip"})


def quote(name: str) -> str:
    return "".join("\\" + c if c in _SPECIAL else c for c in name)


def run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def list_ids(go: list[str], root: Path) -> list[str] | None:
    """The id of every test event, in the order the tool gives them; ``None`` when the tests
    cannot be listed (a package failed before any of its tests ran, or nothing ran at all)."""
    done = run([*go, "test", "-count=1", "-json", "./..."], root)
    ids: dict[str, None] = {}
    tested: set[str] = set()
    failed: set[str] = set()
    for line in done.stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        package, action, test = event.get("Package"), event.get("Action"), event.get("Test")
        if not isinstance(package, str) or action not in _OUTCOMES:
            continue
        if isinstance(test, str):
            ids[f"{package}/{test}"] = None
            tested.add(package)
        elif action == "fail":
            failed.add(package)
    if failed - tested or (not ids and done.returncode != 0):
        return None
    return list(ids)


def package_dirs(go: list[str], root: Path) -> dict[str, Path] | None:
    """The directory of each package of the module, by import path."""
    done = run([*go, "list", "-f", "{{.ImportPath}}\t{{.Dir}}", "./..."], root)
    if done.returncode != 0:
        return None
    found: dict[str, Path] = {}
    for line in done.stdout.splitlines():
        path, _, directory = line.partition("\t")
        if path and directory:
            found[path] = Path(directory)
    return found


def split(test_id: str, packages: dict[str, Path]) -> tuple[str, list[str]] | None:
    """The package of a test id (the longest import path it starts with) and the names of its
    test and subtests."""
    known = [p for p in packages if test_id.startswith(p + "/")]
    if not known:
        return None
    package = max(known, key=len)
    return package, test_id[len(package) + 1 :].split("/")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write("usage: line_driver.py <output directory> <go command...>\n")
        return EXIT_USAGE
    root = Path.cwd()
    output = (root / argv[0]).resolve()
    go = argv[1:]
    shutil.rmtree(output, ignore_errors=True)
    (output / "bin").mkdir(parents=True)

    ids = list_ids(go, root)
    dirs = None if ids is None else package_dirs(go, root)
    if ids is None or dirs is None:
        return EXIT_LIST
    parts: dict[str, tuple[str, list[str]]] = {}
    for test_id in ids:
        found = split(test_id, dirs)
        if found is None:
            return EXIT_LIST
        parts[test_id] = found

    suffix = ".exe" if os.name == "nt" else ""
    binaries: dict[str, Path] = {}
    for package, _ in parts.values():
        if package in binaries:
            continue
        binary = output / "bin" / f"{len(binaries)}.test{suffix}"
        built = run(
            [*go, "test", "-c", "-cover", "-coverpkg=./...", "-o", str(binary), package], root
        )
        if built.returncode != 0:
            return EXIT_BUILD
        binaries[package] = binary

    # a test that has subtests is not a leaf: the ids asked for are the leaves
    leaves = [i for i in ids if not any(other.startswith(i + "/") for other in ids)]
    index: dict[str, str] = {}
    for number, test_id in enumerate(leaves):
        package, levels = parts[test_id]
        pattern = "/".join(f"^{quote(level)}$" for level in levels)
        profile = output / f"{number}.out"
        # the test binary runs in its package's directory, as ``go test`` runs it
        run(
            [str(binaries[package]), f"-test.run={pattern}", f"-test.coverprofile={profile}"],
            dirs[package],
        )
        if not profile.is_file():
            return EXIT_MEASURE
        index[profile.name] = test_id

    (output / "index.json").write_text(json.dumps(index, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
