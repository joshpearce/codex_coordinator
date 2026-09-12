from pathlib import Path

import pytest

from codex_coordinator.coordinator import (
    ApprovalCase,
    ApprovalPolicy,
    JudgeDecision,
    JudgedApprovalHandler,
    JudgedSessionSupervisor,
    OneShotCodexJudge,
    WorkerPermissions,
)


class StaticJudge:
    def __init__(self, verdict="approve_once"):
        self.verdict = verdict
        self.cases = []

    async def decide(self, case):
        self.cases.append(case)
        return JudgeDecision(self.verdict, "test")


class FakeClient:
    def __init__(self):
        self.calls = []

    async def call(self, method, params):
        self.calls.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "worker-1"}}
        return {}


@pytest.mark.asyncio
async def test_starts_worker_with_validated_local_permissions(tmp_path: Path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/config.toml").write_text(
        'approval_policy = "on-request"\n'
        'approvals_reviewer = "user"\n'
        'sandbox_mode = "read-only"\n'
    )
    client = FakeClient()
    judge = StaticJudge()
    handler = JudgedApprovalHandler(tmp_path, ApprovalPolicy(tmp_path), judge)

    thread_id = await JudgedSessionSupervisor(client, handler, tmp_path).start("Do the work")

    assert thread_id == "worker-1"
    assert "worker-1" in handler.worker_thread_ids
    start = client.calls[0][1]
    assert start["cwd"] == str(tmp_path.resolve())
    assert start["approvalPolicy"] == "on-request"
    assert start["approvalsReviewer"] == "user"
    assert start["sandbox"] == "read-only"


def test_rejects_dangerous_local_worker_permissions(tmp_path: Path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/config.toml").write_text(
        'approval_policy = "on-request"\n'
        'approvals_reviewer = "user"\n'
        'sandbox_mode = "danger-full-access"\n'
    )

    with pytest.raises(ValueError, match="unsafe or unsupported"):
        WorkerPermissions.from_project(tmp_path)


@pytest.mark.asyncio
async def test_unmanaged_thread_is_denied_without_consulting_judge(tmp_path: Path):
    judge = StaticJudge()
    handler = JudgedApprovalHandler(tmp_path, ApprovalPolicy(tmp_path), judge)

    result = await handler({
        "method": "item/commandExecution/requestApproval",
        "params": {"threadId": "stranger", "command": "true"},
    })

    assert result == {"decision": "decline"}
    assert judge.cases == []


@pytest.mark.asyncio
async def test_session_approval_is_reduced_to_one_turn_by_default(tmp_path: Path):
    judge = StaticJudge("approve_session")
    handler = JudgedApprovalHandler(tmp_path, ApprovalPolicy(tmp_path), judge)
    handler.register_worker("worker-1")

    result = await handler({
        "method": "item/commandExecution/requestApproval",
        "params": {
            "threadId": "worker-1",
            "command": "git status",
            "availableDecisions": ["accept", "acceptForSession", "decline"],
        },
    })

    assert result == {"decision": "accept"}


@pytest.mark.asyncio
async def test_permission_ceiling_denies_unknown_keys_before_judging(tmp_path: Path):
    judge = StaticJudge()
    policy = ApprovalPolicy(tmp_path, allowed_permission_keys=frozenset({"network"}))
    handler = JudgedApprovalHandler(tmp_path, policy, judge)
    handler.register_worker("worker-1")

    result = await handler({
        "method": "item/permissions/requestApproval",
        "params": {"threadId": "worker-1", "permissions": {"filesystem": ["/"]}},
    })

    assert result == {"permissions": {}, "scope": "turn", "strictAutoReview": True}
    assert judge.cases == []


@pytest.mark.asyncio
async def test_request_outside_project_is_denied_before_judging(tmp_path: Path):
    judge = StaticJudge()
    handler = JudgedApprovalHandler(tmp_path, ApprovalPolicy(tmp_path), judge)
    handler.register_worker("worker-1")

    result = await handler({
        "method": "item/commandExecution/requestApproval",
        "params": {"threadId": "worker-1", "cwd": "/", "command": "true"},
    })

    assert result == {"decision": "decline"}
    assert judge.cases == []


@pytest.mark.asyncio
async def test_malformed_or_failed_judge_response_fails_closed(tmp_path: Path):
    async def malformed(_prompt):
        return "not json"

    judge = OneShotCodexJudge(malformed)
    decision = await judge.decide(ApprovalCase(
        "item/fileChange/requestApproval", "worker-1", str(tmp_path), {}
    ))

    assert decision.verdict == "deny"


@pytest.mark.asyncio
async def test_known_worker_decision_is_translated_to_protocol(tmp_path: Path):
    judge = StaticJudge("approve_once")
    handler = JudgedApprovalHandler(tmp_path, ApprovalPolicy(tmp_path), judge)
    handler.register_worker("worker-1")

    result = await handler({
        "method": "item/fileChange/requestApproval",
        "params": {"threadId": "worker-1", "changes": [{"path": "safe.txt"}]},
    })

    assert result == {"decision": "accept"}
    assert len(judge.cases) == 1
