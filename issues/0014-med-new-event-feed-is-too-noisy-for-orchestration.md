# 0014 — The event feed is too noisy for orchestration clients

**Found:** 2026-09-24, review of parent Codex session `01a0d0d4-63b7-7222-98bb-236e2beff44b`
**Affects:** codex_coordinator, event projection, `src/codex_coordinator/service.py`

## What happens

Every notification for a managed child is emitted as an
`app_server.notification` containing the complete message. Routine item,
command, token, and lifecycle traffic therefore shares the same feed as child
assistant updates, terminal state, and protocol failures.

In the reviewed session one cursor advanced by more than 1,000 events in about
15 seconds. The parent had to repeatedly download and filter raw notification
payloads with custom `jq` expressions. One early unfiltered response was about
43 KB and was truncated by the tool display.

## Why it matters

Bounded service degradation and measurement quality. High-volume implementation
details obscure the small set of events a coordinator needs, increase transfer
and context volume, and make parent prompts depend on app-server notification
internals.

## What would close this

- The service exposes a documented, low-volume orchestration projection that
  includes child messages, session transitions, approvals/protocol errors, and
  terminal results without routine raw item traffic.
- Raw managed notifications remain available only through an explicit debug or
  verbose surface when needed.
- Projection tests cover concurrent sessions, follow-ups, cancellation,
  failures, and preservation of ordering/correlation fields.
- A high-volume child turn demonstrates that the normal orchestration feed stays
  bounded and useful without client-side knowledge of raw notification schemas.
