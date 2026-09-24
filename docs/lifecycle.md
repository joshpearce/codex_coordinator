# Session and event lifecycle

A session is created from a configured project name and records both that stable name and its resolved absolute path. `thread/start` creates the child thread; `turn/start` begins the initial prompt. A completed thread remains available for follow-up turns.

States are `active`, `completed`, `failed`, `interrupted`, `cancelling`, `cancelled`, `cancel_unknown`, `connection_lost`, `protocol_unknown`, and `shutdown_unknown`. Cancellation targets the currently recorded turn. A stale completion cannot finish a newer follow-up turn. Connection loss marks active sessions explicitly rather than implying success.

Supported approval server requests are answered immediately and recorded as `approval.auto_approved`. No pending verdict state exists. Malformed, unsupported, or unmanaged requests produce `approval.protocol_error` and an error response correlated to the original request ID.

Orchestration events have monotonically increasing sequence numbers within one
service generation and bounded count/byte retention. They include child
messages, session transitions, approval/protocol outcomes, and terminal
results. Routine raw notification traffic is excluded. Polling behind the
retained window reports an expired cursor; polling beyond the current
generation reports an ahead cursor. `GET /debug/events` has its own bounded
cursor and exposes redacted managed app-server notifications only for explicit
diagnosis. Default stdout logging emits orchestration metadata rather than full
payloads.

`GET /sessions` is the recovery surface when an orchestration cursor expires.
Each session retains its current/terminal state plus a bounded evidence summary:
the last complete child message, the last automatic approval, and up to 32
managed protocol errors. A 410 event response returns `latestSequence` and a
`recovery` object directing the client to reconcile `/sessions`, then continue
with `after=recovery.resumeAfter`. This avoids aggressive polling; the default
2,048-event/8 MiB orchestration window is independent of raw debug traffic.

Shutdown stops new work, drains in-flight server requests briefly, interrupts active turns, marks uncertain outcomes explicitly, and closes the app-server connection. It never starts, restarts, or stops the host app-server daemon.
