# 0008 — Socket and listener exposure is not hardened for non-local deployment

**Found:** 2026-09-12, local security review of the app-server socket and HTTP
listener configuration
**Affects:** codex_coordinator, transport setup,
`src/codex_coordinator/service.py`, `src/codex_coordinator/protocol.py`

## What happens

Originally, the proof of concept relied on a known app-server Unix socket and
permitted the HTTP listener host to be overridden. Socket ownership and mode
were not verified, and an operator could expose the unauthenticated API on a
non-loopback interface.

Local-mode checks are now implemented: socket type, owner, mode, parent
directory, loopback-only binding, and rejection of browser-origin and non-local
Host headers.

Remaining: remote or reverse-proxy deployment is unsupported and must not be
inferred safe from these local checks. A reverse proxy can still publish
loopback services; that deployment needs authentication and transport security,
and the remaining criteria below are deliberately deferred while the service
stays a trusted, single-user, loopback-only prototype.

## Why it matters

Scope and isolation, and the precondition for any use beyond one operator on one
machine. The deferral is a scope decision, not a claim that the current
transport is safe when exposed.

## What would close this

- Unix socket ownership, type, and permissions are verified before connection.
- Non-loopback binding requires an explicit secure deployment mode.
- Reverse-proxy guidance requires transport security and authenticated identity.
- Startup refuses unsafe listener configurations by default.
