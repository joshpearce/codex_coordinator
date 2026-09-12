# In-memory event and item state is unbounded

## Priority

Low — resilience.

## Problem

`EventLog.events` and `ApprovalBroker.items` grow for the lifetime of the service.
High-volume token, diff, or item notifications can exhaust memory and make event
polling increasingly expensive.

## Desired behavior

Use bounded state with durable or rotating audit output when history is required.

## Acceptance criteria

- In-memory events and completed item metadata have configurable bounds.
- Event cursors behave predictably after eviction.
- High-volume notification tests demonstrate stable memory use.
- Pending approvals are preserved until resolved or cancelled.
