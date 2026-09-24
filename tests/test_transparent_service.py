import asyncio
import json

import pytest

from codex_coordinator.api import Coordinator
from codex_coordinator.protocol import ProtocolError
from codex_coordinator.config import OperatorConfig
from codex_coordinator.service import AutomaticApprovalHandler, CoordinatorService, EventCursorExpired, EventLog, HttpControlServer, RequestTooLarge


class FakeClient:
    def __init__(self):
        self.calls = []
        self.disconnected = asyncio.Event()
        self.connection_error = None
        self.fail_interrupt = False

    async def call(self, method, params):
        self.calls.append((method, params.copy()))
        if method == "thread/start":
            return {"thread": {
                "id": f"thread-{sum(m == method for m, _ in self.calls)}-{len(self.calls)}",
                "model": params.get("model", "inherited-model"),
                "reasoningEffort": "inherited-effort",
            }}
        if method == "turn/start":
            return {"turn": {"id": f"turn-{len(self.calls)}", "status": "inProgress"}}
        if method == "turn/interrupt" and self.fail_interrupt:
            raise ProtocolError("lost")
        return {}

    async def drain_requests(self, timeout=3):
        return None

    async def close(self):
        self.disconnected.set()


@pytest.fixture
def system(tmp_path):
    alpha = tmp_path / "alpha"; beta = tmp_path / "beta"
    alpha.mkdir(); beta.mkdir()
    events = EventLog(capacity=64, max_bytes=1024 * 1024)
    approvals = AutomaticApprovalHandler(events)
    client = FakeClient()
    service = CoordinatorService(client, approvals, events, projects={"alpha": alpha, "beta": beta})
    return client, approvals, events, service, {"alpha": alpha, "beta": beta}


@pytest.mark.asyncio
async def test_thread_and_turn_wire_are_transparent_and_named(system):
    client, _, _, service, projects = system
    session = await service.start_session("alpha", "do work", "high", "gpt-child")
    assert session["project"] == "alpha"
    assert session["projectPath"] == str(projects["alpha"])
    assert session["effectiveModel"] == "gpt-child"
    assert session["effectiveReasoningEffort"] == "inherited-effort"
    assert client.calls[0] == ("thread/start", {"cwd": str(projects["alpha"]), "model": "gpt-child"})
    assert client.calls[1][0] == "turn/start"
    assert client.calls[1][1] == {
        "threadId": session["threadId"],
        "input": [{"type": "text", "text": "do work"}],
        "effort": "high",
    }
    forbidden = {"approvalPolicy", "approvalsReviewer", "permissions", "sandbox", "sandboxPolicy", "runtimeWorkspaceRoots", "cwd", "turnTrigger"}
    assert not (forbidden & set(client.calls[1][1]))
    with pytest.raises(ValueError, match="unknown project"):
        await service.start_session(str(projects["alpha"]), "path is not a project name")


@pytest.mark.asyncio
async def test_optional_model_and_effort_are_omitted(system):
    client, _, _, service, projects = system
    await service.start_session("beta", "plain")
    assert client.calls[0][1] == {"cwd": str(projects["beta"])}
    assert set(client.calls[1][1]) == {"threadId", "input"}


def request(method, thread_id, **extra):
    return {"id": 7, "method": method, "params": {
        "threadId": thread_id, "turnId": "turn", "itemId": "item", "startedAtMs": 1, **extra,
    }}


@pytest.mark.asyncio
async def test_all_supported_approvals_are_automatic_and_observable(system):
    _, approvals, events, service, _ = system
    session = await service.start_session("alpha", "work")
    thread = session["threadId"]
    command = await approvals(request("item/commandExecution/requestApproval", thread, availableDecisions=["accept", "acceptForSession", "decline"]))
    file_change = await approvals(request("item/fileChange/requestApproval", thread, availableDecisions=["accept", "decline"]))
    permissions = await approvals(request("item/permissions/requestApproval", thread, cwd=".", permissions={"network": {"enabled": True}}))
    assert command == {"decision": "acceptForSession"}
    assert file_change == {"decision": "accept"}
    assert permissions == {"permissions": {"network": {"enabled": True}}, "scope": "session", "strictAutoReview": True}
    approved = [event for event in events.events if event["type"] == "approval.auto_approved"]
    assert len(approved) == 3
    assert all(event["project"] == "alpha" and event["projectPath"] for event in approved)


