# 0014 — A worker can rewrite the permission configuration that governs it

**Found:** 2026-09-14, live E2E work on prompt-independent approvals, code review
of `WorkerPermissions.from_project` against the enforced sandbox
**Affects:** codex_coordinator, worker permission validation,
`src/codex_coordinator/coordinator.py`, `src/codex_coordinator/service.py`

## What happens

`WorkerPermissions.from_project` reads `approval_policy`, `approvals_reviewer`,
and `sandbox_mode` from `<project>/.codex/config.toml`, and
`enforced_sandbox` derives the turn sandbox from `sandbox_mode`. Under
`workspace-write` the writable root is the project itself, and `.codex/config.toml`
is inside it. The file that decides a worker's permission boundary is therefore
writable by that worker.

The current session is unaffected: registration snapshots the values and every
`turn/start` replays the captured sandbox policy, so a mid-session edit changes
nothing. The next `start_session` for that project re-reads the file.

The approval policy is no longer taken from that file when the operator sets
one. `worker_approval_policy` in `operator.toml` is sent as the `thread/start`
`approvalPolicy` parameter and overrides the project's declared value, so how
much reaches a judge is operator-owned. `sandbox_mode` is still read from the
worker's file, and that is the remaining exposure.

Validation bounds the damage but does not remove it. `approval_policy` must be
`on-request` and `approvals_reviewer` must be `user`, so neither can be
weakened. `sandbox_mode` accepts both `read-only` and `workspace-write`, so a
read-only worker can promote itself to workspace-write for its next session by
editing one line — an in-scope, in-sandbox write that a judge has every reason
to approve, since writing project files is exactly what the worker was asked to
do.

A read-only worker is the case that matters: it is chosen precisely when the
operator does not want the worker writing. It cannot edit the file under its
own read-only sandbox, so the escalation needs one approved write in a prior
workspace-write session, or a second worker sharing the root.

This is a code review finding, not an observed exploit.

## Why it matters

Governance integrity, and it survives a restart. The permission configuration is
what decides which actions escalate into judged approvals; a worker that can
edit it can widen its own boundary for every later session, and each step looks
like ordinary in-project work.

## What would close this

- The effective `sandbox_mode` for a project comes from operator-owned
  configuration outside every worker-writable root, or the worker's
  `.codex/config.toml` is required to be non-worker-writable, or a change to it
  between sessions is detected and fails closed. The approval policy already
  works this way via `worker_approval_policy`; `sandbox_mode` does not.
- A registration records the digest of the permission configuration it was
  derived from, so an audit can show a between-session change.
- A regression test registers a project, rewrites `.codex/config.toml` to a
  wider `sandbox_mode`, and shows the next `start_session` refusing or retaining
  the original boundary.
- `docs/security.md` states whether worker-owned permission configuration is
  supported, since the current text presents it as the mechanism that decides
  what gets judged.
