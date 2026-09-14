# 0012 — The HTTP parser has no resource limits

**Found:** 2026-09-12, local review of the hand-written HTTP handler
**Affects:** codex_coordinator, HTTP listener, `src/codex_coordinator/service.py`

## What happens

Originally, the hand-written HTTP handler had no limits for header size, body
size, read time, concurrent connections, or slow clients. A local client could
hold resources or force large allocations without valid requests.

Implemented in the local parser with header, body, request-line, read-time,
response-write-time, and concurrency limits. Handler slots are released even
when a client stalls response draining or the handler is cancelled. Oversized,
slow-client, and saturation unit tests pass.

Remaining: loopback integration testing has not run outside the restricted
development sandbox.

## Why it matters

Resilience. The listener is the only control-plane entry point, and stalling it
stalls approval resolution for every running worker.

## What would close this

- Header and body sizes are bounded.
- Header/body reads and idle connections have time limits.
- Concurrent requests are capped or back-pressured.
- Oversized and slow-client tests fail cheaply and predictably.
- Loopback integration testing runs outside the restricted sandbox and passes.
