"""The proof of life of a CLI is remembered for a day, per way of calling it; the doctor never
answers from memory."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from forja.cli import prepare as preparation
from forja.cli.prepare import PROOF_VALID_SECONDS, Refused, prove
from forja.core.adapters.executor import CommandTemplate

TEMPLATE = CommandTemplate(argv=("{package}",))
START = 1_000_000.0


class Quiet:
    interactive = False

    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text: str = "") -> None:
        self.said.append(text)

    def ask(self, question: str, *, default: str = "") -> str:
        return default


class World:
    """A CLI whose version is told and whose probe is counted, and a clock that is set."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.cache = tmp_path / "home" / "profile" / "provas.json"  # neither folder is there yet
        self.probes = 0
        self.problems: list[str] = []
        self.now = START
        self.console = Quiet()

        async def version(*args: object) -> str:
            return "1.2.3"

        async def probe(*args: object) -> list[str]:
            self.probes += 1
            return self.problems

        monkeypatch.setattr(preparation, "executable_version", version)
        monkeypatch.setattr(preparation, "version_problem", lambda *_a: None)
        monkeypatch.setattr(preparation, "run_probe", probe)

    def prove(
        self, *, template: CommandTemplate = TEMPLATE, path: str = "p", fresh: bool = False
    ) -> str:
        choice = cast(Any, SimpleNamespace(path=path))
        return asyncio.run(
            prove(
                "minha",
                choice,
                {},
                self.cache,
                self.console,
                template,
                fresh=fresh,
                clock=lambda: self.now,
            )
        )


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    return World(tmp_path, monkeypatch)


def test_a_proof_is_remembered_until_it_is_a_day_old(world: World) -> None:
    assert world.prove() == "1.2.3"
    assert world.probes == 1

    world.now = START + PROOF_VALID_SECONDS - 1
    world.prove()
    assert world.probes == 1

    world.now = START + PROOF_VALID_SECONDS
    world.prove()
    assert world.probes == 2


def test_a_proof_made_again_counts_from_the_new_moment(world: World) -> None:
    world.prove()
    world.now = START + PROOF_VALID_SECONDS
    world.prove()

    world.now = START + 2 * PROOF_VALID_SECONDS - 1
    world.prove()

    assert world.probes == 2


def test_a_proof_dated_after_now_does_not_count(world: World) -> None:
    world.prove()

    world.now = START - 1
    world.prove()

    assert world.probes == 2


def test_a_proof_made_right_now_counts(world: World) -> None:
    world.prove()
    world.prove()

    assert world.probes == 1


def test_the_doctor_proves_again_whatever_is_remembered(world: World) -> None:
    world.prove()

    world.prove(fresh=True)

    assert world.probes == 2
    assert len(world.console.said) == 2


def test_an_expired_login_is_seen_by_a_fresh_proof(world: World) -> None:
    world.prove()
    world.problems = ["not logged in"]

    with pytest.raises(Refused, match="not logged in"):
        world.prove(fresh=True)


def test_another_way_of_calling_the_cli_is_proven_on_its_own(world: World) -> None:
    world.prove()

    world.prove(template=CommandTemplate(argv=("--yes", "{package}")))
    assert world.probes == 2

    world.prove()
    assert world.probes == 2


def test_another_path_is_proven_on_its_own(world: World) -> None:
    world.prove()

    world.prove(path="q")

    assert world.probes == 2


def test_the_cache_keeps_the_moment_under_the_path_the_version_and_the_template(
    world: World,
) -> None:
    world.prove()

    kept = json.loads(world.cache.read_text(encoding="utf-8"))

    (key,) = kept
    path, version, digest = key.split("|")
    assert (path, version) == ("p", "1.2.3")
    assert len(digest) == 16
    assert kept[key] == START


def test_a_known_cli_has_no_template_in_its_key(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preparation, "adapter_for", lambda *_a: object())
    choice = cast(Any, SimpleNamespace(path="p"))

    asyncio.run(prove("claude-code", choice, {}, world.cache, world.console, clock=lambda: START))

    assert list(json.loads(world.cache.read_text(encoding="utf-8"))) == ["p|1.2.3|"]


@pytest.mark.parametrize("old", ["true", "false", '"ontem"', "null"])
def test_what_an_older_forja_remembered_carries_no_moment_and_is_proven_again(
    world: World, old: str
) -> None:
    world.prove()
    key = next(iter(json.loads(world.cache.read_text(encoding="utf-8"))))
    world.cache.write_text(f'{{"{key}": {old}}}', encoding="utf-8")
    world.now = 1.5  # a moment at which ``true``, read as the number 1, would still be young

    world.prove()

    assert world.probes == 2


@pytest.mark.parametrize("content", ["[]", "not json", '"x"'])
def test_a_cache_that_cannot_be_read_is_started_over(world: World, content: str) -> None:
    world.cache.parent.mkdir(parents=True)
    world.cache.write_text(content, encoding="utf-8")

    world.prove()

    assert world.probes == 1
    assert len(json.loads(world.cache.read_text(encoding="utf-8"))) == 1


def test_unless_told_to_prove_again_what_is_remembered_is_used(world: World) -> None:
    choice = cast(Any, SimpleNamespace(path="p"))

    for _ in range(2):
        asyncio.run(
            prove("minha", choice, {}, world.cache, world.console, TEMPLATE, clock=lambda: START)
        )

    assert world.probes == 1


def test_a_failed_proof_is_not_remembered(world: World) -> None:
    world.problems = ["quota"]
    with pytest.raises(Refused):
        world.prove()

    assert not world.cache.exists()
