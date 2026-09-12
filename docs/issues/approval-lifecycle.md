# Approval requests can remain pending forever

## Priority

Low — resilience.

## Problem

Approval futures intentionally have no timeout. If a coordinator disappears or
misses an event, the worker request and broker state can remain pending for the
service lifetime. Shutdown and session cancellation semantics are not defined.

## Desired behavior

Support configurable approval expiry and deterministic cancellation without placing
a timeout on the overall long-running orchestration goal.

## Acceptance criteria

- Approval expiry is configurable independently of session lifetime.
- Expired, cancelled, and shutdown approvals receive a denial response.
- Late verdicts are rejected clearly.
- Lifecycle events record why an approval ended.
