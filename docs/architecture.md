# Architecture

The repository contains two entry points built on one app-server transport.

## One-shot judged worker

This prototype separates three responsibilities:

1. `ProtocolClient` owns the app-server connection and JSON-RPC framing.
2. `JudgedSessionSupervisor` reads and validates the permission fields in the
   project's `.codex/config.toml`, then sends those exact values on
   `thread/start`. This prevents a shared daemon from leaking broader startup
   permissions into a worker while keeping the project file authoritative.
3. `JudgedApprovalHandler` authenticates the worker thread, applies a
   deterministic permission ceiling, asks a `Judge` for a narrow verdict, and
   converts that verdict to the app-server response shape.

After `start()`, `JudgedSessionSupervisor.monitor()` keeps the owning client
attached and draining server requests until the worker leaves the active state
or the configured watch window expires. The caller owns WebSocket connection
and reconnection lifecycle.

`OneShotCodexJudge` accepts an injected runner. The included
`codex_exec_json_runner` is an optional adapter and is never invoked
implicitly. A deployment can instead inject a judge backed by a second
app-server connection and a dedicated Codex thread.

## Long-running orchestration service

`codex-coordinator-service` owns one multiplexed `ProtocolClient`. Its background
reader correlates RPC responses while approval handlers remain pending, so HTTP
requests can start and inspect other sessions over the same connection. Server
requests become `approval.requested` JSONL events and remain pending until an
outside actor posts a verdict.

The service binds to loopback by default and has no authentication, durable state,
or approval timeout. It is an orchestration mechanism, not a production security
boundary. The live E2E places policy in the coordinating session and its independent
one-shot judges.

Judges should still use separate `codex exec` processes (or a second app-server
connection), so their work cannot introduce nested approval dependencies on the
worker connection.

## Safety boundary

The LLM judge is advisory. Deterministic code always:

- rejects requests from unregistered thread IDs;
- rejects unknown approval methods;
- rejects working directories and file-change grant roots outside the project;
- rejects permission categories outside an explicit allowlist;
- downgrades session-wide approval unless explicitly enabled;
- denies malformed or unavailable judge responses;
- requires `on-request`, the `user` approval reviewer, and either a read-only
  or workspace-write sandbox in the project-local configuration;
- sends those validated values explicitly when creating the worker.

The approval request is serialized inside a JSON object and labeled untrusted,
which reduces prompt-injection risk but does not eliminate it. Consequently,
the LLM cannot be the final permission boundary.

## Validation status

The tests under `tests/` use fake clients and local loopback HTTP. They cover policy
validation, response translation, fail-closed parsing, concurrent RPC multiplexing,
event emission, verdict submission, and child-session creation. They do not start a
live Codex process or consume model usage.
