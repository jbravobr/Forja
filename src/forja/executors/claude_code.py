"""Claude Code as an executor: how to find it, call it headless and read what it says.

Everything about the CLI itself (installation, login, model, gateway) belongs to the user and is
done before Forja runs; Forja only checks it (the proof of life) and never configures it. What
Forja does decide is the invocation: only the user's own settings are read (the repository's
settings and hooks are not, since they could carry instructions to the agent), no MCP servers, no
slash commands, no saved session, and the tools of the role.
"""

import json
import re
from collections.abc import Mapping
from pathlib import Path

from forja.core.adapters.common import CommandPlan
from forja.core.adapters.executor import (
    AGENT_DIR,
    DIARY_FILE,
    AgentActivity,
    AgentRequest,
    CommandActivity,
    DiscoverySpec,
    ErrorActivity,
    ErrorKind,
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

NAME = "claude-code"
SETTINGS_FILE = "claude-settings.json"
INSTRUCTIONS_FILE = "instructions.md"
# Hooks of the user's profile would otherwise run inside every agent call and add their output to
# what the agent reads.
# Best effort, and the CLI's own rules: the agent may not write its own instructions and
# permissions, which sit inside the copy. Changing them is also checked for when the work comes
# back. What keeps the file tools under the working copy is the scope each one is allowed with.
OWN = tuple(
    f"{tool}(./{AGENT_DIR}/{name})"
    for tool in ("Edit", "Write")
    for name in (SETTINGS_FILE, INSTRUCTIONS_FILE, "package.md", DIARY_FILE)
)
SETTINGS = {"disableAllHooks": True, "permissions": {"deny": list(OWN)}}

READ_TOOLS = ("Read", "Glob", "Grep")


def _scoped(tool: str) -> str:
    """Allowed only for paths under the working directory; anything else is refused (dontAsk)."""
    return f"{tool}(./**)"


EDIT_TOOLS = ("Edit", "Write")

# The CLI's own configuration, as the user set it up. The variables a host application sets
# around the CLI (CLAUDE_CODE_*) are deliberately absent: they describe another session.
ENVIRONMENT = frozenset(
    {
        "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT", "FORJA_AGENT_PYTHON",
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
        "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
        "AWS_PROFILE", "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
        "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE",
    }
)  # fmt: skip

_BLOCKING = re.compile(
    r"authenticat|oauth|log ?in|api key|unauthori[sz]ed|forbidden|credit balance|quota"
    r"|usage limit|billing|permission denied",
    re.IGNORECASE,
)
_PERSISTENT = re.compile(
    r"ENOTFOUND|ECONNREFUSED|EAI_AGAIN|getaddrinfo|network is unreachable|no internet|offline",
    re.IGNORECASE,
)


def classify_error(message: str, status: object = None) -> ErrorKind:
    """What a failure means for the run: wait and retry, pause, or stop for the user."""
    if isinstance(status, int) and not isinstance(status, bool):
        if status in (401, 402, 403):
            return ErrorKind.BLOCKING
        if status == 429 or status >= 500:
            return ErrorKind.TRANSIENT
    if _BLOCKING.search(message):
        return ErrorKind.BLOCKING
    if _PERSISTENT.search(message):
        return ErrorKind.PERSISTENT
    return ErrorKind.TRANSIENT


def _lines(text: object) -> int:
    return len(text.splitlines()) if isinstance(text, str) and text else 0


def _path(value: object) -> str | None:
    return value.replace("\\", "/") if isinstance(value, str) and value else None


class ClaudeCodeAdapter:
    name = NAME
    contract_version = 1
    enforces_permissions = True
    env_allow = ENVIRONMENT
    discovery = DiscoverySpec(
        executable_names=("claude",),
        extra_locations=("$HOME/.local/bin", "$USERPROFILE/.local/bin", "$APPDATA/npm"),
        version_args=("--version",),
        min_version="2.0.0",
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
        tools = [*READ_TOOLS, *(EDIT_TOOLS if permissions.can_edit_files else ())]
        # Glob and Grep are not allowed by name: a bare allow reaches any directory, while the
        # file rules cover them under the working directory only
        allowed = [_scoped(tool) for tool in tools if tool not in ("Glob", "Grep")]
        if permissions.allowed_commands:
            tools.append("Bash")
            # Exact commands only: a trailing `*` also matches `; anything else` (seen live).
            allowed += [f"Bash({command})" for command in permissions.allowed_commands]
        return PermissionSetup(
            {SETTINGS_FILE: json.dumps(SETTINGS).encode("utf-8")},
            ("--tools", ",".join(tools), "--allowedTools", ",".join(allowed)),
        )

    def instruction_setup(self, text: str) -> PermissionSetup:
        """The text goes after the default system prompt, which keeps the CLI's own tool and
        safety guidance; ``--system-prompt`` would replace it."""
        return PermissionSetup(
            {INSTRUCTIONS_FILE: text.encode("utf-8")},
            ("--append-system-prompt-file", f"{AGENT_DIR}/{INSTRUCTIONS_FILE}"),
        )

    def plan_invocation(
        self, request: AgentRequest, setup: PermissionSetup, executable: Path
    ) -> CommandPlan:
        argv = (
            str(executable), "-p", "--output-format", "stream-json", "--verbose",
            "--permission-mode", "dontAsk",
            "--setting-sources", "user", "--settings", f"{AGENT_DIR}/{SETTINGS_FILE}",
            "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
            *model_args(request), *setup.extra_args,
        )  # fmt: skip
        return CommandPlan(
            argv,
            request.workdir,
            env_allow=self.env_allow,
            env_set={"NO_COLOR": "1"},
            stdin_file=request.package,
        )

    def parse_event(self, line: str) -> AgentActivity | None:
        try:
            data = json.loads(line)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        if data.get("type") == "result":
            return _result(data)
        if data.get("type") == "assistant" and not data.get("error"):
            return _assistant(data)
        return None


def _result(data: Mapping[str, object]) -> AgentActivity | None:
    if data.get("is_error"):
        message = str(data.get("result") or "the CLI reported an error")
        return ErrorActivity(message, classify_error(message, data.get("api_error_status")))
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    # what was read from the cache is not counted: it costs a fraction and varies between runs
    sent = _count(usage.get("input_tokens")) + _count(usage.get("cache_creation_input_tokens"))
    return UsageActivity(sent, _count(usage.get("output_tokens")))


def _count(value: object) -> int:
    return max(value, 0) if isinstance(value, int) and not isinstance(value, bool) else 0


def _assistant(data: Mapping[str, object]) -> AgentActivity | None:
    message = data.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            seen = _tool(str(block.get("name")), block.get("input"))
            if seen is not None:
                return seen
        elif block.get("type") == "text" and str(block.get("text", "")).strip():
            return TextActivity(str(block["text"]))
    return None


def _tool(name: str, arguments: object) -> AgentActivity | None:
    if not isinstance(arguments, dict):
        return None
    path = _path(arguments.get("file_path") or arguments.get("path"))
    if name == "Read" and path:
        return FileReadActivity(path)
    if name == "Edit" and path:
        return FileEditActivity(
            path, _lines(arguments.get("new_string")), _lines(arguments.get("old_string"))
        )
    if name == "Write" and path:
        return FileEditActivity(path, _lines(arguments.get("content")), 0)
    if name == "Bash" and isinstance(arguments.get("command"), str):
        return CommandActivity(arguments["command"])
    return None
