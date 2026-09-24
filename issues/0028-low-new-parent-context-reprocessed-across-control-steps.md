# 0028 — Parent context is reprocessed across mechanical control steps

**Found:** 2026-09-24, live coordination parent thread `01a0d375-0768-79f2-a299-40eaaf3ba817`, by inspecting its rollout and per-response token-usage records
**Affects:** coordination workspace workflow, monitor handoff contract, live E2E measurement

## What happens

The project-scoped monitor keeps routine event waits out of the parent, but the
parent still resumes for each mechanical control-plane step around a batch.
The observed one-child run recorded 16 parent token-usage samples and 362,712
cumulative parent input tokens, of which 315,136 were cached, while producing
2,824 output tokens. The child session's own model usage is separate; the parent
total primarily reflects repeatedly processing its instructions and accumulated
context around health checks, startup, reconciliation, session creation,
monitor recovery, and final evidence retrieval.

That run also required a replacement monitor after the first monitor could not
reach the loopback service. Commit `fbf7c8f` adds a tracked monitor definition
with per-command escalation and retry guidance, so that specific diagnostic
path is historical evidence rather than the whole issue. Even the later healthy
two-child monitor E2E recorded 201,603 parent input tokens, including 169,216
cached tokens. Raw cumulative input is not equivalent to uncached cost, but the
number of parent activations still affects latency, quota accounting, and the
amount of context repeatedly processed.

## Why it matters

The coordinator is meant to let a high-context parent retain decisions while a
small monitor handles waiting. Repeatedly waking the parent for deterministic
transport work weakens that benefit and makes cumulative-token totals difficult
to interpret. It also encourages ad hoc shell sequences whose verbose output
becomes durable parent context even when only a service identity, cursor,
session map, or terminal evidence is needed.

## What would close this

- Define and test a compact coordination path that combines safe deterministic
  preparation work where practical, without adding an installed command beyond
  `service.py` and `preflight.py` or moving task decisions into the monitor.
- A newly created session provides enough service identity, session identity,
  and cursor information to start monitoring without an avoidable reconciliation
  turn.
- A terminal monitor handoff carries the authoritative bounded session evidence
  needed for the parent to decide whether a further service read is necessary.
- Transient monitor transport recovery does not resume the parent when the
  documented recovery can complete safely; true identity changes or uncertain
  state still fail visibly.
- The coordination workspace avoids duplicating stable operating instructions
  across always-loaded parent context, Goal text, and monitor briefs while
  preserving routing, authority, and recovery requirements.
- The rollout evidence reports parent model-invocation count, cached and
  uncached input, monitor notifications, direct parent event calls, and
  recovery-related resumptions instead of relying on cumulative tokens alone.
- A live run with the pinned Codex CLI demonstrates the improved healthy path
  with no lost session continuity, raw-event filtering, sleep polling, hidden
  approval behavior, or transfer of task decisions to the monitor, and records
  a materially smaller parent activation/uncached-input baseline.
