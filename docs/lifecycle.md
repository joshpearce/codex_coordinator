# Local coordinator lifecycle

This contract applies to the single-process, single-user local service and
`Coordinator` API. State is in memory only; restarting the process does not
resume sessions, approvals, or event cursors.

| State or event | Meaning | Next action |
| --- | --- | --- |
| `active` | A turn was requested and has not produced `turn/completed`. | Wait, inspect events, or cancel. |
| `completed` | Codex emitted `turn/completed` with `status: completed`. | A follow-up turn may be sent. |
| `failed` / `interrupted` | Codex reported a failed or interrupted turn, or starting a turn failed. | Inspect the cause; this API does not silently resume it. |
| `cancelling` | `turn/interrupt` was requested; the final notification is still pending. | Wait for `interrupted`, or treat disconnection as unknown. |
| `cancel_unknown` | The explicit interrupt request failed, so cancellation was not confirmed. | Reconcile with the app-server; a later turn completion may still update the state. |
| `protocol_unknown` | A `turn/completed` notification had no valid terminal status. | Treat the outcome as unknown and reconcile; it is never inferred as success. |
| `cancelled` | Shutdown requested an interrupt and the app-server acknowledged it. | Do not assume the worker completed its task. |
| `shutdown_unknown` | Shutdown could not confirm the interrupt. | Check the app-server independently before restarting work. |
| `connection_lost` | The app-server transport closed while work was active. | Reconcile externally; no completion is inferred. |

Approval requests are bound to an immutable session and thread registration.
They are pending until resolved, expired, cancelled, or the transport closes.
Default expiry is 300 seconds and is configurable. Expiry, cancellation,
shutdown, and judge failure deny the request. A late or duplicate verdict is
rejected as an unknown or already resolved approval; a verdict for another
session is rejected before policy evaluation. Every approving verdict is
constrained by the normalized original request and operator ceiling before
the wire response is sent.
Transport loss emits one `service.connection_lost` event even if no session
exists, then marks active sessions `connection_lost` and denies pending
approvals. Closing the broker prevents new approvals or registrations from
entering during shutdown's drain window.
`approval.resolved` records the computed policy response. A separate
`approval.wire_sent` event is emitted only after the app-server WebSocket send
returns; it does not by itself prove the remote worker executed the command.
A command every part of which matches the project's operator-owned execpolicy
rules and stays inside the project is answered `accept` immediately and
recorded as `approval.allowed_by_policy`, with the normalized request, the
rules file's source and digest, and the rule behind each simple command. It
has no `approvalId`, is never pending, and does not count as an approval; a
later `approval.wire_sent` still records the send.
A `workspace-write` file change whose every path normalizes inside the
registered project is answered `accept` the same way and recorded as
`approval.allowed_by_policy` with a `containment` record naming the rule and
every normalized path. A file change the containment rule cannot decide is
answered `decline` without a judge and recorded as
`approval.declined_by_policy`: a `grantRoot`, or any file change under
`read-only`, whose reason names the sandbox mode. Both events carry the same
fields an approval carries, have no `approvalId`, and are never pending. A
path outside the project is refused earlier still, by normalization, and
recorded as `approval.rejected`. An operator rules file may name in-project
paths that escalate anyway; such a request becomes an ordinary pending
approval whose `approval.requested` event carries the escalation under
`containment`.
`approval.server_resolved` records the app-server's receipt or clearance of a
request, while `approval.command_completed` records the correlated command
item's terminal status. The strict live gate checks all three signals.
An item completion removes its cached approval evidence. A turn completion
must identify the active turn; a stale completion emits
`session.stale_turn_completion` without finishing a follow-up. A valid turn
completion denies any still-pending approvals and clears that turn's cached
items. A completion without a valid turn ID yields `protocol_unknown`, never
success.
A judge's `codex exec` subprocess is terminated and reaped if its call times out
or is cancelled; neither case grants an approval.
A control-plane request that fails for an unanticipated reason answers HTTP
`500` with a fixed body carrying no detail, and emits one
`http.internal_error` event naming the request method, path, and exception so
the failure is diagnosable from the operator's own event stream rather than
silently discarded.

The event cursor is the last sequence number already consumed. `/events?after=N`
and `Coordinator.events(after=N)` return later events; numbers never repeat in
one process. HTTP health, session, and event reads carry a per-process
`serviceId`; persisted clients must reset or partition their cursor when that
identifier changes. A cursor ahead of the current generation fails with HTTP
`409 Conflict` rather than appearing idle. `Coordinator.event_cursor` exposes
the latest emitted sequence for catch-up waits. The in-memory event window retains at most 2,048 records and 8
MiB, with a 1 MiB limit per record. A stale cursor yields HTTP `410 Gone` with
`oldestSequence`, or raises `EventCursorExpired` in Python. Oversized approval
evidence is denied instead of being silently reduced for the judge. Item and
notification caches, pending approvals, and sessions have finite count limits.
At most 128 app-server-initiated requests are handled concurrently; excess
requests receive a protocol error instead of accumulating tasks.
The local HTTP control server caps active handlers at 64 and bounds both
request reading and response draining; stalled writers are closed without
retaining handler slots.
Individual item evidence over 64 KiB is not cached; a file approval without
that item evidence fails closed. Cached item evidence is keyed by
thread, turn, and item ID; an approval cannot borrow a prior turn's item when
an ID is reused. File approvals require that matching item's change list;
request-supplied change lists are rejected.

A session is never started in a worker project whose tree carries Codex
execpolicy rules of its own — a `.codex/rules` entry, a `*.rules` file under a
`.codex` directory, or a symlinked `.codex` the scan cannot see through. The
runtime would load those at thread start and decide approvals before the
coordinator saw them. `start_session` refuses before `thread/start`, emits
`session.project_rules_refused` naming the path, and answers HTTP `400`; the
same check runs before every follow-up `turn/start`, so a project that acquires
such a file mid-session gets no further turn. The file's contents are never
read.

`Coordinator.wait(timeout=...)` raises `TimeoutError` if no terminal turn state
arrives. Observation timeout does not cancel the worker or imply success.
`Coordinator.close()` denies pending approvals, attempts to interrupt active
turns, and closes the transport. The service's `/shutdown` does the same.

The local HTTP parser caps request line, header, and body sizes; read time is
limited to 10 seconds and at most 64 requests are handled concurrently. The
service does not offer durable persistence or cross-process resume.
