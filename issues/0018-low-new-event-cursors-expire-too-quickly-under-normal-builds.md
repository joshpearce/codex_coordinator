# 0018 — Event cursors expire too quickly under normal child builds

**Found:** 2026-09-24, review of parent Codex session `01a0d0d4-63b7-7222-98bb-236e2beff44b`
**Affects:** codex_coordinator, event retention and cursor recovery
**Depends on:** #0014

## What happens

Event count and byte retention are bounded as intended by #0011, but routine raw
app-server traffic can consume the default 2,048-event window very quickly. In
the reviewed session the cursor advanced by more than 1,000 events in roughly
15 seconds during ordinary child activity. A parent delayed by its own model
turn or tool execution can therefore lose the incremental window even while it
is polling conservatively.

The service reports an expired cursor and the oldest retained sequence, but
recovery requires the parent to reconcile sessions and may permanently lose
child messages or terminal evidence that existed only in evicted events.

## Why it matters

Resilience. Bounded storage should not make normal builds outrun a normal parent
consumer or force aggressive polling that worsens context and service load.

## What would close this

- After the low-volume projection in #0014, retention defaults are sized and
  tested against realistic long-running child builds and parent delays.
- Required orchestration evidence, especially terminal results and protocol
  errors, has a recoverable authoritative surface when an event cursor expires.
- Cursor-expiry responses document an unambiguous recovery procedure without
  encouraging busy polling.
- A high-volume live exercise demonstrates that a conservatively polling parent
  can recover all required orchestration evidence.
