"""Grok Build as an executor: how to find it, call it headless and read what it says.

Everything about the CLI itself (installation, API key, model) belongs to the user and is done
before Forja runs. What Forja decides is the invocation: ``grok --output-format streaming-json``
with the package as the prompt file, the permission mode ``dontAsk`` (anything without an
explicit allow rule is denied, headless), a home directory of its own (``GROK_HOME``) holding the
allow rules of the role, and no subagent, plan mode, memory or web search.

Sources, read in full before writing this: the Grok Build pages of docs.x.ai (overview, CLI
reference, headless, permissions, sandbox, MCP servers, hooks, AGENTS.md, sessions, settings
reference, enterprise) and the open source of ``xai-org/grok-build`` where the pages leave the
fields out: ``headless/reducer/acp.rs`` (the ``streaming-json`` lines), ``headless/cli.rs`` and
``app/cli.rs`` (flags), ``xai-grok-permission-rules`` (rule syntax), the tool inputs in
``xai-grok-tools`` and ``xai-grok-agent/src/config.rs`` (permission modes).

Known limits, not solved:

* The user's login is not carried (the home directory is Forja's): ``XAI_API_KEY`` must be set.
* Bash rules prefix-match (``Bash(pytest)`` allows ``pytest -x``) and read ``*``, ``?`` and ``[``
  in a command as a glob. The project's ``.grok/config.toml`` may add allow rules: the CLI reads
  its ``[permission]`` section, and the documentation has no switch for it.
* The sandbox is Landlock on Linux and Seatbelt on macOS; on Windows the CLI says it continues
  without one. The profile is passed anyway.
* ``AGENTS.md`` and ``.grok/rules`` are read. Claude and Cursor MCP servers and hooks are turned
  off by their scanner variables; project hooks and MCP servers need a trusted folder, and
  ``--trust`` is not passed.
* The ``streaming-json`` lines were read from the source, not recorded from a run. The tool call
  line may arrive before its arguments (``rawInput`` null): such a call is not reported. A shell
  call carries no exit code.
* A prompt file ending in ``.json`` is read as content blocks by the CLI: the package must not.
* The install location is not documented, so only ``PATH`` is searched.
"""

import json
from collections.abc import Mapping
from pathlib import Path

from forja.core.adapters.common import CommandPlan
from forja.core.adapters.executor import (
    AGENT_DIR,
    AgentActivity,
    AgentRequest,
    CommandActivity,
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

NAME = "grok"
HOME_DIR = "grok"
CONFIG_FILE = "config.toml"

# The variable the documentation gives for authentication, and the proxy ones its settings page
# names.
ENVIRONMENT = frozenset(
    {
        "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT",
        "XAI_API_KEY",
        "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
    }
)  # fmt: skip

# The scanners that would load the MCP servers and hooks of other tools (settings reference).
_SCANNERS = ("MCPS", "HOOKS")
SCANNERS_OFF = {
    f"GROK_{vendor}_{kind}_ENABLED": "0" for vendor in ("CLAUDE", "CURSOR") for kind in _SCANNERS
}

# the role must not rewrite its own rules: deny beats allow
DENIED = ("WebFetch", "WebSearch", "MCPTool", f"Edit(**/{AGENT_DIR}/**)")
END_OK = "end_turn"


def _rule(tool: str, pattern: str | None = None) -> str:
    if pattern is None:
        return tool
    escaped = pattern.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return f"{tool}({escaped})"


def _toml_list(items: list[str]) -> str:
    # a JSON string is a valid TOML basic string
    return "[" + ", ".join(json.dumps(item) for item in items) + "]"


class GrokAdapter:
    name = NAME
    contract_version = 1
    # The allow list is the CLI's own and the project's config may add to it (see the module
    # docstring): the core records the limitation.
    enforces_permissions = False
    env_allow = ENVIRONMENT
    discovery = DiscoverySpec(
        executable_names=("grok",),
        version_args=("version",),
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
        allow = [_rule("Read"), _rule("Grep")]
        if permissions.can_edit_files:
            allow.append(_rule("Edit"))
        allow.extend(_rule("Bash", command) for command in permissions.allowed_commands)
        config = f"[permission]\nallow = {_toml_list(allow)}\ndeny = {_toml_list(list(DENIED))}\n"
        profile = "workspace" if permissions.can_edit_files else "read-only"
        args = ("--permission-mode", "dontAsk", "--sandbox", profile)
        return PermissionSetup({f"{HOME_DIR}/{CONFIG_FILE}": config.encode()}, args)

    def plan_invocation(
        self, request: AgentRequest, setup: PermissionSetup, executable: Path
    ) -> CommandPlan:
        home = request.workdir / AGENT_DIR / HOME_DIR
        argv = (
            str(executable), "--no-auto-update", "--cwd", str(request.workdir),
            "--output-format", "streaming-json",
            "--no-subagents", "--no-plan", "--no-memory", "--disable-web-search",
            *model_args(request), *setup.extra_args,
            "--prompt-file", str(request.package),
        )  # fmt: skip
        return CommandPlan(
            argv,
            request.workdir,
            env_allow=self.env_allow,
            env_set={"NO_COLOR": "1", "GROK_HOME": str(home), **SCANNERS_OFF},
        )

    def parse_event(self, line: str) -> AgentActivity | None:
        try:
            data = json.loads(line)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        match data.get("type"):
            case "text":
                text = data.get("data")
                return TextActivity(text) if isinstance(text, str) and text.strip() else None
            case "tool_call":
                return _tool(data)
            case "end":
                return _end(data)
            case "error":
                message = str(data.get("message") or "the CLI reported an error")
                return ErrorActivity(message, classify_error(message))
            case "max_turns_reached":
                message = "the CLI stopped at its maximum number of turns"
                return ErrorActivity(message, classify_error(message))
        return None


def _end(data: Mapping[str, object]) -> AgentActivity:
    reason = data.get("stopReason")
    if isinstance(reason, str) and reason != END_OK:
        message = f"the CLI stopped: {reason}"
        return ErrorActivity(message, classify_error(message))
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return UsageActivity()
    return UsageActivity(_count(usage.get("input_tokens")), _count(usage.get("output_tokens")))


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _lines(value: object) -> int:
    return len(value.splitlines()) if isinstance(value, str) else 0


def _path(raw: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value.replace("\\", "/")
    return None


def _tool(data: Mapping[str, object]) -> AgentActivity | None:
    raw = data.get("rawInput")
    if not isinstance(raw, dict):
        return None
    name, kind = data.get("toolName"), data.get("kind")
    if name == "bash":
        command = raw.get("command")
        return CommandActivity(command) if isinstance(command, str) and command else None
    if name == "read_file" or kind == "read":
        path = _path(raw, "target_file", "file_path", "path")
        return FileReadActivity(path) if path else None
    if name == "search_replace":
        path = _path(raw, "file_path")
        if path:
            return FileEditActivity(
                path, _lines(raw.get("new_string")), _lines(raw.get("old_string"))
            )
        return None
    if name == "write" or kind == "edit":
        path = _path(raw, "file_path", "path")
        content = raw.get("content", raw.get("contents"))
        return FileEditActivity(path, _lines(content), 0) if path else None
    return None
