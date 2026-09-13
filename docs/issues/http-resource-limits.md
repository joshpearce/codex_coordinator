# The HTTP parser has no resource limits

## Status

Implemented in the local parser with header, body, request-line, read-time,
response-write-time, and concurrency limits. Handler slots are now released
even when a client stalls response draining or the handler is cancelled.
Oversized, slow-client, and saturation unit tests
pass; loopback integration testing remains pending outside this sandbox.

## Priority

Low — resilience.

## Problem

Originally, the hand-written HTTP handler had no limits for header size, body
size, read time, concurrent connections, or slow clients. A local client could
hold resources or force large allocations without valid requests.

## Desired behavior

Apply explicit protocol limits or use a maintained HTTP server with equivalent
controls.

## Acceptance criteria

- Header and body sizes are bounded.
- Header/body reads and idle connections have time limits.
- Concurrent requests are capped or back-pressured.
- Oversized and slow-client tests fail cheaply and predictably.
