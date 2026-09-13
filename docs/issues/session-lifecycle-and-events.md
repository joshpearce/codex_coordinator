# Define session, approval, and event lifecycles

## Status

Complete — child of [the generic coordination goal](generic-coordination-goal.md).
Timeout, cancellation, shutdown, connection-loss, event-cursor, and resource
limits are implemented with focused tests. Failed interrupt requests and
malformed completion notifications now produce explicit unknown states rather
than leaving cancellation pending or inferring success. Approval and session
HTTP request/response integration now runs through real asyncio stream pairs.
An approval resolved at the timeout boundary now returns its recorded verdict
instead of emitting a contradictory expiry denial.
Malformed or duplicate app-server thread IDs are rejected without rebinding an
existing session's approval and event correlation.
Oversized app-server identifiers can no longer force an oversized retained
event or default stdout line; a bounded truncation marker replaces them.
An API handle now reports `connection_lost` when a transport failure happened
just before its caller began awaiting the terminal result.
Turn-completion notifications are now checked against the active turn ID;
stale completions cannot finish a follow-up. Completing a turn denies any
remaining approvals and clears its item evidence.
The protocol reader now notifies the reusable API immediately on transport
loss, closing pending approvals with denial even when the host is only
consuming events and has not called another API method.
Closing the approval broker now seals it against late requests and
registrations; shutdown marks the service as stopping before draining, so a
late worker start cannot reopen approval work.
Connection loss also emits one service-level event when no sessions exist, so
an event-only host is not left waiting indefinitely for a per-session update.
The separate loopback-listener and real Codex sandbox tests still require an
environment that permits those operations; they remain parent integration
evidence and are not counted as completed here.

## Problem

Approval futures have no expiry, session cancellation is undefined, and the
event and item collections grow for the service lifetime. A host using this as
a generic coordinator has no reliable contract for disconnection, shutdown,
late verdicts, event-cursor gaps, or cleanup of unfinished work.

## Desired behavior

Specify state transitions for sessions and approvals, including terminal
outcomes, expiry, cancellation, shutdown, and transport loss. Preserve useful
correlated audit events without unbounded in-memory growth. Persistence and
resume may be optional for the initial local release, but their absence must
be explicit and pending work must fail closed.

## Acceptance criteria

- Configurable approval expiry and explicit cancellation/shutdown paths send
  deterministic denial responses when the transport permits; late verdicts
  are rejected with a clear reason.
- Transport loss, judge failure, interrupted turns, and process shutdown have
  documented terminal or recoverable states; no case is reported as completed
  merely because observation stopped.
- Event and item retention are bounded, and cursors have defined behavior
  after eviction; session IDs and event sequence numbers remain correlated.
- HTTP body/header/read/concurrency limits or an equivalent maintained server
  prevent a local client from exhausting resources cheaply.
- Tests exercise expiry, cancellation, shutdown, connection loss, late
  resolution, cursor gaps, and high-volume event handling.

See also [approval lifecycle](approval-lifecycle.md), [unbounded runtime
state](unbounded-runtime-state.md), and [HTTP resource limits](http-resource-limits.md).
