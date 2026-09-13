# Socket and listener exposure is not hardened

## Status

Local-mode checks are implemented: socket type, owner, mode, parent directory,
loopback-only binding, and rejection of browser-origin and non-local Host
headers. Remote or reverse-proxy deployment remains unsupported; it must not
be inferred safe from these local checks.

## Priority

Deferred — local control-plane hardening.

## Problem

Originally, the proof of concept relied on a known app-server Unix socket and
permitted the HTTP listener host to be overridden. Socket ownership and mode
were not verified, and an operator could expose the unauthenticated API on a
non-loopback interface. A reverse proxy can still publish loopback services;
that deployment is unsupported without authentication and transport security.

## Deferral rationale

Protected sockets and network-listener hardening are intentionally postponed until
the orchestration mechanics and governance boundary are stable. The current service
must remain loopback-only and single-user while this issue is deferred.

## Acceptance criteria

- Unix socket ownership, type, and permissions are verified before connection.
- Non-loopback binding requires an explicit secure deployment mode.
- Reverse-proxy guidance requires transport security and authenticated identity.
- Startup refuses unsafe listener configurations by default.
