# Judged worker-session prototype

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

## Why the judge should not share the worker connection

The current `ProtocolClient` has one reader and awaits a server-request handler
before reading more messages. Starting a judge turn through that same client
would deadlock: the judge RPC response cannot be read until the approval
handler returns. Use either:

- a one-shot `codex exec` judge with read-only sandboxing and approvals disabled;
- a persistent judge thread on a second app-server connection; or
- a future multiplexed transport with a background reader and independent
  request futures.

The first option is the smallest safe experiment. A third coordinating LLM is
not required for transport. Add one only if it has a distinct planning role;
it must not bypass the deterministic policy ceiling.

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

`tests/test_judged_sessions.py` contains isolated tests using fake clients and
judges. They cover local-config preservation, managed-thread isolation,
permission ceilings, session-scope downgrading, response translation, and
fail-closed parsing. No live Codex process, socket, external service, or copied
third-party package is required by these tests.
