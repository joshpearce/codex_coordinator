# 0004 — A worker-writable root may contain the coordinator that judges it

**Found:** 2026-09-13, multi-capability workspace assessment, code review of
`config.py` root disjointness checks
**Affects:** codex_coordinator, startup configuration validation,
`src/codex_coordinator/config.py`

## What happens

Nothing requires a worker-writable allowed root to exclude the coordinator's own
installed package, its virtualenv, or the resolved `codex_command`. The only
disjointness `config.py` enforces is that `operator.toml` and `constitution.md`
sit outside both worker roots and `coordinator_root` (`config.py:196-238`).
`coordinator_root` itself is not required to be disjoint from `allowed_roots`.

A self-modifying worker is plausibly this repository. With `workspace-write`
over it, the worker can edit `src/codex_coordinator/coordinator.py`: the judge
prompt, `constrain`, the ceiling, `_encode`. The running service keeps the code
it loaded, so nothing breaks in the current session; the next service start runs
worker-authored judging code. Every step is inside the ceiling and the judge is
right to approve each one. An injected instruction in a design document or
dependency README that the worker reads is enough to start it.

The same reasoning applies to `coordinator_root` overlapping an allowed root:
the coordinating session's `AGENTS.md` becomes worker-writable, which lets one
worker rewrite the instructions the coordinator follows for all workers.

## Why it matters

Governance integrity, and the only finding here that survives a restart. It
converts a single approved in-scope edit into permanent control of the approval
path itself.

## What would close this

- Startup rejects an `allowed_roots` entry that contains or equals the
  coordinator's installed package directory, its interpreter prefix, the
  resolved `codex_command`, or `coordinator_root`.
- The rejection is tested for each of those four paths.
- `docs/security.md` states that self-modifying workers must not have the
  coordinator's own runtime inside their writable root.
- Demonstrating the exploit requires a deliberate self-modification run against
  a scratch copy of the tree, not this checkout.
