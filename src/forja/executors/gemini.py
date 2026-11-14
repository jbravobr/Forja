"""Gemini CLI as an executor: how to find it, call it headless and read what it says.

Everything about the CLI itself (installation, login, model) belongs to the user and is done
before Forja runs. What Forja decides is the invocation: ``gemini -p`` with the package on stdin
(the ``-p`` text is appended to it), ``--output-format stream-json`` and a policy file that allows
only the tools of the role. Headless, a call no rule allows is denied, and the file ends in a
deny-all rule.

Sources, read in full before writing this: the headless, CLI reference, policy engine, trusted
folders and authentication pages of the Gemini CLI documentation, and the open source repository
(``core/src/output/types.ts`` for the event shapes, ``core/src/tools/definitions/
base-declarations.ts`` for tool and parameter names, ``core/src/policy/config.ts`` for the tier of
``--policy`` files, ``core/src/utils/trust.ts`` and ``cli/src/utils/userStartupWarnings.ts`` for
folder trust).

Known limits, not solved: headless needs a trusted folder (an untrusted one is a fatal error), so
the working copy is trusted by ``GEMINI_CLI_TRUST_WORKSPACE`` and the project's
``.gemini/settings.json`` and ``.env`` are read; the context file (``GEMINI.md``) cannot be
switched off.
"""

import json
import re
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

NAME = "gemini"
POLICY_FILE = "gemini-policy.toml"
INSTRUCTION = "Do what the instructions above ask."

# The CLI's own login and configuration, as the user set it up. Only the variables the
# authentication page gives are passed on.
ENVIRONMENT = frozenset(
    {
        "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT",
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT_ID", "GOOGLE_CLOUD_LOCATION",
        "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE",
    }
)  # fmt: skip

READ_TOOLS = ("read_file", "read_many_files", "glob", "grep_search", "list_directory")
EDIT_TOOLS = ("write_file", "replace")
SHELL_TOOL = "run_shell_command"

# MCP servers named in the project's settings are not allowed to connect: no server has this name.
NO_MCP_SERVER = "forja-none"


class GeminiAdapter:
    name = NAME
    contract_version = 1
    # The policy limits the tools and the commands, but the project's settings and ``.env`` are
    # read (see the module docstring): the core records the limitation.
    enforces_permissions = False
    env_allow = ENVIRONMENT
    discovery = DiscoverySpec(
        executable_names=("gemini",),
        extra_locations=("$HOME/.local/bin", "$USERPROFILE/.local/bin", "$APPDATA/npm"),
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
        policy = _policy(permissions).encode("utf-8")
        return PermissionSetup({POLICY_FILE: policy}, ("--policy", f"{AGENT_DIR}/{POLICY_FILE}"))

    def plan_invocation(
        self, request: AgentRequest, setup: PermissionSetup, executable: Path
    ) -> CommandPlan:
        argv = (
            str(executable), "-p", INSTRUCTION, "--output-format", "stream-json",
            "--approval-mode", "default",
            "--allowed-mcp-server-names", NO_MCP_SERVER,
            *model_args(request), *setup.extra_args,
        )  # fmt: skip
        return CommandPlan(
            argv,
            request.workdir,
            env_allow=self.env_allow,
            env_set={"NO_COLOR": "1", "GEMINI_CLI_TRUST_WORKSPACE": "true"},
            stdin_file=request.package,
        )

    def parse_event(self, line: str) -> AgentActivity | None:
        try:
            data = json.loads(line)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        match data.get("type"):
            case "message":
                text = data.get("content")
                if data.get("role") == "assistant" and isinstance(text, str) and text.strip():
                    return TextActivity(text)
            case "tool_use":
                return _tool(data.get("tool_name"), data.get("parameters"))
            case "result":
                return _result(data)
        return None


def _toml(value: str) -> str:
    return json.dumps(value)


def _rule(tool: str, decision: str, priority: int, extra: str = "") -> str:
    return (
        f"[[rule]]\ntoolName = {_toml(tool)}\ndecision = {_toml(decision)}\n"
        f"priority = {priority}\n{extra}"
    )


def _command_rule(command: str) -> str:
    # The pattern is tested against the arguments as JSON, after ``"command":"``: the closing quote
    # makes it the whole command, so ``cmd; other`` does not pass.
    escaped = re.escape(json.dumps(command)[1:-1])
    return _rule(SHELL_TOOL, "allow", 100, f"commandRegex = {_toml(escaped + chr(34))}\n")


def _policy(permissions: RolePermissions) -> str:
    tools = (*READ_TOOLS, *(EDIT_TOOLS if permissions.can_edit_files else ()))
    rules = [_rule(tool, "allow", 100) for tool in tools]
    rules += [_command_rule(command) for command in permissions.allowed_commands]
    rules.append(_rule("*", "deny", 1))
    return "\n".join(rules)


def _count(value: object) -> int:
    return max(value, 0) if isinstance(value, int) and not isinstance(value, bool) else 0


def _result(data: Mapping[str, object]) -> AgentActivity | None:
    if data.get("status") == "error":
        error = data.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        text = str(message or "the CLI reported an error")
        return ErrorActivity(text, classify_error(text))
    stats = data.get("stats")
    if not isinstance(stats, dict):
        return None
    # what was read from the cache is not counted: it costs a fraction and varies between runs
    sent = max(_count(stats.get("input_tokens")) - _count(stats.get("cached")), 0)
    return UsageActivity(sent, _count(stats.get("output_tokens")))


def _path(value: object) -> str | None:
    return value.replace("\\", "/") if isinstance(value, str) and value else None


def _lines(value: object) -> int:
    return len(value.splitlines()) if isinstance(value, str) else 0


def _tool(name: object, arguments: object) -> AgentActivity | None:
    if not isinstance(arguments, dict):
        return None
    path = _path(arguments.get("file_path"))
    match name:
        case "read_file" if path:
            return FileReadActivity(path)
        case "write_file" if path:
            return FileEditActivity(path, _lines(arguments.get("content")), 0)
        case "replace" if path:
            return FileEditActivity(
                path, _lines(arguments.get("new_string")), _lines(arguments.get("old_string"))
            )
        case "run_shell_command" if isinstance(arguments.get("command"), str):
            return CommandActivity(arguments["command"])
    return None
