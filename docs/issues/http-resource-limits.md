# The HTTP parser has no resource limits

## Priority

Low — resilience.

## Problem

The hand-written HTTP handler has no limits for header size, body size, read time,
concurrent connections, or slow clients. A local client can hold resources or force
large allocations even without valid requests.

## Desired behavior

Apply explicit protocol limits or use a maintained HTTP server with equivalent
controls.

## Acceptance criteria

- Header and body sizes are bounded.
- Header/body reads and idle connections have time limits.
- Concurrent requests are capped or back-pressured.
- Oversized and slow-client tests fail cheaply and predictably.
