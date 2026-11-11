"""The Cursor CLI adapter: what it asks of the CLI and how it reads what comes back.

The event lines follow the examples of the CLI's output format page; no stream was recorded from
the CLI itself (none is installed here). Run it live to check them against a real one.
"""

import json
from pathlib import Path

from forja.core.adapters.executor import (
    AGENT_DIR,
    AgentRequest,
    ErrorActivity,
    FileEditActivity,
    FileReadActivity,
    RolePermissions,
    TextActivity,
    UsageActivity,
)
from forja.core.artifacts.models import Producer
from forja.executors import CursorAdapter
from forja.executors.cursor import CONFIG_DIR, CONFIG_FILE

adapter = CursorAdapter()


def line(**data: object) -> str:
    return json.dumps({"session_id": "s", **data})


def plan(tmp_path: Path, **permissions: object):  # type: ignore[no-untyped-def]
    allowed = RolePermissions(**{"can_edit_files": True, **permissions})  # type: ignore[arg-type]
    request = AgentRequest(Producer.IMPLEMENTER, tmp_path / "package.md", tmp_path, allowed)
    setup = adapter.permission_setup(Producer.IMPLEMENTER, allowed)
    return adapter.plan_invocation(request, setup, Path("/bin/agent"))


def permissions_of(**permissions: object) -> dict[str, list[str]]:
    allowed = RolePermissions(**{"can_edit_files": True, **permissions})  # type: ignore[arg-type]
    setup = adapter.permission_setup(Producer.IMPLEMENTER, allowed)
    config = json.loads(setup.files[f"{CONFIG_DIR}/{CONFIG_FILE}"])
    assert config["version"] == 1
    permissions_: dict[str, list[str]] = config["permissions"]
    return permissions_


# -- the invocation --------------------------------------------------------------------------------


def test_the_prompt_names_the_package_and_is_not_its_content(tmp_path: Path) -> None:
    result = plan(tmp_path)

    assert result.argv[:4] == (str(Path("/bin/agent")), "-p", "--output-format", "stream-json")
    assert str(tmp_path / "package.md") in result.argv[-1]
    assert result.stdin_file is None
    assert result.cwd == tmp_path


def test_the_sandbox_is_on_and_no_mcp_server_is_approved(tmp_path: Path) -> None:
    argv = plan(tmp_path).argv

    assert argv[argv.index("--sandbox") + 1] == "enabled"
    assert "--approve-mcps" not in argv
    assert argv[argv.index("--workspace") + 1] == str(tmp_path)


def test_the_configuration_is_the_one_forja_wrote(tmp_path: Path) -> None:
    env = plan(tmp_path).env_set

    assert env["CURSOR_CONFIG_DIR"] == str(tmp_path / AGENT_DIR / CONFIG_DIR)


def test_only_the_api_key_of_the_cli_is_passed_on(tmp_path: Path) -> None:
    allow = plan(tmp_path).env_allow

    assert "CURSOR_API_KEY" in allow
    assert "OPENAI_API_KEY" not in allow


# -- the permissions -------------------------------------------------------------------------------


def test_a_role_that_edits_runs_with_force(tmp_path: Path) -> None:
    argv = plan(tmp_path).argv

    assert "--force" in argv
    assert "--mode" not in argv


def test_a_role_that_does_not_edit_runs_in_ask_mode_without_force(tmp_path: Path) -> None:
    argv = plan(tmp_path, can_edit_files=False).argv

    assert argv[argv.index("--mode") + 1] == "ask"
    assert "--force" not in argv
    assert "Write(**)" in permissions_of(can_edit_files=False)["deny"]


def test_a_role_without_commands_cannot_run_a_shell() -> None:
    assert "Shell(*)" in permissions_of()["deny"]


def test_a_role_with_commands_is_not_denied_the_shell() -> None:
    assert "Shell(*)" not in permissions_of(allowed_commands=("pytest",))["deny"]


def test_the_web_and_mcp_are_denied() -> None:
    assert {"WebFetch(*)", "Mcp(*:*)"} <= set(permissions_of()["deny"])


# -- reading the stream ----------------------------------------------------------------------------


def test_the_assistant_message_is_text() -> None:
    event = line(
        type="assistant", message={"role": "assistant", "content": [{"type": "text", "text": "ok"}]}
    )

    assert adapter.parse_event(event) == TextActivity("ok")


def test_a_read_and_a_write_are_seen_when_they_start() -> None:
    read = line(
        type="tool_call", subtype="started", call_id="1",
        tool_call={"readToolCall": {"args": {"path": "src\\a.py"}}},
    )  # fmt: skip
    write = line(
        type="tool_call", subtype="started", call_id="2",
        tool_call={"writeToolCall": {"args": {"path": "b.txt", "fileText": "x\ny\n"}}},
    )  # fmt: skip

    assert adapter.parse_event(read) == FileReadActivity("src/a.py")
    assert adapter.parse_event(write) == FileEditActivity("b.txt", 2, 0)


def test_an_edit_call_is_a_write_too() -> None:
    # cursor 2026.11 reports a file it writes as editToolCall with streamContent
    edit = line(
        type="tool_call", subtype="started", call_id="3",
        tool_call={"editToolCall": {"args": {"path": "c.txt", "streamContent": "forja-ok\n"}}},
    )  # fmt: skip

    assert adapter.parse_event(edit) == FileEditActivity("c.txt", 1, 0)


def test_a_completed_call_is_not_counted_twice() -> None:
    event = line(
        type="tool_call", subtype="completed", call_id="1",
        tool_call={"readToolCall": {"args": {"path": "a"}, "result": {"success": {}}}},
    )  # fmt: skip

    assert adapter.parse_event(event) is None


def test_a_tool_the_page_does_not_describe_is_nothing() -> None:
    event = line(
        type="tool_call", subtype="started", call_id="1",
        tool_call={"function": {"name": "grep", "arguments": "{}"}},
    )  # fmt: skip

    assert adapter.parse_event(event) is None


def test_a_successful_result_reports_an_unknown_use() -> None:
    event = line(type="result", subtype="success", is_error=False, result="done", duration_ms=1)

    assert adapter.parse_event(event) == UsageActivity()


def test_an_error_result_is_an_error() -> None:
    event = line(type="result", subtype="success", is_error=True, result="not logged in")

    seen = adapter.parse_event(event)

    assert isinstance(seen, ErrorActivity)
    assert seen.message == "not logged in"


def test_what_is_not_an_event_is_nothing() -> None:
    assert adapter.parse_event("not json") is None
    assert adapter.parse_event("[1]") is None
    assert adapter.parse_event(line(type="system", subtype="init", model="m")) is None
    assert adapter.parse_event(line(type="user", message={})) is None
