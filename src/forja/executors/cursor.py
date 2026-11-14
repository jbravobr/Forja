"""Cursor CLI as an executor: how to find it, call it headless and read what it says.

Everything about the CLI itself (installation, API key, model) belongs to the user and is done
before Forja runs. What Forja decides is the invocation: ``agent -p --output-format stream-json``,
the sandbox on, the workspace trusted (headless only), no MCP server approved, and a configuration
directory of its own (``CURSOR_CONFIG_DIR``) holding the permissions of the role. The prompt is a
fixed sentence naming the package file: the documentation says nothing about reading it from stdin,
and the package never travels as an argument.

Sources, read in full before writing this: the headless, installation, parameters, permissions,
configuration and output format pages of the Cursor CLI documentation. The CLI is closed source.

Known limits, not solved:

* The user's login is not carried (the configuration directory is Forja's): ``CURSOR_API_KEY``
  must be set.
* Permissions deny, they do not whitelist ("deny rules override allow rules"): a role with no
  commands gets ``Shell(*)`` denied, a role with commands can run any command inside the sandbox.
  A role that does not edit runs in ``--mode ask`` without ``--force``.
* The documentation gives no token counts, so usage is reported as unknown, and it gives the
  shapes of the read and write tool calls only: shell and edit calls are not reported.
* The project's ``.cursor/cli.json`` and rules are read; the documentation has no switch for it.
"""

import json
from collections.abc import Mapping
from pathlib import Path

from forja.core.adapters.common import CommandPlan
from forja.core.adapters.executor import (
    AGENT_DIR,
    AgentActivity,
    AgentRequest,
    DiscoverySpec,
    ErrorActivity,
    FileEditActivity,
    FileReadActivity,
    PermissionSetup,
    ProbeTask,
    RolePermissions,
    TextActivity,
    UsageActivity,
    model_args,
)
from forja.core.artifacts.models import Producer
from forja.executors.claude_code import classify_error

NAME = "cursor"
CONFIG_DIR = "cursor"
CONFIG_FILE = "cli-config.json"

# The variable the documentation gives for authentication, and the proxy ones its configuration
# page names.
ENVIRONMENT = frozenset(
    {
        "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT",
        "CURSOR_API_KEY",
        "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "NODE_USE_ENV_PROXY", "NODE_EXTRA_CA_CERTS",
        "SSL_CERT_FILE",
    }
)  # fmt: skip


class CursorAdapter:
    name = NAME
    contract_version = 1
    # The sandbox and the deny list limit the role, but a role with commands is not held to them
    # (see the module docstring): the core records the limitation.
    enforces_permissions = False
    env_allow = ENVIRONMENT
    discovery = DiscoverySpec(
        executable_names=("agent",),
        extra_locations=("$HOME/.local/bin", "$USERPROFILE/.local/bin"),
        version_args=("--version",),
    )
    probe = ProbeTask(
        prompt=(
            "Create a file named probe.txt in the working directory root"
            " (not inside .forja-agent), containing exactly: forja-ok"
        ),
        expected_file="probe.txt",
        expected_content="forja-ok",
        expected_event_kinds=frozenset({"FileEditActivity", "UsageActivity"}),
    )

    def permission_setup(self, role: Producer, permissions: RolePermissions) -> PermissionSetup:
        deny = ["WebFetch(*)", "Mcp(*:*)"]
        if not permissions.allowed_commands:
            deny.append("Shell(*)")
        if not permissions.can_edit_files:
            deny.append("Write(**)")
        config = {
            "version": 1,
            "editor": {"vimMode": False},
            "permissions": {"allow": [], "deny": deny},
        }
        args = ("--force",) if permissions.can_edit_files else ("--mode", "ask")
        return PermissionSetup({f"{CONFIG_DIR}/{CONFIG_FILE}": json.dumps(config).encode()}, args)

    def plan_invocation(
        self, request: AgentRequest, setup: PermissionSetup, executable: Path
    ) -> CommandPlan:
        config_dir = request.workdir / AGENT_DIR / CONFIG_DIR
        argv = (
            str(executable), "-p", "--output-format", "stream-json",
            "--sandbox", "enabled", "--trust", "--workspace", str(request.workdir),
            *model_args(request), *setup.extra_args,
            f"Read the file {request.package} and do what it asks.",
        )  # fmt: skip
        return CommandPlan(
            argv,
            request.workdir,
            env_allow=self.env_allow,
            env_set={"NO_COLOR": "1", "CURSOR_CONFIG_DIR": str(config_dir)},
        )

    def parse_event(self, line: str) -> AgentActivity | None:
        try:
            data = json.loads(line)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        match data.get("type"):
            case "assistant":
                return _text(data.get("message"))
            case "tool_call" if data.get("subtype") == "started":
                return _tool(data.get("tool_call"))
            case "result":
                return _result(data)
        return None


def _text(message: object) -> AgentActivity | None:
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return None
    parts = [
        block["text"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]  # fmt: skip
    text = "".join(parts)
    return TextActivity(text) if text.strip() else None


def _result(data: Mapping[str, object]) -> AgentActivity | None:
    if data.get("is_error") or data.get("subtype") not in (None, "success"):
        message = str(data.get("result") or "the CLI reported an error")
        return ErrorActivity(message, classify_error(message))
    # no token count in the stream: the use is unknown, not zero
    return UsageActivity()


def _path(value: object) -> str | None:
    return value.replace("\\", "/") if isinstance(value, str) and value else None


def _tool(call: object) -> AgentActivity | None:
    if not isinstance(call, dict):
        return None
    read = call.get("readToolCall")
    path = _path(read.get("args", {}).get("path")) if isinstance(read, dict) else None
    if path:
        return FileReadActivity(path)
    # older versions report writeToolCall with fileText; 2026.11 reports editToolCall with
    # streamContent
    for kind, key in (("writeToolCall", "fileText"), ("editToolCall", "streamContent")):
        write = call.get(kind)
        args = write.get("args") if isinstance(write, dict) else None
        path = _path(args.get("path")) if isinstance(args, dict) else None
        if path and isinstance(args, dict):
            text = args.get(key)
            lines = len(text.splitlines()) if isinstance(text, str) else 0
            return FileEditActivity(path, lines, 0)
    return None
