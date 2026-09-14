# 0013 — Approval requests can remain pending forever

**Found:** 2026-09-12, local review of approval broker lifetimes
**Affects:** codex_coordinator, approval broker,
`src/codex_coordinator/coordinator.py`, `src/codex_coordinator/service.py`

## What happens

Originally, approval futures had no timeout. If a coordinator disappeared or
missed an event, a worker request could remain pending for the service lifetime.
Shutdown and session cancellation semantics were undefined.

Implemented with configurable expiry, session cancellation, shutdown denial,
late-verdict rejection, and correlated lifecycle events. Unit tests pass.

Remaining: the real app-server integration gate has not run.

## Why it matters

Resilience. A stuck approval blocks a worker turn indefinitely, and undefined
shutdown semantics are the kind of gap that gets resolved by granting rather
than denying.

## What would close this

- Approval expiry is configurable independently of session lifetime, without
  placing a timeout on the overall long-running orchestration goal.
- Expired, cancelled, and shutdown approvals receive a denial response.
- Late verdicts are rejected clearly.
- Lifecycle events record why an approval ended.
- The real app-server integration gate runs and passes.
