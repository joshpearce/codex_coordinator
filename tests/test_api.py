import asyncio
from pathlib import Path

import pytest

from codex_coordinator import ApprovalRequest, Coordinator, CoordinationEvent, JudgeDecision, OperatorConfig
from codex_coordinator.service import ApprovalBroker, CoordinatorService, EventCursorExpired, EventLog, Session
from codex_coordinator.coordinator import ApprovalPolicy, SessionRegistration
from codex_coordinator.protocol import ProtocolClient


class FakeClient:
    def __init__(self):
        self.calls = []
        self.disconnected = asyncio.Event()
        self.connection_error = None

    async def call(self, method, params):
        self.calls.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": f"thread-{len([c for c in self.calls if c[0] == 'thread/start'])}"}}
        if method == "turn/start":
            return {"turn": {"id": f"turn-{len([c for c in self.calls if c[0] == 'turn/start'])}"}}
        return {}

    async def close(self):
        self.disconnected.set()


def project(root: Path, name: str) -> Path:
    """A worker project directory; its boundary is declared by the operator."""
    path = root / name
    path.mkdir(parents=True)
    return path


def request(thread_id: str, command: str) -> dict:
    return {
        "method": ApprovalPolicy.COMMAND,
        "params": {
            "threadId": thread_id, "turnId": "turn-1", "itemId": "item-1",
            "startedAtMs": 1, "command": command, "cwd": ".",
            "availableDecisions": ["accept", "decline"],
        },
    }


@pytest.mark.asyncio
async def test_api_concurrent_sessions_judge_follow_up_and_terminal(tmp_path: Path):
    first = project(tmp_path, "first")
    second = project(tmp_path, "second")

    class Judge:
        async def decide(self, case):
            return JudgeDecision(
                "approve_once" if "safe" in case.request["command"] else "deny",
                "test decision",
            )

    events = EventLog()
    broker = ApprovalBroker(events, judge=Judge())
    client = FakeClient()
    service = CoordinatorService(client, broker, events, allowed_roots=(tmp_path,))
    coordinator = Coordinator(service, broker, events, client)
    handles = await asyncio.gather(
        coordinator.start(str(first), "first goal"),
        coordinator.start(str(second), "second goal"),
    )
    assert len({handle.id for handle in handles}) == 2
    assert {handle.project for handle in handles} == {str(first), str(second)}
    assert coordinator.event_cursor == events.next_sequence - 1

    decisions = await asyncio.gather(
        broker(request(handles[0].thread_id, "safe command")),
        broker(request(handles[1].thread_id, "risky operation")),
    )
    assert decisions == [{"decision": "accept"}, {"decision": "decline"}]
    approvals = [event for event in events.events if event["type"] == "approval.requested"]
    assert {event["sessionId"] for event in approvals} == {handle.id for handle in handles}

    for handle in handles:
        await service.notification({
            "method": "turn/completed", "params": {
                "threadId": handle.thread_id, "turn": {"id": service.sessions[handle.id].turn_id, "status": "completed"},
            },
        })
    assert (await handles[0].wait(timeout=1)).state == "completed"
    await handles[0].follow_up("another task")
    assert client.calls[-1][0] == "turn/start"
    assert client.calls[-1][1]["threadId"] == handles[0].thread_id
    await service.notification({
        "method": "turn/completed", "params": {
            "threadId": handles[0].thread_id, "turn": {"id": service.sessions[handles[0].id].turn_id, "status": "completed"},
        },
    })
    assert (await handles[0].wait(timeout=1)).state == "completed"
    await coordinator.close()


@pytest.mark.asyncio
async def test_api_judge_failure_denies_and_close_rejects_operations(tmp_path: Path):
    worker = project(tmp_path, "worker")

    class BrokenJudge:
        async def decide(self, _case):
            raise RuntimeError("private judge details")

    events = EventLog()
    broker = ApprovalBroker(events, judge=BrokenJudge())
    client = FakeClient()
    service = CoordinatorService(client, broker, events, allowed_roots=(tmp_path,))
    coordinator = Coordinator(service, broker, events, client)
    handle = await coordinator.start(str(worker), "goal")
    assert await broker(request(handle.thread_id, "operation")) == {"decision": "decline"}
    resolved = next(event for event in events.events if event["type"] == "approval.resolved")
    assert resolved["reason"] == "judge failed"
    await coordinator.close()
    assert service.sessions[handle.id].state == "cancelled"
    with pytest.raises(RuntimeError, match="closed"):
        await coordinator.start(str(worker), "another")


