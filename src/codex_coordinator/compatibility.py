"""Fail-closed compatibility gate for the experimental Codex app-server wire.

The CLI generates version-specific JSON Schemas as documented at
https://learn.chatgpt.com/docs/app-server#message-schema.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


SUPPORTED_CODEX_VERSIONS = frozenset({"0.154.0"})


class CodexCompatibilityError(RuntimeError):
    pass


def _required(definition: dict[str, Any], fields: set[str], context: str) -> None:
    if not fields.issubset(set(definition.get("required", []))):
        raise CodexCompatibilityError(f"incompatible app-server schema: {context} required fields changed")


def _properties(definition: dict[str, Any], fields: set[str], context: str) -> None:
    if not fields.issubset(set(definition.get("properties", {}))):
        raise CodexCompatibilityError(f"incompatible app-server schema: {context} fields changed")


def _method(schema: dict[str, Any], name: str, ref: str) -> None:
    variants = schema.get("oneOf", [])
    match = next((
        item for item in variants
        if name in item.get("properties", {}).get("method", {}).get("enum", [])
    ), None)
    if match is None or match.get("properties", {}).get("params", {}).get("$ref") != ref:
        raise CodexCompatibilityError(f"incompatible app-server schema: {name} request changed")
    _required(match, {"method", "params"}, name)


#: The complete set of approval policies the pinned CLI accepts on the wire,
#: and the complete set of categories ``granular`` can be told to ask about.
#: Neither separates an in-project file change from a command, which is why
#: the coordinator answers file-change approvals itself rather than
#: configuring the runtime not to raise them (issue #0018). This is asserted
#: rather than merely documented so that a CLI upgrade adding a policy or a
#: category fails the gate and sends someone back to that issue.
WIRE_APPROVAL_POLICIES = frozenset({"untrusted", "on-request", "never"})
GRANULAR_APPROVAL_CATEGORIES = frozenset({
    "mcp_elicitations", "request_permissions", "rules", "sandbox_approval",
    "skill_approval",
})


def _approval_policies(definition: dict[str, Any]) -> None:
    """Fail if the runtime gained or lost a way to decide what it escalates."""
    plain: set[str] = set()
    granular: set[str] | None = None
    for variant in definition.get("oneOf", []):
        plain.update(variant.get("enum", []))
        properties = variant.get("properties", {}).get("granular", {}).get("properties")
        if isinstance(properties, dict):
            granular = set(properties)
    if plain != set(WIRE_APPROVAL_POLICIES):
        raise CodexCompatibilityError(
            "Codex approval policies changed: expected "
            f"{sorted(WIRE_APPROVAL_POLICIES)}, found {sorted(plain)}. Re-read issue "
            "#0018 before adopting this CLI; a new policy may remove the need for the "
            "coordinator to decide in-project file changes itself."
        )
    if granular is None or granular != set(GRANULAR_APPROVAL_CATEGORIES):
        raise CodexCompatibilityError(
            "Codex granular approval categories changed: expected "
            f"{sorted(GRANULAR_APPROVAL_CATEGORIES)}, found "
            f"{sorted(granular) if granular is not None else 'no granular variant'}. "
            "Re-read issue #0018 before adopting this CLI; a file-change category "
            "would remove the need for the coordinator to decide those itself."
        )


#: The network axes a permission profile can control independently, as the
#: runtime reports them. A profile is the only shape that can express a
#: host-scoped ceiling: the wire grant type carries one bit, while these carry a
#: per-domain map, a per-socket map, and loopback binding separately. The
#: config-file spelling differs (`network.mode`, `network.domains`,
#: `network.unix_sockets`, `network.allow_local_binding`) and `mode` has no wire
#: key at all, so only the reported shape is asserted here.
PROFILE_NETWORK_AXES = frozenset({
    "allowLocalBinding", "domains", "enabled", "unixSockets",
})


def _permission_profile_surface(requests: dict[str, Any], v2: dict[str, Any]) -> None:
    """Fail if the runtime loses the shape that gives a boundary its provenance.

    Issue #0021: the coordinator selects a named permission profile rather than
    sending the legacy sandbox literal, because only a profile is reported back
    as `activePermissionProfile` and only a profile can carry a host-scoped
    network ceiling. A CLI that drops `permissions` or renames these keys has
    taken that away, and the coordinator must refuse to start rather than fall
    back to a shape with no provenance.

    The one rule this cannot check is that `permissions` and `sandbox` are
    mutually exclusive: the schema states it only in prose, and the runtime
    enforces it by answering `-32600`. That is asserted live instead, in
    `tests/test_runtime_boundary.py`.
    """
    for params in ("ThreadStartParams", "TurnStartParams"):
        if "permissions" not in requests["definitions"][params].get("properties", {}):
            raise CodexCompatibilityError(
                f"Codex dropped the {params} `permissions` field. Re-read issue #0021 "
                "before adopting this CLI: without it the only way to set a boundary is "
                "the legacy sandbox literal, which the runtime reports no profile "
                "provenance for."
            )
    if "activePermissionProfile" not in v2["ThreadStartResponse"].get("properties", {}):
        raise CodexCompatibilityError(
            "Codex dropped `activePermissionProfile` from the thread/start response. "
            "Re-read issue #0021 before adopting this CLI: the coordinator verifies the "
            "profile the server actually selected, and cannot do so from the legacy "
            "`sandbox` view, which is lossy for host-scoped network grants."
        )
    _required(v2["ActivePermissionProfile"], {"id"}, "active permission profile")
    _properties(v2["ActivePermissionProfile"], {"id", "extends"}, "active permission profile")
    axes = set(v2["NetworkRequirements"].get("properties", {}))
    if not PROFILE_NETWORK_AXES.issubset(axes):
        raise CodexCompatibilityError(
            "Codex network permission axes changed: expected "
            f"{sorted(PROFILE_NETWORK_AXES)}, found {sorted(axes)}. Re-read issue #0021 "
            "before adopting this CLI; the live boundary gate asserts external hosts, "
            "unix sockets, and loopback binding as separately controllable axes."
        )
    for definition, context in (
        ("NetworkDomainPermission", "network domain permission"),
        ("NetworkUnixSocketPermission", "network unix socket permission"),
    ):
        if set(v2[definition].get("enum", [])) != {"allow", "deny"}:
            raise CodexCompatibilityError(
                f"Codex {context} values changed: expected ['allow', 'deny'], found "
                f"{sorted(v2[definition].get('enum', []))}. Re-read issue #0021 before "
                "adopting this CLI."
            )


def _decisions(definition: dict[str, Any], expected: set[str], context: str) -> None:
    found = {
        value for item in definition.get("oneOf", [])
        for value in item.get("enum", [])
    }
    if not expected.issubset(found):
        raise CodexCompatibilityError(f"incompatible app-server schema: {context} decisions changed")


def check_protocol_schema(schema_dir: Path) -> None:
    """Check only wire shapes this package actually sends or consumes."""
    try:
        _check_protocol_schema(schema_dir)
    except (KeyError, TypeError, AttributeError) as exc:
        raise CodexCompatibilityError(
            "incompatible app-server schema: a required protocol definition is missing or malformed"
        ) from exc


def _check_protocol_schema(schema_dir: Path) -> None:
    try:
        requests = json.loads((schema_dir / "ClientRequest.json").read_text())
        server_requests = json.loads((schema_dir / "ServerRequest.json").read_text())
        notifications = json.loads((schema_dir / "ServerNotification.json").read_text())
        bundle = json.loads((schema_dir / "codex_app_server_protocol.schemas.json").read_text())
    except (OSError, ValueError) as exc:
        raise CodexCompatibilityError(f"cannot read generated app-server schemas: {exc}") from exc

    for name, param in (
        ("thread/start", "ThreadStartParams"),
        ("turn/start", "TurnStartParams"),
        ("turn/interrupt", "TurnInterruptParams"),
        ("thread/read", "ThreadReadParams"),
    ):
        _method(requests, name, f"#/definitions/{param}")
    _properties(
        requests["definitions"]["ThreadStartParams"],
        {"cwd", "runtimeWorkspaceRoots", "approvalPolicy", "approvalsReviewer", "sandbox"},
        "thread/start",
    )
    _properties(
        requests["definitions"]["TurnStartParams"],
        {"threadId", "cwd", "input", "sandboxPolicy", "turnTrigger", "effort"},
        "turn/start",
    )
    _required(requests["definitions"]["TurnStartParams"], {"threadId", "input"}, "turn/start")
    _approval_policies(requests["definitions"]["AskForApproval"])
    _permission_profile_surface(requests, bundle["definitions"]["v2"])
    _properties(requests["definitions"]["TurnInterruptParams"], {"threadId", "turnId"}, "turn/interrupt")
    _required(requests["definitions"]["TurnInterruptParams"], {"threadId", "turnId"}, "turn/interrupt")
    v2 = bundle["definitions"]["v2"]
    _required(v2["ThreadStartResponse"], {"thread"}, "thread/start response")
    _required(v2["Thread"], {"id"}, "thread/start thread")
    _required(v2["TurnStartResponse"], {"turn"}, "turn/start response")
    _required(v2["Turn"], {"id", "status"}, "turn/start turn")

    approvals = (
        ("item/commandExecution/requestApproval", "CommandExecutionRequestApprovalParams", "CommandExecutionRequestApprovalResponse"),
        ("item/fileChange/requestApproval", "FileChangeRequestApprovalParams", "FileChangeRequestApprovalResponse"),
        ("item/permissions/requestApproval", "PermissionsRequestApprovalParams", "PermissionsRequestApprovalResponse"),
    )
    definitions = bundle["definitions"]
    for name, params, response in approvals:
        _method(server_requests, name, f"#/definitions/{params}")
        _required(definitions[params], {"threadId", "turnId", "itemId", "startedAtMs"}, name)
        if response not in definitions:
            raise CodexCompatibilityError(f"incompatible app-server schema: {name} response missing")
    _properties(
        definitions["CommandExecutionRequestApprovalParams"],
        {"command", "cwd", "availableDecisions", "commandActions", "additionalPermissions"},
        "command approval request",
    )
    _properties(definitions["FileChangeRequestApprovalParams"], {"grantRoot"}, "file approval request")
    _required(definitions["PermissionsRequestApprovalParams"], {"cwd", "permissions"}, "permission approval request")
    _properties(definitions["CommandExecutionRequestApprovalResponse"], {"decision"}, "command approval response")
    _properties(definitions["FileChangeRequestApprovalResponse"], {"decision"}, "file approval response")
    _properties(definitions["PermissionsRequestApprovalResponse"], {"permissions", "scope", "strictAutoReview"}, "permission approval response")
    _required(definitions["PermissionsRequestApprovalResponse"], {"permissions"}, "permission approval response")
    if definitions["CommandExecutionRequestApprovalResponse"]["properties"]["decision"].get("$ref") != "#/definitions/CommandExecutionApprovalDecision":
        raise CodexCompatibilityError("incompatible app-server schema: command approval response decision changed")
    if definitions["FileChangeRequestApprovalResponse"]["properties"]["decision"].get("$ref") != "#/definitions/FileChangeApprovalDecision":
        raise CodexCompatibilityError("incompatible app-server schema: file approval response decision changed")
    _decisions(definitions["CommandExecutionApprovalDecision"], {"accept", "acceptForSession", "decline"}, "command approval")
    _decisions(definitions["FileChangeApprovalDecision"], {"accept", "acceptForSession", "decline"}, "file approval")
    if not {"turn", "session"}.issubset(set(definitions["PermissionGrantScope"].get("enum", []))):
        raise CodexCompatibilityError("incompatible app-server schema: permission grant scopes changed")

    _method(notifications, "turn/completed", "#/definitions/TurnCompletedNotification")
    _required(notifications["definitions"]["TurnCompletedNotification"], {"threadId", "turn"}, "turn/completed")
    _properties(notifications["definitions"]["Turn"], {"id", "status"}, "turn/completed turn")
    if not {"completed", "failed", "interrupted", "inProgress"}.issubset(
        set(notifications["definitions"]["TurnStatus"].get("enum", []))
    ):
        raise CodexCompatibilityError("incompatible app-server schema: turn statuses changed")
    _method(notifications, "serverRequest/resolved", "#/definitions/ServerRequestResolvedNotification")
    _required(
        notifications["definitions"]["ServerRequestResolvedNotification"],
        {"requestId", "threadId"}, "server request receipt",
    )
    _method(notifications, "item/started", "#/definitions/ItemStartedNotification")
    _required(
        notifications["definitions"]["ItemStartedNotification"],
        {"item", "threadId", "turnId"}, "item start",
    )
    _method(notifications, "item/completed", "#/definitions/ItemCompletedNotification")
    _required(
        notifications["definitions"]["ItemCompletedNotification"],
        {"item", "threadId", "turnId"}, "item completion",
    )
    command_items = [
        variant for variant in notifications["definitions"]["ThreadItem"].get("oneOf", [])
        if "commandExecution" in variant.get("properties", {}).get("type", {}).get("enum", [])
    ]
    if len(command_items) != 1:
        raise CodexCompatibilityError("incompatible app-server schema: command item changed")
    _required(command_items[0], {"id", "status", "type", "command", "cwd"}, "command item")
    file_items = [
        variant for variant in notifications["definitions"]["ThreadItem"].get("oneOf", [])
        if "fileChange" in variant.get("properties", {}).get("type", {}).get("enum", [])
    ]
    if len(file_items) != 1:
        raise CodexCompatibilityError("incompatible app-server schema: file item changed")
    _required(file_items[0], {"id", "status", "type", "changes"}, "file item")
    _required(notifications["definitions"]["FileUpdateChange"], {"path"}, "file change")
    if not {"completed", "failed", "declined"}.issubset(
        set(notifications["definitions"]["CommandExecutionStatus"].get("enum", []))
    ):
        raise CodexCompatibilityError("incompatible app-server schema: command completion statuses changed")


def check_codex_compatibility(codex_command: str = "codex") -> str:
    """Return the supported CLI version or raise an actionable startup error."""
    executable = shutil.which(codex_command)
    if executable is None:
        raise CodexCompatibilityError(
            f"Codex executable {codex_command!r} was not found; install Codex CLI or set codex_command"
        )
    try:
        version_result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True,
            timeout=10, check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CodexCompatibilityError(f"cannot run Codex CLI {executable}: {exc}") from exc
    match = re.fullmatch(r"codex-cli (\d+\.\d+\.\d+)", version_result.stdout.strip())
    version = match.group(1) if match else "unrecognized"
    if version not in SUPPORTED_CODEX_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_CODEX_VERSIONS))
        raise CodexCompatibilityError(
            f"Codex CLI {version} is not verified for this package; supported: {supported}"
        )
    with tempfile.TemporaryDirectory(prefix="codex-coordinator-schema-") as temporary:
        try:
            result = subprocess.run(
                [executable, "app-server", "generate-json-schema", "--experimental", "--out", temporary],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CodexCompatibilityError(f"could not generate Codex app-server schema: {exc}") from exc
        if result.returncode:
            raise CodexCompatibilityError(
                f"could not generate Codex app-server schema: {result.stderr.strip()}"
            )
        check_protocol_schema(Path(temporary))
    return version
