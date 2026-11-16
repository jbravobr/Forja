"""The probe the Forja puts in a Java test run: the listener that records what each test runs, the
fail-fast condition, the two orderers, and the reader of the recorded data. Its sources are text in
``probe/``; nothing compiled is kept in the repository.

Maven cannot compile sources from its command line, so the probe is compiled by
``probe/Lancador.java`` (a single source file the JDK runs itself) into a cache outside the
project, against the jars of
the local Maven repository (``~/.m2``) named here: the ones the template's JUnit and the JaCoCo
the Forja pins already put there. Gradle compiles the probe in its own build (``init_scripts``),
against what the build resolves, so it needs none of this.
"""

import os
from pathlib import Path

from forja.core.paths import forja_home

SOURCES = Path(__file__).parent / "probe"
LAUNCHER = SOURCES / "Lancador.java"
READER = "com.forja.probe.LinhasPorTeste"
ORDER_OF_CLASSES = "com.forja.probe.OrdemDeClasses"
ORDER_OF_METHODS = "com.forja.probe.OrdemDeMetodos"
REPORTS = ".forja-test-lines"
EXECUTIONS = f"{REPORTS}/exec"
LINES_JSON = f"{REPORTS}/linhas.json"
SOURCE_ROOT = "src/main/java"
# what the probe is compiled against: the pins of the template (JUnit 5.10.2) and of the Forja
# (JaCoCo)
_JARS = (
    ("org.junit.jupiter", "junit-jupiter-api", "5.10.2"),
    ("org.junit.platform", "junit-platform-commons", "1.10.2"),
    ("org.junit.platform", "junit-platform-engine", "1.10.2"),
    ("org.junit.platform", "junit-platform-launcher", "1.10.2"),
    ("org.opentest4j", "opentest4j", "1.3.0"),
    ("org.apiguardian", "apiguardian-api", "1.1.2"),
    ("org.jacoco", "org.jacoco.core", "0.8.13"),
    ("org.jacoco", "org.jacoco.agent", "0.8.13"),  # the runtime one, see ``_jar``
    ("org.ow2.asm", "asm", "9.8"),
    ("org.ow2.asm", "asm-commons", "9.8"),
    ("org.ow2.asm", "asm-tree", "9.8"),
)


def default_cache_dir() -> Path:
    """Where the compiled probe goes: the Forja's home, which belongs to this user (never the
    shared temporary directory, where another user could put classes of their own)."""
    return forja_home() / "java-probe"


def _jar(repository: Path, group: str, artifact: str, version: str) -> Path | None:
    """The pinned version of the jar, else the newest one the repository has: the probe uses the
    API every version of these has, and the JaCoCo agent is the one the Forja pins."""
    base = repository.joinpath(*group.split("."), artifact)
    classifier = "-runtime" if artifact == "org.jacoco.agent" else ""
    versions = [version] if artifact.startswith("org.jacoco") else [version, *_newest(base)]
    for candidate in dict.fromkeys(versions):
        jar = base / candidate / f"{artifact}-{candidate}{classifier}.jar"
        if jar.is_file():
            return jar
    return None


def _newest(base: Path) -> list[str]:
    if not base.is_dir():
        return []
    found = [entry.name for entry in base.iterdir() if entry.is_dir()]
    return sorted(
        found,
        key=lambda name: [int(part) if part.isdigit() else 0 for part in name.split(".")],
        reverse=True,
    )


def classpath(repository: Path) -> str | None:
    """The jars the probe is compiled and read with, joined as the platform separates them; ``None``
    while the local repository does not have them (the first test run of the project puts them)."""
    found = [_jar(repository, group, artifact, version) for group, artifact, version in _JARS]
    if any(jar is None for jar in found):
        return None
    return os.pathsep.join(str(jar) for jar in found if jar is not None)