@pytest.mark.asyncio
async def test_malformed_unknown_and_unmanaged_requests_are_protocol_errors(system):
    _, approvals, events, service, _ = system
    session = await service.start_session("alpha", "work")
    with pytest.raises(ProtocolError, match="unsupported"):
        await approvals(request("unknown/request", session["threadId"]))
    with pytest.raises(ProtocolError, match="permissions payload"):
        await approvals(request("item/permissions/requestApproval", session["threadId"]))
    with pytest.raises(ProtocolError, match="managed thread"):
        await approvals(request("item/fileChange/requestApproval", "stranger"))
    assert len([e for e in events.events if e["type"] == "approval.protocol_error"]) == 3


@pytest.mark.asyncio
async def test_concurrency_follow_up_cancel_wait_and_connection_loss(system):
    client, approvals, events, service, _ = system
    coordinator = Coordinator(service, approvals, events, client)
    alpha, beta = await asyncio.gather(coordinator.start("alpha", "a"), coordinator.start("beta", "b"))
    for handle in (alpha, beta):
        session = service.sessions[handle.id]
        await service.notification({"method": "turn/completed", "params": {
            "threadId": handle.thread_id, "turn": {"id": session.turn_id, "status": "completed"},
        }})
    assert (await alpha.wait(timeout=1)).state == "completed"
    await alpha.follow_up("again", effort="medium")
    await alpha.cancel()
    assert client.calls[-1][0] == "turn/interrupt"
    service.connection_lost("socket closed")
    assert service.sessions[beta.id].state == "completed"
    assert service.sessions[alpha.id].state == "connection_lost"


@pytest.mark.asyncio
async def test_orchestration_projection_is_concise_ordered_and_correlated(system):
    client, approvals, events, service, _ = system
    alpha, beta = await asyncio.gather(
        service.start_session("alpha", "a"), service.start_session("beta", "b")
    )
    baseline = events.next_sequence - 1
    for index in range(1000):
        await service.notification({"method": "item/agentMessage/delta", "params": {
            "threadId": alpha["threadId"], "turnId": alpha["turnId"],
            "delta": f"noise-{index}",
        }})
    await service.notification({"method": "item/completed", "params": {
        "threadId": alpha["threadId"], "turnId": alpha["turnId"],
        "item": {"id": "message-1", "type": "agentMessage", "text": "alpha result"},
    }})
    await service.notification({"method": "turn/completed", "params": {
        "threadId": alpha["threadId"],
        "turn": {"id": alpha["turnId"], "status": "completed"},
    }})
    await service.notification({"method": "turn/completed", "params": {
        "threadId": beta["threadId"],
        "turn": {"id": beta["turnId"], "status": "failed"},
    }})

    projected = events.after(baseline)
    assert [event["type"] for event in projected] == [
        "child.message", "session.completed", "session.failed",
    ]
    assert [event["sequence"] for event in projected] == sorted(
        event["sequence"] for event in projected
    )
    assert projected[0] == {
        "sequence": projected[0]["sequence"], "type": "child.message",
        "sessionId": alpha["id"], "threadId": alpha["threadId"],
        "turnId": alpha["turnId"], "itemId": "message-1", "text": "alpha result",
    }
    assert projected[-1]["session"]["id"] == beta["id"]
    assert len(service.raw_events.events) == 64
    assert all(event["type"] == "app_server.notification" for event in service.raw_events.events)

    status, debug = await HttpControlServer(service).route(
        "GET", f"/debug/events?after={service.raw_events.events[-2]['sequence']}", {}
    )
    assert status == 200
    assert debug["events"][0]["message"]["method"] == "turn/completed"


@pytest.mark.asyncio
async def test_projection_covers_follow_up_cancel_and_protocol_failure(system):
    client, approvals, events, service, _ = system
    session = await service.start_session("alpha", "first")
    await service.notification({"method": "turn/completed", "params": {
        "threadId": session["threadId"],
        "turn": {"id": session["turnId"], "status": "completed"},
    }})
    follow_up = await service.send_message(session["id"], "second")
    await service.cancel_session(session["id"])
    await service.notification({"method": "turn/completed", "params": {
        "threadId": session["threadId"],
        "turn": {"id": follow_up["turnId"], "status": "interrupted"},
    }})
    with pytest.raises(ProtocolError):
        await approvals(request("unsupported/request", session["threadId"]))

    types = [event["type"] for event in events.events]
    assert "session.turn_started" in types
    assert "session.cancelling" in types
    assert "session.interrupted" in types
    assert "approval.protocol_error" in types
    assert "app_server.notification" not in types


@pytest.mark.asyncio
async def test_http_has_no_approval_resolution_route(system):
    _, _, _, service, _ = system
    status, body = await HttpControlServer(service).route("POST", "/approvals/abc", {"verdict": "deny"})
    assert (status, body) == (404, {"error": "not found"})


