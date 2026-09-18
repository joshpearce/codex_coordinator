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
                    "sandbox", "permissions", "model",
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