@pytest.mark.asyncio
async def test_api_rejects_cross_session_resolution_and_correlates_events(tmp_path: Path):
    first = project(tmp_path, "first")
    second = project(tmp_path, "second")
    events = EventLog()
    broker = ApprovalBroker(events)
    client = FakeClient()
    service = CoordinatorService(client, broker, events, allowed_roots=(tmp_path,))
    coordinator = Coordinator(service, broker, events, client)
    alpha = await coordinator.start(str(first), "alpha")
    beta = await coordinator.start(str(second), "beta")
    waiting = asyncio.create_task(broker(request(alpha.thread_id, "risky operation")))
    await asyncio.sleep(0)
    approval_id = next(iter(broker.pending))
    with pytest.raises(ValueError, match="does not belong"):
        coordinator.resolve_approval(approval_id, beta.id, "approve_once", "wrong worker")
    assert not waiting.done()
    coordinator.resolve_approval(approval_id, alpha.id, "deny", "correct worker")
    assert await waiting == {"decision": "decline"}
    stream = coordinator.events(after=0)
    observed = [await anext(stream) for _ in range(3)]
    assert [event.session_id for event in observed] == [alpha.id, beta.id, alpha.id]
    observed[2].data["request"]["command"] = "tampered event copy"
    original = next(event for event in events.events if event["type"] == "approval.requested")
    assert original["request"]["command"] == "risky operation"
    await stream.aclose()
    await coordinator.close()


@pytest.mark.asyncio
async def test_api_manual_approval_uses_typed_session_bound_request(tmp_path: Path):
    worker = project(tmp_path, "worker")
    events = EventLog()
    broker = ApprovalBroker(events)
    client = FakeClient()
    service = CoordinatorService(client, broker, events, allowed_roots=(tmp_path,))
    coordinator = Coordinator(service, broker, events, client)
    handle = await coordinator.start(str(worker), "goal")
    cursor = coordinator.event_cursor
    waiting = asyncio.create_task(broker(request(handle.thread_id, "review me")))
    stream = coordinator.events(after=cursor)
    event = await asyncio.wait_for(anext(stream), 1)
    approval = event.approval
    assert isinstance(approval, ApprovalRequest)
    assert approval.session_id == handle.id
    assert approval.thread_id == handle.thread_id
    assert approval.request["command"] == "review me"
    approval.request["command"] = "mutated caller copy"
    recorded = next(item for item in events.events if item["type"] == "approval.requested")
    assert recorded["request"]["command"] == "review me"
    assert coordinator.resolve_approval_request(
        approval, JudgeDecision("deny", "manual review denied"),
    ) == {"decision": "decline"}
    assert await waiting == {"decision": "decline"}
    await stream.aclose()
    await coordinator.close()


def test_api_rejects_incomplete_typed_approval_event():
    truncated = CoordinationEvent.from_record({
        "sequence": 1, "type": "approval.requested", "truncated": True,
        "sessionId": "one",
    })
    assert truncated.approval is None
    with pytest.raises(ValueError, match="not a complete approval"):
        ApprovalRequest.from_event(truncated)


