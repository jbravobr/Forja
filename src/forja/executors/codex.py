"""Codex CLI as an executor: how to find it, call it headless and read what it says.

Everything about the CLI itself (installation, login, model, provider) belongs to the user and is
done before Forja runs. What Forja decides is the invocation: ``codex exec --json`` with the prompt
on stdin, no saved session, the sandbox of the role, no execution-policy rules and no project
instructions (``AGENTS.md`` of the repository could carry instructions to the agent), no hooks.

Sources, read in full before writing this: the non-interactive mode page and the command reference
of the Codex documentation, and ``codex-rs/exec/src/{cli,lib,exec_events}.rs`` of the open source
repository (event shapes, exit codes, stdin handling).
"""

import json
from collections.abc import Mapping
from pathlib import Path

from forja.core.adapters.common import CommandPlan
from forja.core.adapters.executor import (
    AgentActivity,
    AgentRequest,
    CommandActivity,
    DiscoverySpec,
    ErrorActivity,
    FileEditActivity,
    PermissionSetup,
    ProbeTask,
    RolePermissions,
    TextActivity,
    UsageActivity,
    model_args,
)
from forja.core.artifacts.models import Producer
from forja.executors.claude_code import classify_error

NAME = "codex"

# The CLI's own configuration, as the user set it up (``CODEX_HOME`` holds its login). Only the
# variable the documentation gives for an API key is passed on.
ENVIRONMENT = frozenset(
    {
        "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT",
        "CODEX_HOME", "CODEX_API_KEY",
        "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE",
    }
)  # fmt: skip

# Overrides (``-c key=value``) for what a repository or a profile could otherwise inject.
OVERRIDES = (
    "project_doc_max_bytes=0",
    "features.hooks=false",
    "sandbox_workspace_write.network_access=false",
    "sandbox_workspace_write.writable_roots=[]",
)


class CodexAdapter:
    name = NAME
    contract_version = 1
    # The sandbox keeps a role from writing outside the working copy, but Codex has no list of
    # commands a role may run: the core records the limitation.
    enforces_permissions = False
    env_allow = ENVIRONMENT
    discovery = DiscoverySpec(
        executable_names=("codex",),
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
        sandbox = "workspace-write" if permissions.can_edit_files else "read-only"
        return PermissionSetup({}, ("--sandbox", sandbox))

    def plan_invocation(
        self, request: AgentRequest, setup: PermissionSetup, executable: Path
    ) -> CommandPlan:
        overrides = tuple(part for item in OVERRIDES for part in ("-c", item))
        argv = (
            str(executable), "exec", "--json", "--ephemeral", "--ignore-rules",
            "--color", "never", "--cd", str(request.workdir),
            *overrides, *model_args(request), *setup.extra_args,
            "-",
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
        match data.get("type"):
            case "item.completed":
                return _item(data.get("item"))
            case "turn.completed":
                return _usage(data.get("usage"))
            case "turn.failed":
                return _failure(data.get("error"))
            case "error":
                return _failure(data)
        return None


def _count(value: object) -> int:
    return max(value, 0) if isinstance(value, int) and not isinstance(value, bool) else 0


def _usage(usage: object) -> AgentActivity | None:
    if not isinstance(usage, dict):
        return None
    # the cached part of the input is not counted: it costs a fraction and varies between runs
    sent = max(_count(usage.get("input_tokens")) - _count(usage.get("cached_input_tokens")), 0)
    return UsageActivity(sent, _count(usage.get("output_tokens")))


def _failure(data: object) -> AgentActivity | None:
    if not isinstance(data, dict):
        return None
    message = str(data.get("message") or "the CLI reported an error")
    return ErrorActivity(message, classify_error(message))


def _item(item: object) -> AgentActivity | None:
    if not isinstance(item, dict):
        return None
    match item.get("type"):
        case "agent_message":
            text = item.get("text")
            return TextActivity(text) if isinstance(text, str) and text.strip() else None
        case "command_execution":
            command = item.get("command")
            if not isinstance(command, str):
                return None
            code = item.get("exit_code")
            return CommandActivity(command, code if isinstance(code, int) else None)
        case "file_change":
            return _file_change(item)
    return None


def _file_change(item: Mapping[str, object]) -> AgentActivity | None:
    if item.get("status") != "completed":
        return None
    changes = item.get("changes")
    if not isinstance(changes, list):
        return None
    for change in changes:
        path = change.get("path") if isinstance(change, dict) else None
        if isinstance(path, str) and path:
            # the stream names the file and the kind of change, not how many lines
            return FileEditActivity(path.replace("\\", "/"), 0, 0)
    return None
