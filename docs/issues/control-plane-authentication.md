# The HTTP control plane has no authentication

## Priority

Deferred — local control-plane hardening.

## Problem

Any client that can reach the listener can create sessions, send prompts, read
events, resolve approvals, and stop the service. Loopback limits remote reachability
but does not protect against other local processes, browser-to-localhost attacks,
or accidental non-loopback binding.

## Deferral rationale

Authentication is intentionally postponed while the proof of concept remains a
single-user, loopback-only design exercise. It becomes mandatory before multi-user,
remote, or untrusted-local-client use.

## Acceptance criteria

- Every state-changing route and sensitive read route requires authentication.
- Credentials are not written to event logs or prompts.
- Browser-origin and replay risks are addressed.
- Authentication failure tests cover every protected route.
