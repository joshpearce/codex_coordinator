# Session creation accepts arbitrary project paths

## Status

Implemented in the service; non-network tests cover missing projects,
out-of-root paths, and symlink escapes before project configuration is read.
Worker config symlinks that resolve outside the canonical worker project are
also rejected before thread creation.
The real loopback integration gate still needs to run outside this sandbox.

## Priority

Medium — scope and isolation.

## Problem

Originally, `POST /sessions` resolved any supplied filesystem path and treated a
compatible `.codex/config.toml` there as a worker project. It had no
configured set of allowed project roots, so a local caller could start Codex
outside the intended exercise.

## Desired behavior

The service should accept sessions only under startup-configured project roots,
using canonical paths and rejecting symlink escapes.

## Acceptance criteria

- Startup configuration declares one or more allowed roots.
- Canonical project paths must be descendants of an allowed root.
- Missing projects, symlink escapes, and paths outside the roots are rejected.
- Rejections occur before reading project configuration or starting a thread.
