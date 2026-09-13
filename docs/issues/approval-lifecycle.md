# Approval requests can remain pending forever

## Status

Implemented with configurable expiry, session cancellation, shutdown denial,
late-verdict rejection, and correlated lifecycle events. Unit tests pass;
the real app-server integration gate remains pending.

## Priority

Low — resilience.

## Problem

Originally, approval futures had no timeout. If a coordinator disappeared or
missed an event, a worker request could remain pending for the service
lifetime. Shutdown and session cancellation semantics were undefined.

## Desired behavior

Support configurable approval expiry and deterministic cancellation without placing
a timeout on the overall long-running orchestration goal.

## Acceptance criteria

- Approval expiry is configurable independently of session lifetime.
- Expired, cancelled, and shutdown approvals receive a denial response.
- Late verdicts are rejected clearly.
- Lifecycle events record why an approval ended.
