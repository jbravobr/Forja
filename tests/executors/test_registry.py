"""The contract every CLI adapter in ``KNOWN_CLIS`` must keep, whichever CLI it drives.

A new CLI enters the registry and is held to these checks without a test of its own being added
here; what is specific to it (its events, its flags) is tested in its own module.
"""

from pathlib import Path

import pytest

from forja.core.adapters import CONTRACT_VERSION, CommandPlan
from forja.core.adapters.executor import AgentRequest, RolePermissions
from forja.core.artifacts.models import Producer
from forja.executors import DEFAULT_CLI, KNOWN_CLIS

ROLES = (Producer.IMPLEMENTER, Producer.TEST_AUTHOR)


def test_the_default_cli_is_a_known_one() -> None:
    assert DEFAULT_CLI in KNOWN_CLIS


@pytest.mark.parametrize("key", sorted(KNOWN_CLIS))
def test_a_cli_is_registered_under_its_own_name(key: str) -> None:
    assert KNOWN_CLIS[key]().name == key


@pytest.mark.parametrize("key", sorted(KNOWN_CLIS))
def test_a_cli_declares_the_current_contract_and_how_to_find_it(key: str) -> None:
    adapter = KNOWN_CLIS[key]()
    assert adapter.contract_version == CONTRACT_VERSION
    assert adapter.discovery.executable_names
    assert adapter.probe.expected_file


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("key", sorted(KNOWN_CLIS))
def test_a_cli_plans_an_invocation_for_every_agent_role(
    key: str, role: Producer, tmp_path: Path
) -> None:
    adapter = KNOWN_CLIS[key]()
    permissions = RolePermissions(can_edit_files=True)
    setup = adapter.permission_setup(role, permissions)
    request = AgentRequest(role, tmp_path / "package.md", tmp_path, permissions)
    plan = adapter.plan_invocation(request, setup, Path("/bin/agent"))
    assert isinstance(plan, CommandPlan)
    assert plan.argv


@pytest.mark.parametrize("key", sorted(KNOWN_CLIS))
@pytest.mark.parametrize("text", ["", "not json", "{", "[]", '{"type": 1}'])
def test_a_cli_never_raises_on_a_line_it_does_not_understand(key: str, text: str) -> None:
    assert KNOWN_CLIS[key]().parse_event(text) is None


@pytest.mark.parametrize("name", list(KNOWN_CLIS))
def test_every_cli_takes_the_model_by_the_flag_and_none_by_default(
    name: str, tmp_path: Path
) -> None:
    adapter = KNOWN_CLIS[name]()
    permissions = RolePermissions(can_edit_files=True)

    def argv_of(model: str) -> tuple[str, ...]:
        request = AgentRequest(
            Producer.IMPLEMENTER, tmp_path / "p.md", tmp_path, permissions, model
        )
        setup = adapter.permission_setup(Producer.IMPLEMENTER, permissions)
        return adapter.plan_invocation(request, setup, Path("/bin/x")).argv

    chosen = argv_of("some-model")
    assert chosen[chosen.index("--model") + 1] == "some-model"
    assert "--model" not in argv_of("")


def test_a_model_name_cannot_pass_for_a_flag(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="dash"):
        AgentRequest(
            Producer.IMPLEMENTER, tmp_path, tmp_path, RolePermissions(can_edit_files=True), "--yolo"
        )
