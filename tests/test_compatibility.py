import json
import subprocess
from pathlib import Path

import pytest

from codex_coordinator.compatibility import (
    CodexCompatibilityError, check_codex_compatibility, check_protocol_schema,
)


def _variant(method, params):
    return {
        "properties": {
            "method": {"enum": [method]},
            "params": {"$ref": f"#/definitions/{params}"},
        },
        "required": ["method", "params"],
    }


def _schema_fixture(root: Path):
    requests = {
        "oneOf": [_variant(method, params) for method, params in (
            ("thread/start", "ThreadStartParams"),
            ("turn/start", "TurnStartParams"),
            ("turn/interrupt", "TurnInterruptParams"),
            ("thread/read", "ThreadReadParams"),
        )],
        "definitions": {
            "ThreadStartParams": {"properties": {
                key: {} for key in (
                    "cwd", "runtimeWorkspaceRoots", "approvalPolicy", "approvalsReviewer",
                    "sandbox", "permissions",
                )
            }},
            "TurnStartParams": {
                "properties": {key: {} for key in (
                    "threadId", "cwd", "input", "sandboxPolicy", "turnTrigger", "effort",
                    "permissions",
                )},
                "required": ["threadId", "input"],
            },
            "TurnInterruptParams": {
                "properties": {"threadId": {}, "turnId": {}},
                "required": ["threadId", "turnId"],
            },
            "AskForApproval": {
                "oneOf": [
                    {"enum": ["untrusted", "on-request", "never"], "type": "string"},
                    {"properties": {"granular": {"properties": {
                        key: {"type": "boolean"} for key in (
                            "mcp_elicitations", "request_permissions", "rules",
                            "sandbox_approval", "skill_approval",
                        )
                    }}}},
                ],
            },
        },
    }
    approvals = (
        ("item/commandExecution/requestApproval", "CommandExecutionRequestApprovalParams", "CommandExecutionRequestApprovalResponse"),
        ("item/fileChange/requestApproval", "FileChangeRequestApprovalParams", "FileChangeRequestApprovalResponse"),
        ("item/permissions/requestApproval", "PermissionsRequestApprovalParams", "PermissionsRequestApprovalResponse"),
    )
    server_requests = {"oneOf": [_variant(name, params) for name, params, _ in approvals]}
    notifications = {
        "oneOf": [
            _variant("turn/completed", "TurnCompletedNotification"),
            _variant("serverRequest/resolved", "ServerRequestResolvedNotification"),
            _variant("item/started", "ItemStartedNotification"),
            _variant("item/completed", "ItemCompletedNotification"),
        ],
        "definitions": {
            "TurnCompletedNotification": {"required": ["threadId", "turn"]},
            "Turn": {"properties": {"id": {}, "status": {}}},
            "TurnStatus": {"enum": ["completed", "failed", "interrupted", "inProgress"]},
            "ServerRequestResolvedNotification": {"required": ["requestId", "threadId"]},
            "ItemStartedNotification": {"required": ["item", "threadId", "turnId"]},
            "ItemCompletedNotification": {"required": ["item", "threadId", "turnId"]},
            "ThreadItem": {"oneOf": [
                {
                    "properties": {"type": {"enum": ["commandExecution"]}},
                    "required": ["id", "status", "type", "command", "cwd"],
                },
                {
                    "properties": {"type": {"enum": ["fileChange"]}},
                    "required": ["id", "status", "type", "changes"],
                },
            ]},
            "FileUpdateChange": {"required": ["path"]},
            "CommandExecutionStatus": {"enum": ["inProgress", "completed", "failed", "declined"]},
        },
    }
    definitions = {
        params: {"required": ["threadId", "turnId", "itemId", "startedAtMs"]}
        for _, params, _ in approvals
    }
    definitions["CommandExecutionRequestApprovalParams"]["properties"] = {
        key: {} for key in ("command", "cwd", "availableDecisions", "commandActions", "additionalPermissions")
    }
    definitions["FileChangeRequestApprovalParams"]["properties"] = {"grantRoot": {}}
    definitions["PermissionsRequestApprovalParams"]["required"] += ["cwd", "permissions"]
    definitions.update({
        "CommandExecutionRequestApprovalResponse": {"properties": {"decision": {"$ref": "#/definitions/CommandExecutionApprovalDecision"}}},
        "FileChangeRequestApprovalResponse": {"properties": {"decision": {"$ref": "#/definitions/FileChangeApprovalDecision"}}},
        "PermissionsRequestApprovalResponse": {
            "properties": {"permissions": {}, "scope": {}, "strictAutoReview": {}},
            "required": ["permissions"],
        },
        "CommandExecutionApprovalDecision": {"oneOf": [
            {"enum": [value]} for value in ("accept", "acceptForSession", "decline")
        ]},
        "FileChangeApprovalDecision": {"oneOf": [
            {"enum": [value]} for value in ("accept", "acceptForSession", "decline")
        ]},
        "PermissionGrantScope": {"enum": ["turn", "session"]},
    })
    documents = {
        "ClientRequest.json": requests,
        "ServerRequest.json": server_requests,
        "ServerNotification.json": notifications,
        "codex_app_server_protocol.schemas.json": {"definitions": {
            **definitions,
            "v2": {
                "ThreadStartResponse": {
                    "required": ["thread"],
                    "properties": {"activePermissionProfile": {}, "sandbox": {}},
                },
                "ActivePermissionProfile": {
                    "properties": {"id": {}, "extends": {}}, "required": ["id"],
                },
                "NetworkRequirements": {"properties": {key: {} for key in (
                    "allowLocalBinding", "domains", "enabled", "unixSockets",
                )}},
                "NetworkDomainPermission": {"enum": ["allow", "deny"]},
                "NetworkUnixSocketPermission": {"enum": ["allow", "deny"]},
                "Thread": {"required": ["id"]},
                "TurnStartResponse": {"required": ["turn"]},
                "Turn": {"required": ["id", "status"]},
            },
        }},
    }
    for name, content in documents.items():
        (root / name).write_text(json.dumps(content))
    return documents


