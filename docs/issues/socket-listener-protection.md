# Socket and listener exposure is not hardened

## Priority

Deferred — local control-plane hardening.

## Problem

The proof of concept relies on a known app-server Unix socket and permits the HTTP
listener host to be overridden. Socket ownership and mode are not verified, and an
operator can expose the unauthenticated API on a non-loopback interface or through a
reverse proxy.

## Deferral rationale

Protected sockets and network-listener hardening are intentionally postponed until
the orchestration mechanics and governance boundary are stable. The current service
must remain loopback-only and single-user while this issue is deferred.

## Acceptance criteria

- Unix socket ownership, type, and permissions are verified before connection.
- Non-loopback binding requires an explicit secure deployment mode.
- Reverse-proxy guidance requires transport security and authenticated identity.
- Startup refuses unsafe listener configurations by default.
