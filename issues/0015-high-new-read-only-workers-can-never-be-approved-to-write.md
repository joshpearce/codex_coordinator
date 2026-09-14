# 0015 — A read-only worker can never be approved to write, so judging deadlocks it

**Found:** 2026-09-14, live E2E run with `sandbox_mode = "read-only"` children,
observed in real judge verdicts
**Affects:** codex_coordinator, judge evidence construction,
`src/codex_coordinator/coordinator.py`

## What happens

`ApprovalPolicy.enforced_capabilities` reports `filesystemWriteRoots` as
`[project]` under `workspace-write` and `[]` under `read-only`, and that value
is handed to the judge as `deterministic_ceiling`. For a `read-only` worker,
every file-change approval therefore arrives with evidence stating that no
filesystem write is permitted anywhere.

A judge applying the constitution's containment principle to that evidence
refuses the write. Both children in a live run were denied on their first patch,
with reasons that are correct given what they were told:

> The patch advances the assigned implementation work, but it writes domain.py
> while the deterministic ceiling permits no filesystem writes.

> The patch advances the assigned work, but the deterministic ceiling permits no
> filesystem writes. Updating report.py is therefore prohibited; provide the
> proposed diff without applying it.

Neither child could make progress; the run deadlocked with every patch denied.

The evidence conflates two different limits. `filesystemWriteRoots` describes
what the turn sandbox permits *without* approval. It does not describe what an
*approved* file change may do, and under `read-only` an approved file change is
the only mechanism by which a legitimate write can happen at all. The real
deterministic guarantee for a file change is separate and already enforced:
`normalize_path` rejects any change path outside the registered project before
a judge ever sees the case.

`read-only` is consequently unusable with service-owned judging, which is the
configuration an operator would pick precisely when they want every write
reviewed rather than presumed.

## Why it matters

Governance integrity and correctness. The strictest available sandbox is the one
that cannot be governed, so an operator who wants more review gets less work and
is pushed toward `workspace-write`, where in-project writes are never judged at
all. A judge is also being handed evidence that misrepresents the boundary,
which is exactly the failure the deterministic ceiling exists to prevent.

## What would close this

- The judge is told what an approved file change may do, separately from what
  the sandbox permits unapproved — for example a distinct
  `approvedFileChangeRoots` derived from the same containment rule
  `normalize_path` already enforces — so containment can be judged by where the
  effect lands rather than by which mechanism delivers it.
- The audit record keeps both limits distinguishable.
- A regression test builds a file-change case for a `read-only` project and
  shows the ceiling naming the project as an approvable write target while
  `filesystemWriteRoots` stays empty.
- A live run with `read-only` children completes, with file changes approved and
  applied. Whether the app-server actually applies an approved patch under a
  `readOnly` turn sandbox is still unverified: the judge denied every patch
  before that path was reached.