def test_schema_gate_accepts_supported_contract(tmp_path: Path):
    _schema_fixture(tmp_path)
    check_protocol_schema(tmp_path)


def test_schema_gate_rejects_changed_approval_response(tmp_path: Path):
    documents = _schema_fixture(tmp_path)
    properties = documents["codex_app_server_protocol.schemas.json"]["definitions"]["PermissionsRequestApprovalResponse"]["properties"]
    properties.pop("strictAutoReview")
    (tmp_path / "codex_app_server_protocol.schemas.json").write_text(
        json.dumps(documents["codex_app_server_protocol.schemas.json"])
    )
    with pytest.raises(CodexCompatibilityError, match="permission approval response"):
        check_protocol_schema(tmp_path)


def test_schema_gate_rejects_missing_server_receipt(tmp_path: Path):
    documents = _schema_fixture(tmp_path)
    documents["ServerNotification.json"]["definitions"]["ServerRequestResolvedNotification"]["required"].remove("requestId")
    (tmp_path / "ServerNotification.json").write_text(json.dumps(documents["ServerNotification.json"]))
    with pytest.raises(CodexCompatibilityError, match="server request receipt"):
        check_protocol_schema(tmp_path)


def test_schema_gate_rejects_changed_item_start_evidence(tmp_path: Path):
    documents = _schema_fixture(tmp_path)
    notification = documents["ServerNotification.json"]
    notification["definitions"]["ItemStartedNotification"]["required"].remove("item")
    (tmp_path / "ServerNotification.json").write_text(json.dumps(notification))
    with pytest.raises(CodexCompatibilityError, match="item start"):
        check_protocol_schema(tmp_path)


