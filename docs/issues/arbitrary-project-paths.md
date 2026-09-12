# Session creation accepts arbitrary project paths

## Priority

Medium — scope and isolation.

## Problem

`POST /sessions` resolves any supplied filesystem path and treats a compatible
`.codex/config.toml` there as a worker project. There is no configured set of
allowed project roots. A caller that can reach the local API can therefore start
Codex in directories outside the intended exercise.

## Desired behavior

The service should accept sessions only under startup-configured project roots,
using canonical paths and rejecting symlink escapes.

## Acceptance criteria

- Startup configuration declares one or more allowed roots.
- Canonical project paths must be descendants of an allowed root.
- Missing projects, symlink escapes, and paths outside the roots are rejected.
- Rejections occur before reading project configuration or starting a thread.
