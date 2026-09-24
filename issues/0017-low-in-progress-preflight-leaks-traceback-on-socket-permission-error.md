# 0017 — Preflight leaks a traceback when socket metadata is inaccessible

**Found:** 2026-09-24, review of parent Codex session `01a0d0d4-63b7-7222-98bb-236e2beff44b`
**Affects:** codex_coordinator, `src/codex_coordinator/preflight.py`

## What happens

`preflight.check()` calls `Path.exists()` and `Path.is_symlink()` for the host
app-server socket without handling `OSError`. In the reviewed managed execution
environment, metadata access raised `PermissionError: [Errno 1] Operation not
permitted`, and the installed command printed a full Python traceback through
`make start`.

The failure did indicate the real boundary, but it did not explain that the
parent needed permission to access the existing host socket or present the
error in the same concise form as other preflight failures.

## Why it matters

Operator effort and diagnostic quality. A common managed-environment boundary
looks like an internal crash and makes it harder for the parent to request the
smallest necessary escalation.

## What would close this

- Socket metadata and probe `OSError` cases become concise, actionable preflight
  errors that retain the socket path and underlying reason without a traceback
  in normal CLI output.
- The diagnostic does not suggest starting another app-server when the actual
  failure is access denial.
- Tests cover denied metadata access, denied connection access, a missing
  socket, an invalid socket, and a healthy socket.