@pytest.mark.asyncio
async def test_api_reports_unavailable_app_server_connection(monkeypatch, tmp_path: Path):
    config = OperatorConfig.load(environ={}, overrides={
        "allowed_roots": [str(tmp_path)],
        "socket_path": str(tmp_path / "stale.sock"),
    })
    monkeypatch.setattr("codex_coordinator.api.check_codex_compatibility", lambda _command: "0.154.0")
    monkeypatch.setattr("codex_coordinator.api.validate_local_socket", lambda _path: None)

    async def unavailable(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr("codex_coordinator.api.websockets.unix_connect", unavailable)
    with pytest.raises(ConnectionError, match="codex-coordinator-preflight"):
        await Coordinator.connect(config, start_daemon=False)


@pytest.mark.asyncio
async def test_api_wait_reports_unknown_protocol_outcome(tmp_path: Path):
    worker = project(tmp_path, "worker")
    events = EventLog()
    broker = ApprovalBroker(events)
    client = FakeClient()
    service = CoordinatorService(client, broker, events, allowed_roots=(tmp_path,))
    coordinator = Coordinator(service, broker, events, client)
    handle = await coordinator.start(str(worker), "goal")
    await service.notification({
        "method": "turn/completed", "params": {
            "threadId": handle.thread_id, "turn": {"id": service.sessions[handle.id].turn_id, "status": "inProgress"},
        },
    })
    assert (await handle.wait(timeout=1)).state == "protocol_unknown"
    await coordinator.close()


@pytest.mark.asyncio
async def test_api_wait_returns_connection_loss_when_transport_is_already_down(tmp_path: Path):
    worker = project(tmp_path, "worker")
    events = EventLog()
    broker = ApprovalBroker(events)
    client = FakeClient()
    service = CoordinatorService(client, broker, events, allowed_roots=(tmp_path,))
    coordinator = Coordinator(service, broker, events, client)
    handle = await coordinator.start(str(worker), "goal")
    client.connection_error = OSError("transport closed")
    client.disconnected.set()

    result = await handle.wait(timeout=1)
    assert result.state == "connection_lost"
    assert result.session_id == handle.id
    assert any(event["type"] == "session.connection_lost" for event in events.events)
    await coordinator.close()


@pytest.mark.asyncio
async def test_api_transport_loss_immediately_denies_pending_approval(tmp_path: Path):
    worker = project(tmp_path, "worker")

    class ClosingSocket:
        def __init__(self):
            self.closing = asyncio.Event()

        async def recv(self):
            await self.closing.wait()
            raise ConnectionError("transport closed")

        async def send(self, _payload):
            return None

    socket = ClosingSocket()
    events = EventLog()
    broker = ApprovalBroker(events, approval_timeout_seconds=60)
    client = ProtocolClient(socket, broker)
    service = CoordinatorService(client, broker, events, allowed_roots=(tmp_path,))
    coordinator = Coordinator(service, broker, events, client)
    service.sessions["session-1"] = Session(
        "session-1", "thread-1", str(worker), turn_id="turn-1",
    )
    service.thread_sessions["thread-1"] = "session-1"
    broker.register(SessionRegistration(
        "session-1", "thread-1", str(worker), ApprovalPolicy(worker),
    ))
    pending = asyncio.create_task(broker(request("thread-1", "review me")))
    await asyncio.sleep(0)
    assert broker.pending

    await client.start()
    socket.closing.set()
    await asyncio.wait_for(client.disconnected.wait(), 1)
    assert await asyncio.wait_for(pending, 1) == {"decision": "decline"}
    assert service.sessions["session-1"].state == "connection_lost"
    assert any(event["type"] == "approval.cancelled" for event in events.events)
    await coordinator.close()


@pytest.mark.asyncio
async def test_api_constructor_reconciles_disconnect_before_callback_install(tmp_path: Path):
    events = EventLog()
    broker = ApprovalBroker(events)
    client = FakeClient()
    service = CoordinatorService(client, broker, events, allowed_roots=(tmp_path,))
    service.sessions["session-1"] = Session("session-1", "thread-1", str(tmp_path))
    client.disconnected.set()

    coordinator = Coordinator(service, broker, events, client)
    assert service.sessions["session-1"].state == "connection_lost"
    await coordinator.close()


@pytest.mark.asyncio
async def test_api_connection_loss_event_without_sessions_is_idempotent(tmp_path: Path):
    events = EventLog()
    broker = ApprovalBroker(events)
    client = FakeClient()
    service = CoordinatorService(client, broker, events, allowed_roots=(tmp_path,))
    coordinator = Coordinator(service, broker, events, client)
    stream = coordinator.events(after=coordinator.event_cursor)

    client.disconnect_handler("socket closed")
    observed = await asyncio.wait_for(anext(stream), 1)
    assert observed.type == "service.connection_lost"
    assert observed.data["reason"] == "socket closed"
    client.disconnect_handler("duplicate callback")
    assert [event["type"] for event in events.events] == ["service.connection_lost"]
    await stream.aclose()
    await coordinator.close()
    client.disconnect_handler("intentional shutdown")
    assert [event["type"] for event in events.events].count("service.connection_lost") == 1


@pytest.mark.asyncio
async def test_api_handle_cancel_waits_for_interrupted_turn(tmp_path: Path):
    worker = project(tmp_path, "worker")
    events = EventLog()
    broker = ApprovalBroker(events)
    client = FakeClient()
    service = CoordinatorService(client, broker, events, allowed_roots=(tmp_path,))
    coordinator = Coordinator(service, broker, events, client)
    handle = await coordinator.start(str(worker), "goal")
    await handle.cancel()
    assert service.sessions[handle.id].state == "cancelling"
    assert client.calls[-1] == (
        "turn/interrupt", {"threadId": handle.thread_id, "turnId": "turn-1"},
    )
    await service.notification({
        "method": "turn/completed", "params": {
            "threadId": handle.thread_id, "turn": {"id": service.sessions[handle.id].turn_id, "status": "interrupted"},
        },
    })
    assert (await handle.wait(timeout=1)).state == "interrupted"
    await coordinator.close()


@pytest.mark.asyncio
async def test_api_event_cursor_reports_eviction():
    events = EventLog(capacity=1)
    broker = ApprovalBroker(events)
    client = FakeClient()
    coordinator = Coordinator(CoordinatorService(client, broker, events), broker, events, client)
    events.emit("first")
    events.emit("second")
    stream = coordinator.events(after=0)
    with pytest.raises(EventCursorExpired):
        await anext(stream)
    await stream.aclose()
    await coordinator.close()
