# Architecture

The coordinator is a thin multiplexer over one connection to the host user's already-running Codex app-server.

`OperatorConfig` maps stable project names to absolute directories and carries only orchestration settings. `CoordinatorService` resolves a requested name, starts a thread with that path as `cwd`, starts turns, correlates notifications, and maintains bounded session/event state. `ProtocolClient` owns the single reader and correlates concurrent calls and server requests. `AutomaticApprovalHandler` recognizes the approval methods supported by the pinned compatibility gate and immediately returns the protocol-defined acceptance response.

Thread startup sends only `cwd` and an optional model override. Turn startup sends only the thread identity, prompt, and an optional reasoning-effort override. The coordinator does not send an approval policy, reviewer, permission profile, sandbox, rules, workspace roots, or other security setting.

The service does not start or manage the app-server and does not set `CODEX_HOME`. Codex therefore resolves host and project configuration as it would for a session started naturally at the configured path.

This design deliberately has no coordinator-owned governance layer. Project registration is authority to start there; effective child capabilities come from Codex configuration outside this package.

A coordinating Codex session is an ordinary client of the loopback HTTP
service. It normally runs in a separate coordination workspace containing the
operator mapping and instructions for issuing HTTP requests. It is not a
privileged coordinator thread, and its directory need not be registered as a
child project. The service cannot push an unsolicited turn into that parent
session. While its turn remains active, the parent can use
`GET /events?after=N&wait=30`, which blocks in the service until events arrive,
the bounded timeout elapses, or shutdown begins. Disconnected HTTP clients
cancel their waiter promptly.

The normal event log is a projection for orchestration: child assistant
messages, session transitions, approval/protocol outcomes, and terminal
results. Routine app-server deltas and item traffic do not enter that cursor or
retention budget. A separately bounded `GET /debug/events` feed retains
redacted full managed notifications for explicit diagnosis; clients must not
depend on that app-server-specific schema for ordinary coordination.