def test_schema_gate_rejects_missing_session_identity_in_start_response(tmp_path: Path):
    documents = _schema_fixture(tmp_path)
    bundle = documents["codex_app_server_protocol.schemas.json"]
    bundle["definitions"]["v2"]["Thread"]["required"].remove("id")
    (tmp_path / "codex_app_server_protocol.schemas.json").write_text(json.dumps(bundle))
    with pytest.raises(CodexCompatibilityError, match="thread/start thread"):
        check_protocol_schema(tmp_path)


def test_schema_gate_reports_missing_definition_as_compatibility_error(tmp_path: Path):
    documents = _schema_fixture(tmp_path)
    bundle = documents["codex_app_server_protocol.schemas.json"]
    del bundle["definitions"]["v2"]["TurnStartResponse"]
    (tmp_path / "codex_app_server_protocol.schemas.json").write_text(json.dumps(bundle))
    with pytest.raises(CodexCompatibilityError, match="required protocol definition"):
        check_protocol_schema(tmp_path)


def test_schema_gate_requires_command_item_evidence_for_nullable_approval_command(tmp_path: Path):
    documents = _schema_fixture(tmp_path)
    notification = documents["ServerNotification.json"]
    command_item = notification["definitions"]["ThreadItem"]["oneOf"][0]
    command_item["required"].remove("command")
    (tmp_path / "ServerNotification.json").write_text(json.dumps(notification))
    with pytest.raises(CodexCompatibilityError, match="command item"):
        check_protocol_schema(tmp_path)


def test_schema_gate_requires_file_item_changes(tmp_path: Path):
    documents = _schema_fixture(tmp_path)
    notification = documents["ServerNotification.json"]
    file_item = notification["definitions"]["ThreadItem"]["oneOf"][1]
    file_item["required"].remove("changes")
    (tmp_path / "ServerNotification.json").write_text(json.dumps(notification))
    with pytest.raises(CodexCompatibilityError, match="file item"):
        check_protocol_schema(tmp_path)


def test_missing_and_unsupported_codex_are_actionable(monkeypatch):
    monkeypatch.setattr("codex_coordinator.compatibility.shutil.which", lambda _value: None)
    with pytest.raises(CodexCompatibilityError, match="install Codex CLI"):
        check_codex_compatibility("missing-codex")
    monkeypatch.setattr("codex_coordinator.compatibility.shutil.which", lambda _value: "/bin/codex")
    monkeypatch.setattr(
        "codex_coordinator.compatibility.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "codex-cli 0.153.0\n", ""),
    )
    with pytest.raises(CodexCompatibilityError, match="not verified"):
        check_codex_compatibility()


def test_schema_gate_flags_a_new_approval_policy(tmp_path: Path):
    """A CLI that gains a way to decide what it escalates must be re-examined.

    The coordinator answers in-project file-change approvals itself only
    because no policy here separates them from commands (#0018). A new policy,
    or a new granular category, may remove that need, so it fails the gate
    rather than passing silently into an upgrade.
    """
    documents = _schema_fixture(tmp_path)
    approval = documents["ClientRequest.json"]["definitions"]["AskForApproval"]
    approval["oneOf"][0]["enum"].append("on-file-change")
    (tmp_path / "ClientRequest.json").write_text(json.dumps(documents["ClientRequest.json"]))
    with pytest.raises(CodexCompatibilityError, match="approval policies changed"):
        check_protocol_schema(tmp_path)

    granular_root = tmp_path / "granular"
    granular_root.mkdir()
    documents = _schema_fixture(granular_root)
    approval = documents["ClientRequest.json"]["definitions"]["AskForApproval"]
    approval["oneOf"][1]["properties"]["granular"]["properties"]["file_changes"] = {
        "type": "boolean",
    }
    (granular_root / "ClientRequest.json").write_text(
        json.dumps(documents["ClientRequest.json"])
    )
    with pytest.raises(CodexCompatibilityError, match="granular approval categories changed"):
        check_protocol_schema(granular_root)


