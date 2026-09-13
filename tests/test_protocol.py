import asyncio
import json

import pytest

from codex_coordinator.protocol import ProtocolClient


class FakeSocket:
    def __init__(self, incoming):
        self.incoming = iter(incoming)
        self.sent = []

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def recv(self):
        return json.dumps(next(self.incoming))


@pytest.mark.asyncio
async def test_call_dispatches_server_request_before_correlated_response():
    socket = FakeSocket([
        {"id": 99, "method": "approval", "params": {"threadId": "t"}},
        {"id": 1, "result": {"ok": True}},
    ])

    async def approve(_request):
        return {"decision": "accept"}

    client = ProtocolClient(socket, approve)
    result = await client.call("thread/read", {"threadId": "t"})

    assert result == {"ok": True}
    assert socket.sent == [
        {"method": "thread/read", "id": 1, "params": {"threadId": "t"}},
        {"id": 99, "result": {"decision": "accept"}},
    ]


@pytest.mark.asyncio
async def test_server_request_limit_fails_closed_without_unbounded_tasks():
    socket = FakeSocket([])
    release = asyncio.Event()
    started = asyncio.Event()

    async def approve(_request):
        started.set()
        await release.wait()
        return {"decision": "decline"}

    client = ProtocolClient(socket, approve, max_concurrent_server_requests=1)
    try:
        await client.dispatch({"id": 1, "method": "approval", "params": {}})
        await started.wait()
        await client.dispatch({"id": 2, "method": "approval", "params": {}})
        assert len(client._request_tasks) == 1
        assert socket.sent == [{
            "id": 2,
            "error": {"code": -32000, "message": "server request limit reached"},
        }]
        release.set()
        await client.drain_requests(timeout=1)
        assert socket.sent[-1] == {"id": 1, "result": {"decision": "decline"}}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_failed_wire_send_does_not_report_response_sent():
    class FailingSocket(FakeSocket):
        async def send(self, _message):
            raise ConnectionError("transport closed")

    called = False

    async def approve(_request):
        return {"decision": "accept"}

    async def sent(_request, _response):
        nonlocal called
        called = True

    client = ProtocolClient(FailingSocket([]), approve, response_sent_handler=sent)
    await client.dispatch({"id": 7, "method": "approval", "params": {}})
    await client.drain_requests(timeout=1)
    assert not called
    await client.close()
