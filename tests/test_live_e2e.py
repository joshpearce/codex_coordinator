import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from codex_coordinator.live_e2e import (
    monitoring_evidence,
    parent_monitor_prompt,
    parent_rollout_evidence,
    validate_monitoring_evidence,
)


def args():
    return Namespace(
        first="app",
        second="docs",
        first_prompt="implement it",
        second_prompt="document it",
    )


def test_parent_monitor_prompt_encodes_guidance_and_tasks():
    prompt = parent_monitor_prompt(args(), Path("/tmp/live/control.sock"))

    assert '"app": "implement it"' in prompt
    assert '"docs": "document it"' in prompt
    assert "--unix-socket /tmp/live/control.sock" in prompt
    assert "URL base `http://localhost`" in prompt
    assert 'sandbox_permissions="require_escalated"' in prompt
    assert "exactly one POST /sessions/batch request" in prompt
    assert "serviceId, eventCursor" in prompt
    assert "project-scoped `coordinator_monitor` custom subagent" in prompt
    assert "task name `coordinator_monitor`" in prompt
    assert "GET /events?after=N&wait=30" in prompt
    assert "continue silently on timeout" in prompt
    assert "never use shell\nsleeps or busy polling" in prompt
    assert "recovery.resumeAfter" in prompt
    assert "Do not personally call GET /events" in prompt
    assert "are not main-context event waits" in prompt
    assert "call `wait_agent` exactly once" in prompt
    assert "must not send empty, timeout, or non-actionable progress messages" in prompt
    assert '"mainContextWaitCalls": 0' in prompt


def test_monitoring_evidence_is_extracted_and_validated():
    text = "done\n```json\n" + '''{
      "monitoringEvidence": {
        "monitorSpawned": true,
        "boundedBrief": true,
        "serviceId": "service-1",
        "sessionIds": {"app": "s1", "docs": "s2"},
        "lastCursor": 9,
        "timeoutCount": 2,
        "recoveryCount": 1,
        "terminalSessions": [
          {"id": "s1", "state": "completed", "evidence": {"lastMessage": {}}},
          {"id": "s2", "state": "completed", "evidence": {"lastMessage": {}}}
        ],
        "reports": [
          {"kind": "terminal", "sessionId": "s1", "state": "completed"},
          {"kind": "terminal", "sessionId": "s2", "state": "completed"}
        ],
        "mainContextWaitCalls": 0,
        "shellSleepCalls": 0
      }
    }''' + "\n```"
    evidence = monitoring_evidence(text)
    sessions = {
        "s1": SimpleNamespace(project="app", state="completed", json=lambda: {
            "id": "s1", "state": "completed", "evidence": {"lastMessage": {}},
        }),
        "s2": SimpleNamespace(project="docs", state="completed", json=lambda: {
            "id": "s2", "state": "completed", "evidence": {"lastMessage": {}},
        }),
    }

    validate_monitoring_evidence(evidence, sessions, {"app", "docs"})


def test_monitoring_evidence_rejects_main_context_waiting():
    evidence = {
        "monitorSpawned": True,
        "boundedBrief": True,
        "serviceId": "service-1",
        "sessionIds": {"app": "s1", "docs": "s2"},
        "lastCursor": 9,
        "timeoutCount": 0,
        "recoveryCount": 0,
        "terminalSessions": [],
        "reports": [],
        "mainContextWaitCalls": 1,
        "shellSleepCalls": 0,
    }

    with pytest.raises(RuntimeError, match="main-context"):
        validate_monitoring_evidence(evidence, {}, {"app", "docs"})


def test_parent_rollout_requires_one_wait_and_one_monitor_handoff(monkeypatch, tmp_path):
    rollout = tmp_path / ".codex" / "sessions" / "2026" / "rollout-thread-1.jsonl"
    rollout.parent.mkdir(parents=True)
    records = [
        {"type": "response_item", "payload": {
            "name": "spawn_agent",
            "arguments": json.dumps({
                "task_name": "coordinator_monitor",
                "agent_type": "coordinator_monitor",
            }),
        }},
        {"type": "response_item", "payload": {"name": "wait_agent"}},
        {"type": "response_item", "payload": {
            "type": "agent_message", "author": "/root/batch_monitor",
            "recipient": "/root",
            "content": [{"text": "Message Type: FINAL_ANSWER\nPayload:\nterminal"}],
        }},
        {"type": "token_usage_record", "payload": {
            "thread_id": "thread-1",
            "turn_token_usage": {
                "input_tokens": 123, "cached_input_tokens": 100, "total_tokens": 130,
            },
        }},
    ]
    rollout.write_text("\n".join(json.dumps(record) for record in records))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    evidence = parent_rollout_evidence("thread-1")

    assert evidence["waitAgentCalls"] == 1
    assert evidence["coordinatorMonitorSpawns"] == 1
    assert evidence["monitorNotifications"] == 1
    assert evidence["parentModelInvocations"] == 1
    assert evidence["cachedInputTokens"] == 100
    assert evidence["uncachedInputTokens"] == 23
    assert evidence["directParentEventCalls"] == 0
    assert evidence["turnTokenUsage"]["input_tokens"] == 123