def test_event_state_remains_bounded():
    events = EventLog(capacity=3, max_bytes=4096)
    for index in range(10):
        events.emit("test", index=index)
    assert len(events.events) == 3
    with pytest.raises(EventCursorExpired):
        events.after(0)


@pytest.mark.asyncio
async def test_high_volume_raw_work_does_not_expire_orchestration_cursor(system):
    _, _, events, service, _ = system
    session = await service.start_session("alpha", "work")
    cursor = events.next_sequence - 1
    for index in range(10_000):
        await service.notification({"method": "item/agentMessage/delta", "params": {
            "threadId": session["threadId"], "turnId": session["turnId"],
            "delta": str(index),
        }})
    await service.notification({"method": "item/completed", "params": {
        "threadId": session["threadId"], "turnId": session["turnId"],
        "item": {"id": "final-message", "type": "agentMessage", "text": "required result"},
    }})
    await service.notification({"method": "turn/completed", "params": {
        "threadId": session["threadId"],
        "turn": {"id": session["turnId"], "status": "completed"},
    }})

    assert [event["type"] for event in events.after(cursor)] == [
        "child.message", "session.completed",
    ]
    recovered = service.sessions[session["id"]].json()
    assert recovered["state"] == "completed"
    assert recovered["evidence"]["lastMessage"]["text"] == "required result"


@pytest.mark.asyncio
async def test_expired_cursor_recovery_preserves_required_session_evidence(system):
    _, approvals, events, service, _ = system
    session = await service.start_session("alpha", "work")
    with pytest.raises(ProtocolError):
        await approvals(request("unsupported/request", session["threadId"]))
    await service.notification({"method": "item/completed", "params": {
        "threadId": session["threadId"], "turnId": session["turnId"],
        "item": {"id": "answer", "type": "agentMessage", "text": "recover me"},
    }})
    await service.notification({"method": "turn/completed", "params": {
        "threadId": session["threadId"],
        "turn": {"id": session["turnId"], "status": "failed"},
    }})
    for index in range(100):
        events.emit("test.eviction", index=index)
    with pytest.raises(EventCursorExpired):
        events.after(0)

    status, body = await HttpControlServer(service).route("GET", "/sessions", {})
    recovered = body["sessions"][0]
    assert status == 200
    assert recovered["state"] == "failed"
    assert recovered["evidence"]["lastMessage"]["text"] == "recover me"
    assert recovered["evidence"]["protocolErrors"][0]["method"] == "unsupported/request"


@pytest.mark.asyncio
async def test_expired_cursor_http_response_has_unambiguous_recovery(system):
    _, _, events, service, _ = system
    for index in range(100):
        events.emit("test.eviction", index=index)

    class Writer:
        def __init__(self):
            self.data = b""
        def write(self, data):
            self.data += data
        async def drain(self):
            return None
        def close(self):
            return None
        async def wait_closed(self):
            return None

    reader = asyncio.StreamReader()
    reader.feed_data(b"GET /events?after=0 HTTP/1.1\r\nHost: localhost\r\n\r\n")
    reader.feed_eof()
    writer = Writer()
    await HttpControlServer(service).handle(reader, writer)
    payload = json.loads(writer.data.split(b"\r\n\r\n", 1)[1])
    assert writer.data.startswith(b"HTTP/1.1 410 Gone")
    assert payload["recovery"] == {
        "sessions": "/sessions", "resumeAfter": payload["latestSequence"],
    }


@pytest.mark.asyncio
async def test_http_body_limit_is_enforced_before_allocation(system):
    _, _, _, service, _ = system
    reader = asyncio.StreamReader()
    reader.feed_data(
        b"POST /sessions HTTP/1.1\r\nHost: localhost\r\nContent-Length: 1048577\r\n\r\n"
    )
    reader.feed_eof()
    with pytest.raises(RequestTooLarge) as caught:
        await HttpControlServer(service)._read_request(reader)
    assert caught.value.status == 413


@pytest.mark.asyncio
async def test_python_api_fails_clearly_when_host_socket_is_missing(monkeypatch, tmp_path):
    project = tmp_path / "project"; project.mkdir()
    config = OperatorConfig.load(overrides={
        "projects": {"p": str(project)},
        "socket_path": str(tmp_path / "missing.sock"),
    }, environ={})
    monkeypatch.setattr("codex_coordinator.api.check_codex_compatibility", lambda _command: "0.154.0")
    with pytest.raises(ConnectionError, match="start the host app-server"):
        await Coordinator.connect(config)
