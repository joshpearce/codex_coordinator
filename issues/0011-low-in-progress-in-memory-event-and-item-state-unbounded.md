# 0011 — In-memory event and item state is unbounded

**Found:** 2026-09-12, local review of service runtime state
**Affects:** codex_coordinator, event log and approval broker,
`src/codex_coordinator/service.py`, `src/codex_coordinator/coordinator.py`

## What happens

Originally, event and approval-evidence state grew for the lifetime of the
service. High-volume token, diff, or item notifications could exhaust memory and
make event polling increasingly expensive.

Partially addressed. Events have operator-configurable count and byte limits
with defined cursor-gap behavior; item caches have operator-configurable count
and per-item byte limits; protocol notifications retain metadata only. A
10,000-event retention test passes.

Issue #0026 removed the approval evidence cache with the brokered-verdict path.
Remaining: durable or rotating audit output is not implemented, so bounding
event memory currently means discarding history rather than moving it.

## Why it matters

Resilience. A long-running coordination session is the intended use, and
unbounded growth degrades event polling before it fails outright.

## What would close this

- In-memory events and completed item metadata have configurable bounds.
- Event cursors behave predictably after eviction.
- High-volume notification tests demonstrate stable memory use.
- Durable or rotating audit output exists where history is required.
