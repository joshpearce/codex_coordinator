import asyncio
import json
from pathlib import Path

import pytest

from codex_coordinator.protocol import ProtocolClient
from codex_coordinator.service import (
    ApprovalBroker,
    CoordinatorService,
    EventLog,
    HttpControlServer,
)


class QueueSocket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def recv(self):
        return json.dumps(await self.incoming.get())


@pytest.mark.asyncio
async def test_multiplexed_client_receives_response_while_approval_is_pending():
    socket = QueueSocket()
    approval_entered = asyncio.Event()
    release_approval = asyncio.Event()

    async def approval(_message):
        approval_entered.set()
        await release_approval.wait()
        return {"decision": "accept"}

    client = ProtocolClient(socket, approval)
    call = asyncio.create_task(client.call("thread/read", {"threadId": "t"}))
    await asyncio.sleep(0)
    await socket.incoming.put({"id": 99, "method": "approval", "params": {"threadId": "t"}})
    await approval_entered.wait()
    await socket.incoming.put({"id": 1, "result": {"ok": True}})

    assert await asyncio.wait_for(call, 1) == {"ok": True}
    release_approval.set()
    await asyncio.sleep(0)
    await client.close()


async def http_json(port, method, path, body=None):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    payload = json.dumps(body).encode() if body is not None else b""
    writer.write(
        f"{method} {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: {len(payload)}\r\n\r\n".encode()
        + payload
    )
    await writer.drain()
    status = int((await reader.readline()).decode().split()[1])
    headers = {}
    while (line := await reader.readline()) not in {b"\r\n", b"\n", b""}:
        key, value = line.decode().split(":", 1)
        headers[key.lower()] = value.strip()
    result = json.loads((await reader.readexactly(int(headers["content-length"]))).decode())
    writer.close()
    await writer.wait_closed()
    return status, result


@pytest.mark.asyncio
async def test_stdout_approval_event_is_resolved_through_http(capsys):
    events = EventLog()
    broker = ApprovalBroker(events)

    class UnusedClient:
        pass

    service = CoordinatorService(UnusedClient(), broker, events)
    server = await asyncio.start_server(HttpControlServer(service).handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        waiting = asyncio.create_task(broker({
            "id": 7,
            "method": "item/fileChange/requestApproval",
            "params": {"threadId": "worker-1", "changes": [{"path": "app.py"}]},
        }))
        await asyncio.sleep(0)
        approval_id = next(iter(broker.pending))
        status, response = await http_json(port, "POST", f"/approvals/{approval_id}", {
            "verdict": "approve_once",
            "reason": "project-local application file",
        })

    assert status == 200
    assert response == {"decision": "accept"}
    assert await waiting == {"decision": "accept"}
    output_events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [event["type"] for event in output_events] == [
        "approval.requested",
        "approval.resolved",
    ]


@pytest.mark.asyncio
async def test_file_approval_event_includes_correlated_file_changes(capsys):
    events = EventLog()
    broker = ApprovalBroker(events)
    broker.thread_projects["worker-1"] = "/project"
    broker.items[("worker-1", "change-1")] = {
        "id": "change-1",
        "type": "fileChange",
        "changes": [{"path": "/project/app.py", "kind": {"type": "add"}}],
    }
    waiting = asyncio.create_task(broker({
        "id": 8,
        "method": "item/fileChange/requestApproval",
        "params": {"threadId": "worker-1", "itemId": "change-1"},
    }))
    await asyncio.sleep(0)
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["project"] == "/project"
    assert emitted["item"]["changes"][0]["path"] == "/project/app.py"
    approval_id = emitted["approvalId"]
    broker.resolve(approval_id, "deny", "test cleanup")
    assert await waiting == {"decision": "decline"}


@pytest.mark.asyncio
async def test_http_starts_child_session_and_emits_event(tmp_path: Path, capsys):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text(
        'approval_policy = "on-request"\n'
        'approvals_reviewer = "user"\n'
        'sandbox_mode = "read-only"\n'
    )

    class FakeClient:
        async def call(self, method, params):
            if method == "thread/start":
                return {"thread": {"id": "thread-1"}}
            return {"turn": {"id": "turn-1"}}

    events = EventLog()
    broker = ApprovalBroker(events)
    service = CoordinatorService(FakeClient(), broker, events)
    server = await asyncio.start_server(HttpControlServer(service).handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        status, session = await http_json(port, "POST", "/sessions", {
            "project": str(tmp_path),
            "prompt": "Build an app",
        })
        _, event_page = await http_json(port, "GET", "/events?after=0")

    assert status == 201
    assert session["threadId"] == "thread-1"
    assert session["turnId"] == "turn-1"
    assert event_page["events"][0]["type"] == "session.started"
    assert json.loads(capsys.readouterr().out)["type"] == "session.started"
