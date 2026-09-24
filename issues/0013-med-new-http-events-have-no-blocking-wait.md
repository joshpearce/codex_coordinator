# 0013 — HTTP event consumers have no blocking wait

**Found:** 2026-09-24, review of parent Codex session `01a0d0d4-63b7-7222-98bb-236e2beff44b`
**Affects:** codex_coordinator, HTTP control plane, `src/codex_coordinator/service.py`

## What happens

`GET /events?after=N` always returns immediately. A parent coordinating active
children must implement waits itself and issue another HTTP request for every
check. In the reviewed session this produced 42 shell commands containing
sleep-based polling and 19 additional tool waits. Four parent turns accumulated
about 5.88 million input/output tokens, mostly repeated cached input, while the
useful child status updates were small.

`EventLog.wait_after()` already provides an internal notification primitive, but
the HTTP route calls only `EventLog.after()` and does not expose bounded waiting.

## Why it matters

Bounded service degradation and operator effort. Polling consumes parent model
turns, tool calls, and context even when no child state changed. Long-running
builds make the parent both expensive and unnecessarily complicated.

## What would close this

- The HTTP API offers a bounded blocking wait for events or terminal session
  state, with a documented maximum timeout and prompt disconnect cleanup.
- The response distinguishes timeout-with-no-change from new events and from
  cursor/service-generation errors.
- Offline tests cover immediate events, delayed events, timeout, disconnect,
  shutdown, expired cursors, and concurrent waiters.
- A live parent/child exercise demonstrates progress tracking without shell
  sleep loops or one model response per empty poll.
