# 0027 — Parent guidance keeps event monitoring in the main context

**Found:** 2026-09-24, follow-up review of parent Codex session `01a0d0d4-63b7-7222-98bb-236e2beff44b`
**Affects:** codex_coordinator, coordination-workspace example and live parent/child exercise
**Depends on:** #0013

## What happens

The standard coordination-workspace guidance tells the main parent session to
call `GET /events?after=N&wait=30` while children are active. The bounded wait
from #0013 removes shell sleep loops and empty rapid polls, but every timeout or
event still resumes the model with the main session's growing coordination
history. A long-running child or a stream of small progress events can therefore
repeatedly submit a large parent context even though monitoring requires little
reasoning.

The example guidance and its tests do not currently establish a separate,
small-context monitoring subagent or require that only meaningful state changes
return to the main coordinator. The live exercise proves the HTTP wait behavior,
not that the recommended agent structure bounds parent-session token growth.

## Why it matters

This is an operator-effort and measurement-quality gap. Blocking HTTP waits make
monitoring efficient at the service boundary, but the standard agent workflow
can still amplify cached input usage and crowd useful coordination work out of
the parent context.

## What would close this

- The coordination-workspace `AGENTS.md` and documented starter Goal direct the
  parent to delegate routine event waiting and session reconciliation to a
  dedicated monitoring subagent with a small, bounded brief.
- The guidance defines the monitor's handoff contract: retain cursors and
  session IDs, use the bounded blocking endpoint, handle timeout, shutdown,
  cursor recovery, and connection loss, and report only actionable progress,
  terminal state, or a monitoring failure to the main parent.
- The guidance makes the parent retain responsibility for task decisions,
  focused child follow-ups, cancellation, user questions, and final
  verification; the monitor is not a second coordinator or an authorization
  boundary.
- Template tests cover the monitoring guidance so generated coordination
  workspaces do not regress to main-context polling or shell sleep loops.
- A live parent/child exercise records the monitoring structure and token-usage
  evidence showing that routine waits do not repeatedly resume the main parent
  context.
