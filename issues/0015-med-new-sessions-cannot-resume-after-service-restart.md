# 0015 — Managed sessions cannot resume after a service restart

**Found:** 2026-09-24, review of parent Codex session `01a0d0d4-63b7-7222-98bb-236e2beff44b`
**Affects:** codex_coordinator, session lifecycle and app-server thread reuse

## What happens

The service keeps its session-to-thread registry only in memory. Restarting the
coordinator loses every session handle even though the host app-server still
retains the child threads. There is no supported route or Python API for
resuming or adopting those known threads.

The reviewed workflow restarted the coordinator four times and consequently
created new `chiron` and `homelab_agent` threads for related follow-up work.
Filesystem changes survived, but each replacement child had to rediscover the
worktree, verification state, constraints, and prior conclusions.

## Why it matters

Continuity and bounded degradation. A transient coordinator restart discards
useful conversational state, increases prompt/context cost, and weakens the
documented expectation that related work stays in one managed session.

## What would close this

- The supported lifecycle either persists enough coordinator metadata to resume
  managed app-server threads or provides a validated adoption mechanism for
  known thread IDs.
- Recovery cannot attach an unmanaged or differently configured thread to a
  project accidentally.
- Recovered sessions preserve follow-ups, terminal waits, cancellation,
  notification correlation, and automatic-approval registration.
- Offline restart/recovery tests and an opt-in live exercise prove continuity
  across a real service restart.
