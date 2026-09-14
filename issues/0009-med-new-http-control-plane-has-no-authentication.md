# 0009 — The HTTP control plane has no authentication

**Found:** 2026-09-12, local security review of the HTTP service routes
**Affects:** codex_coordinator, HTTP control plane,
`src/codex_coordinator/service.py`
**Depends on:** #0008

## What happens

Any client that can reach the listener can create sessions, send prompts, read
events, resolve approvals, and stop the service. Loopback limits remote
reachability but does not protect against other local processes,
browser-to-localhost attacks, or accidental non-loopback binding.

Authentication is intentionally postponed while the proof of concept remains a
single-user, loopback-only design exercise. It becomes mandatory before
multi-user, remote, or untrusted-local-client use. Mitigations in place today
are the loopback binding and browser-origin/Host rejection described in #0008.

## Why it matters

Scope and isolation, and the gating control for the whole deployment model. An
unauthenticated approval-resolution route is a governance bypass the moment the
listener is reachable by anything other than the operator.

## What would close this

- Every state-changing route and sensitive read route requires authentication.
- Credentials are not written to event logs or prompts.
- Browser-origin and replay risks are addressed.
- Authentication failure tests cover every protected route.
