# 0023 — Worker loopback requirements expose the control plane

**Found:** 2026-09-17, live SharpSast worker acceptance-test diagnosis
**Affects:** codex_coordinator, HTTP control-plane transport and local permission profiles
**Depends on:** #0008, #0009

## What happens

The SharpSast worker must bind and connect to loopback sockets for VSTest
testhost communication. Under Codex CLI 0.154.0, enabling
`allow_local_binding` made that in-process TCP exchange succeed, but the same
worker sandbox could then reach the Coordinator's unauthenticated listener on
`127.0.0.1:8765`.

Leaving loopback disabled blocks the required test runner. Enabling it while
the control plane remains on TCP gives the governed worker a path to create
sessions, send prompts, read governance events, and stop the service that
governs it.

An exact Unix-socket allowlist probe established a narrower local transport:
the worker was denied an operator-owned Unix socket while a coordinating
profile explicitly allowed that same socket.

The same live diagnosis found a second IPC shape: multiprocess MSBuild creates
dynamic `/tmp/MSBuild<PID>` Unix sockets even when its diagnostic and cache
paths use a project-local `TMPDIR`. Codex CLI 0.154.0 permits an AF_UNIX root,
but does not permit a wildcard filesystem write for those dynamic filenames.
The minimum working profile therefore retains writable `/tmp` and grants the
`/tmp` Unix-socket root. Such a widening must be explicitly acknowledged as
`:slash_tmp` in that project's operator-owned `writable_temp_roots`; omission
continues to fail startup, and preflight plus session provenance report the
exception.

## Why it matters

This is a governance-integrity conflict. A project cannot be given a runtime
capability required by its acceptance suite if the same capability lets it
reach and influence its own coordinator.

## What would close this

- The service supports an operator-owned Unix control socket with no TCP
  control listener in this deployment.
- The coordinating profile explicitly allows only that socket, while the
  worker profile does not.
- Worker loopback bind/connect works and the worker remains unable to connect
  to either coordinator-owned Unix socket.
- Offline transport tests pass, followed by a live existing-test invocation
  under the managed worker profile before the issue is closed.