def test_schema_gate_flags_a_removed_approval_policy(tmp_path: Path):
    """untrusted is the only wire policy that escalates without asking a worker."""
    documents = _schema_fixture(tmp_path)
    documents["ClientRequest.json"]["definitions"]["AskForApproval"]["oneOf"][0][
        "enum"
    ].remove("untrusted")
    (tmp_path / "ClientRequest.json").write_text(json.dumps(documents["ClientRequest.json"]))
    with pytest.raises(CodexCompatibilityError, match="approval policies changed"):
        check_protocol_schema(tmp_path)


def _rewrite(root: Path, documents: dict, name: str) -> None:
    (root / name).write_text(json.dumps(documents[name]))


def test_schema_gate_flags_a_lost_permissions_field(tmp_path: Path):
    """The boundary this project sends has to stay selectable by profile id.

    Issue #0021: `permissions` is the only field that attaches a thread to the
    profile system, and the alternative is the legacy sandbox literal the
    runtime reports no provenance for. A CLI that drops it fails startup here
    rather than silently returning this project to the shape it migrated off.
    """
    for params in ("ThreadStartParams", "TurnStartParams"):
        root = tmp_path / params
        root.mkdir()
        documents = _schema_fixture(root)
        del documents["ClientRequest.json"]["definitions"][params]["properties"]["permissions"]
        _rewrite(root, documents, "ClientRequest.json")
        with pytest.raises(CodexCompatibilityError, match=f"{params} `permissions` field"):
            check_protocol_schema(root)


def test_schema_gate_flags_a_lost_profile_provenance(tmp_path: Path):
    """Without the readback there is no way to verify the selected boundary."""
    documents = _schema_fixture(tmp_path)
    v2 = documents["codex_app_server_protocol.schemas.json"]["definitions"]["v2"]
    del v2["ThreadStartResponse"]["properties"]["activePermissionProfile"]
    _rewrite(tmp_path, documents, "codex_app_server_protocol.schemas.json")
    with pytest.raises(CodexCompatibilityError, match="activePermissionProfile"):
        check_protocol_schema(tmp_path)

    identity = tmp_path / "identity"
    identity.mkdir()
    documents = _schema_fixture(identity)
    v2 = documents["codex_app_server_protocol.schemas.json"]["definitions"]["v2"]
    v2["ActivePermissionProfile"]["required"] = []
    _rewrite(identity, documents, "codex_app_server_protocol.schemas.json")
    with pytest.raises(CodexCompatibilityError, match="active permission profile"):
        check_protocol_schema(identity)


def test_schema_gate_flags_a_renamed_network_axis(tmp_path: Path):
    """The live gate proves three network axes separately; each must survive.

    A host-scoped ceiling lives in `domains`, the app-server control socket in
    `unixSockets`, and the service's own loopback port in `allowLocalBinding`.
    Collapsing any of them back into one bit is the change #0021 exists to
    notice.
    """
    for axis in ("domains", "unixSockets", "allowLocalBinding"):
        root = tmp_path / axis
        root.mkdir()
        documents = _schema_fixture(root)
        v2 = documents["codex_app_server_protocol.schemas.json"]["definitions"]["v2"]
        del v2["NetworkRequirements"]["properties"][axis]
        _rewrite(root, documents, "codex_app_server_protocol.schemas.json")
        with pytest.raises(CodexCompatibilityError, match="network permission axes changed"):
            check_protocol_schema(root)


def test_schema_gate_flags_a_changed_network_grant_value(tmp_path: Path):
    """A third value would be a grant this project has never reasoned about."""
    documents = _schema_fixture(tmp_path)
    v2 = documents["codex_app_server_protocol.schemas.json"]["definitions"]["v2"]
    v2["NetworkDomainPermission"]["enum"].append("prompt")
    _rewrite(tmp_path, documents, "codex_app_server_protocol.schemas.json")
    with pytest.raises(CodexCompatibilityError, match="network domain permission values changed"):
        check_protocol_schema(tmp_path)
