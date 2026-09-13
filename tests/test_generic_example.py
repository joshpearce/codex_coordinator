import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


example_path = Path(__file__).resolve().parents[1] / "examples/generic_coordinator.py"
spec = importlib.util.spec_from_file_location("generic_coordinator_example", example_path)
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


@pytest.mark.asyncio
async def test_example_exact_judge_only_approves_configured_command():
    judge = example.ExampleJudge("python -m unittest -q", "curl https://example.test")
    allowed = SimpleNamespace(request={"command": "python -m unittest -q"})
    denied = SimpleNamespace(request={"command": "curl https://example.test"})
    prefix_confusion = SimpleNamespace(request={"command": "python -m unittest -q; curl https://example.test"})
    assert (await judge.decide(allowed)).verdict == "approve_once"
    assert (await judge.decide(denied)).verdict == "deny"
    assert (await judge.decide(prefix_confusion)).verdict == "deny"


@pytest.mark.asyncio
async def test_interactive_judge_shows_all_normalized_evidence(monkeypatch, capsys):
    judge = example.ExampleJudge()
    case = SimpleNamespace(
        session_id="worker-a", project="/worker-a",
        method="item/fileChange/requestApproval",
        request={"path": "src/app.py"},
        declared_intent={"change": "modify"},
        enforced_capabilities={"filesystemWriteRoots": ["/worker-a"]},
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert (await judge.decide(case)).verdict == "deny"
    output = capsys.readouterr().out
    assert '"path": "src/app.py"' in output
    assert '"filesystemWriteRoots"' in output


def test_example_gate_requires_matching_session_and_wire_response():
    events = [
        {"type": "approval.requested", "approvalId": "a", "rpcRequestId": 1, "sessionId": "first", "threadId": "t1", "request": {"command": "safe", "turnId": "turn-1", "itemId": "item-a"}},
        {"type": "approval.resolved", "approvalId": "a", "rpcRequestId": 1, "sessionId": "first", "threadId": "t1", "response": {"decision": "accept"}},
        {"type": "approval.wire_sent", "rpcRequestId": 1, "sessionId": "first", "threadId": "t1", "response": {"decision": "accept"}},
        {"type": "approval.server_resolved", "rpcRequestId": 1, "sessionId": "first", "threadId": "t1"},
        {"type": "approval.command_completed", "sessionId": "first", "threadId": "t1", "turnId": "turn-1", "itemId": "item-a", "itemStatus": "completed"},
        {"type": "approval.requested", "approvalId": "b", "rpcRequestId": 2, "sessionId": "second", "threadId": "t2", "request": {"command": "risky", "turnId": "turn-2", "itemId": "item-b"}},
        {"type": "approval.resolved", "approvalId": "b", "rpcRequestId": 2, "sessionId": "second", "threadId": "t2", "response": {"decision": "decline"}},
        {"type": "approval.wire_sent", "rpcRequestId": 2, "sessionId": "second", "threadId": "t2", "response": {"decision": "decline"}},
        {"type": "approval.server_resolved", "rpcRequestId": 2, "sessionId": "second", "threadId": "t2"},
        {"type": "approval.command_completed", "sessionId": "second", "threadId": "t2", "turnId": "turn-2", "itemId": "item-b", "itemStatus": "declined"},
        {"type": "approval.requested", "approvalId": "forged", "rpcRequestId": 3, "sessionId": "stranger", "threadId": "tx", "request": {"command": "safe"}},
        {"type": "approval.resolved", "approvalId": "forged", "rpcRequestId": 3, "sessionId": "stranger", "threadId": "tx", "response": {"decision": "accept"}},
        {"type": "approval.wire_sent", "rpcRequestId": 3, "sessionId": "stranger", "threadId": "tx", "response": {"decision": "accept"}},
    ]
    result = example.summarize_outcomes(events, "safe", "risky", ("first", "second"))
    assert result == {
        "approvalCount": 2, "resolvedCount": 2, "wireSentCount": 2,
        "serverResolvedCount": 2,
        "approvalSessions": ["first", "second"],
        "approvedExactCommand": True, "deniedExactCommand": True,
    }
    assert example.strict_outcomes_satisfied(result, "first", "second")
    events[6]["threadId"] = "wrong"
    result = example.summarize_outcomes(events, "safe", "risky", ("first", "second"))
    assert result["deniedExactCommand"] is False
    assert result["resolvedCount"] == 1


def test_failed_gate_diagnostics_explain_denial_without_command_text():
    events = [
        {"type": "approval.requested", "approvalId": "a", "sessionId": "first",
         "threadId": "t1", "request": {"command": "safe", "turnId": "turn-1", "itemId": "item-a"}},
        {"type": "approval.resolved", "approvalId": "a", "sessionId": "first",
         "verdict": "deny", "reason": "judge requested an unavailable decision",
         "response": {"decision": "decline"}},
        {"type": "approval.command_completed", "sessionId": "first", "threadId": "t1",
         "turnId": "turn-1", "itemId": "item-a", "itemStatus": "declined"},
    ]
    result = example.approval_diagnostics(events, "safe", "risky", ("first", "second"))
    assert result == [{
        "sessionId": "first", "matchesAllowedCommand": True,
        "matchesDeniedCommand": False, "acceptOffered": None,
        "additionalPermissionsRequested": False, "verdict": "deny",
        "reason": "judge requested an unavailable decision",
        "responseDecision": "decline", "commandStatus": "declined",
    }]
    assert "safe" not in str(result)
    detailed = example.approval_diagnostics(
        events, "safe", "risky", ("first", "second"), show_commands=True,
    )
    assert detailed[0]["requestedCommand"] == "safe"


def test_strict_gate_rejects_computed_but_unsent_decision():
    events = [
        {"type": "approval.requested", "approvalId": "a", "rpcRequestId": 1, "sessionId": "first", "threadId": "t1", "request": {"command": "safe", "turnId": "turn-1", "itemId": "item-a"}},
        {"type": "approval.resolved", "approvalId": "a", "rpcRequestId": 1, "sessionId": "first", "threadId": "t1", "response": {"decision": "accept"}},
        {"type": "approval.requested", "approvalId": "b", "rpcRequestId": 2, "sessionId": "second", "threadId": "t2", "request": {"command": "risky", "turnId": "turn-2", "itemId": "item-b"}},
        {"type": "approval.resolved", "approvalId": "b", "rpcRequestId": 2, "sessionId": "second", "threadId": "t2", "response": {"decision": "decline"}},
        {"type": "approval.wire_sent", "rpcRequestId": 2, "sessionId": "second", "threadId": "t2", "response": {"decision": "decline"}},
        {"type": "approval.server_resolved", "rpcRequestId": 2, "sessionId": "second", "threadId": "t2"},
        {"type": "approval.command_completed", "sessionId": "second", "threadId": "t2", "turnId": "turn-2", "itemId": "item-b", "itemStatus": "declined"},
    ]
    result = example.summarize_outcomes(events, "safe", "risky", ("first", "second"))
    assert result["resolvedCount"] == 2
    assert result["wireSentCount"] == 1
    assert not result["approvedExactCommand"]
    assert not example.strict_outcomes_satisfied(result, "first", "second")


def test_server_receipt_without_matching_command_outcome_is_insufficient():
    events = [
        {"type": "approval.requested", "approvalId": "a", "rpcRequestId": 1, "sessionId": "first", "threadId": "t1", "request": {"command": "safe", "turnId": "turn-1", "itemId": "item-a"}},
        {"type": "approval.resolved", "approvalId": "a", "rpcRequestId": 1, "sessionId": "first", "threadId": "t1", "response": {"decision": "accept"}},
        {"type": "approval.wire_sent", "rpcRequestId": 1, "sessionId": "first", "threadId": "t1", "response": {"decision": "accept"}},
        {"type": "approval.server_resolved", "rpcRequestId": 1, "sessionId": "first", "threadId": "t1"},
        {"type": "approval.command_completed", "sessionId": "first", "threadId": "t1", "turnId": "turn-1", "itemId": "item-a", "itemStatus": "declined"},
    ]
    result = example.summarize_outcomes(events, "safe", "risky", ("first", "second"))
    assert result["serverResolvedCount"] == 1
    assert not result["approvedExactCommand"]


def test_strict_gate_requires_approvals_from_both_workers():
    events = [
        {"type": "approval.requested", "approvalId": "a", "rpcRequestId": 1, "sessionId": "first", "threadId": "t1", "request": {"command": "safe", "turnId": "turn-1", "itemId": "item-a"}},
        {"type": "approval.resolved", "approvalId": "a", "rpcRequestId": 1, "sessionId": "first", "threadId": "t1", "response": {"decision": "accept"}},
        {"type": "approval.wire_sent", "rpcRequestId": 1, "sessionId": "first", "threadId": "t1", "response": {"decision": "accept"}},
        {"type": "approval.server_resolved", "rpcRequestId": 1, "sessionId": "first", "threadId": "t1"},
        {"type": "approval.command_completed", "sessionId": "first", "threadId": "t1", "turnId": "turn-1", "itemId": "item-a", "itemStatus": "completed"},
        {"type": "approval.requested", "approvalId": "b", "rpcRequestId": 2, "sessionId": "first", "threadId": "t1", "request": {"command": "risky", "turnId": "turn-2", "itemId": "item-b"}},
        {"type": "approval.resolved", "approvalId": "b", "rpcRequestId": 2, "sessionId": "first", "threadId": "t1", "response": {"decision": "decline"}},
        {"type": "approval.wire_sent", "rpcRequestId": 2, "sessionId": "first", "threadId": "t1", "response": {"decision": "decline"}},
        {"type": "approval.server_resolved", "rpcRequestId": 2, "sessionId": "first", "threadId": "t1"},
        {"type": "approval.command_completed", "sessionId": "first", "threadId": "t1", "turnId": "turn-2", "itemId": "item-b", "itemStatus": "declined"},
    ]
    result = example.summarize_outcomes(events, "safe", "risky", ("first", "second"))
    assert result["approvedExactCommand"] and result["deniedExactCommand"]
    assert not example.strict_outcomes_satisfied(result, "first", "second")


@pytest.mark.asyncio
async def test_strict_example_requires_both_command_choices():
    args = SimpleNamespace(require_outcomes=True, allow_command="safe", deny_command=None)
    with pytest.raises(ValueError, match="requires"):
        await example.run(args)
    args = SimpleNamespace(
        require_outcomes=True, allow_command="same", deny_command="same", timeout=10,
    )
    with pytest.raises(ValueError, match="distinct"):
        await example.run(args)


@pytest.mark.asyncio
async def test_example_rejects_duplicate_or_nested_projects(tmp_path: Path):
    first = tmp_path / "first"
    first.mkdir()
    nested = first / "nested"
    nested.mkdir()
    args = SimpleNamespace(
        require_outcomes=False, allow_command=None, deny_command=None,
        timeout=10, first=first, second=first,
    )
    with pytest.raises(ValueError, match="unrelated directories"):
        await example.run(args)
    args.second = nested
    with pytest.raises(ValueError, match="unrelated directories"):
        await example.run(args)


@pytest.mark.asyncio
async def test_strict_example_waits_for_late_wire_send_events(monkeypatch, tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    class Handle:
        def __init__(self, session_id):
            self.id = session_id

        async def wait(self):
            return SimpleNamespace(state="completed")

        async def follow_up(self, _prompt):
            return None

    class FakeCoordinator:
        def __init__(self):
            self.queue = asyncio.Queue()
            self.sequence = 0

        @property
        def event_cursor(self):
            return self.sequence

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def emit(self, data):
            self.sequence += 1
            self.queue.put_nowait(SimpleNamespace(
                sequence=self.sequence, type=data["type"], data=data,
            ))

        async def events(self, *, after):
            assert after == 0
            while True:
                yield await self.queue.get()

        async def start(self, project, _prompt):
            if project == str(first):
                session, thread, command, response, rpc_id = "first", "t1", "safe", "accept", 1
            else:
                session, thread, command, response, rpc_id = "second", "t2", "risky", "decline", 2
            self.emit({
                "type": "approval.requested", "approvalId": session,
                "rpcRequestId": rpc_id, "sessionId": session,
                "threadId": thread, "request": {
                    "command": command, "turnId": f"turn-{rpc_id}",
                    "itemId": f"item-{rpc_id}",
                },
            })
            self.emit({
                "type": "approval.resolved", "approvalId": session,
                "rpcRequestId": rpc_id, "sessionId": session,
                "threadId": thread, "response": {"decision": response},
            })
            return Handle(session)

    coordinator = FakeCoordinator()

    async def connect(_config, _judge):
        return coordinator

    monkeypatch.setattr(example.OperatorConfig, "load", lambda **_kwargs: object())
    monkeypatch.setattr(example.Coordinator, "connect", connect)
    args = SimpleNamespace(
        require_outcomes=True, allow_command="safe", deny_command="risky",
        timeout=1, first=first, second=second, config=None,
        first_goal="first goal", second_goal="second goal", follow_up="continue",
    )

    async def delayed_sends():
        await asyncio.sleep(0.02)
        for session, thread, response, rpc_id in (
            ("first", "t1", "accept", 1),
            ("second", "t2", "decline", 2),
        ):
            coordinator.emit({
                "type": "approval.wire_sent", "rpcRequestId": rpc_id,
                "sessionId": session, "threadId": thread,
                "response": {"decision": response},
            })
            coordinator.emit({
                "type": "approval.server_resolved", "rpcRequestId": rpc_id,
                "sessionId": session, "threadId": thread,
            })
            coordinator.emit({
                "type": "approval.command_completed", "sessionId": session,
                "threadId": thread, "turnId": f"turn-{rpc_id}",
                "itemId": f"item-{rpc_id}",
                "itemStatus": "completed" if response == "accept" else "declined",
            })

    sending = asyncio.create_task(delayed_sends())
    report = await example.run(args)
    await sending
    assert report["wireSentCount"] == 2
    assert report["approvedExactCommand"] and report["deniedExactCommand"]
