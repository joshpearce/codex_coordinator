# In-memory event and item state is unbounded

## Status

Partially addressed. Events have operator-configurable count and byte limits
with cursor-gap behavior; item caches have operator-configurable count and
per-item byte limits. Protocol notifications retain metadata only. Durable or
rotating audit output is not implemented. A 10,000-event retention test passes.

## Priority

Low — resilience.

## Problem

Originally, `EventLog.events` and `ApprovalBroker.items` grew for the lifetime
of the service. High-volume token, diff, or item notifications could exhaust
memory and make event polling increasingly expensive.

## Desired behavior

Use bounded state with durable or rotating audit output when history is required.

## Acceptance criteria

- In-memory events and completed item metadata have configurable bounds.
- Event cursors behave predictably after eviction.
- High-volume notification tests demonstrate stable memory use.
- Pending approvals are preserved until resolved or cancelled.
