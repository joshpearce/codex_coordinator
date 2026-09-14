# 0005 — Session creation accepts arbitrary project paths

**Found:** 2026-09-12, local security review of the HTTP service routes
**Affects:** codex_coordinator, session creation, `src/codex_coordinator/service.py`

## What happens

Originally, `POST /sessions` resolved any supplied filesystem path and treated a
compatible `.codex/config.toml` there as a worker project. It had no configured
set of allowed project roots, so a local caller could start Codex outside the
intended exercise.

Allowed-root enforcement is now implemented in the service. Non-network tests
cover missing projects, out-of-root paths, and symlink escapes before project
configuration is read. Worker config symlinks that resolve outside the canonical
worker project are also rejected before thread creation.

Remaining: the real loopback integration gate has not run outside the restricted
development sandbox, so the enforcement is verified only by offline tests.

## Why it matters

Scope and isolation. Without it, a local caller chooses which trees Codex is
started against, which is the boundary every per-project ceiling and policy is
keyed to.

## What would close this

- Startup configuration declares one or more allowed roots.
- Canonical project paths must be descendants of an allowed root.
- Missing projects, symlink escapes, and paths outside the roots are rejected.
- Rejections occur before reading project configuration or starting a thread.
- The loopback integration gate runs outside the restricted development sandbox
  and passes.
