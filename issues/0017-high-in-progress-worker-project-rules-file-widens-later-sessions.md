# 0017 — A worker-written `.codex/rules` file is loaded by the next session in that project

**Found:** 2026-09-14, live probing of Codex CLI 0.154.0 `app-server --stdio` with an
isolated `CODEX_HOME`, while settling the execpolicy unknowns recorded in #0016
**Affects:** codex_coordinator, worker session startup, `src/codex_coordinator/service.py`
(`CoordinatorService.start_session`), `src/codex_coordinator/coordinator.py`
(`JudgedSessionSupervisor.start`); the loading behavior itself is in the Codex runtime

## What happens

The runtime loads execpolicy rules from two places: `$CODEX_HOME/rules/*.rules` and
`<cwd>/.codex/rules/*.rules`, where `cwd` is the thread's working directory — the
worker project. There is no configuration key that changes this: with
`--strict-config`, the app-server rejects `exec_policy`, `execpolicy`, and `rules`
as unknown fields, and `--ignore-rules` exists for `codex exec` but not for the
app-server. Under `approval_policy = "untrusted"` an `allow` rule that matches every
simple command in a script suppresses the approval request entirely.

Observed on a thread started with `approvalPolicy: untrusted`, `sandbox:
workspace-write`, the project as the only writable root:

| Step | Worker did | Approval raised | Rules loaded (app-server trace log) |
|---|---|---|---|
| Turn 1 | wrote `.codex/rules/worker.rules` with `prefix_rule(pattern=["cat"], decision="allow", …)`, then ran `cat NOTES.md` | yes, for the write, the `mkdir`, and the `cat` | 0 from `$CODEX_HOME/rules`; project directory not yet consulted |
| Turn 2, same thread | ran `cat NOTES.md` | yes | unchanged |
| New thread, same project | ran `cat NOTES.md` | **no** — the command ran without any approval | `loaded 1 .rules files in <project>/.codex/rules` |

Rules are read at thread start, not per turn or per command. A worker therefore
cannot change the policy of its current session, but one in-scope, in-sandbox write
— the kind of write a judge has every reason to approve, since writing project files
is the assignment — removes judging from every later session of that project for
whichever commands the worker chose to allow. This is the shape of #0014 (a worker's
`.codex/config.toml` deciding its own next sandbox) reappearing one layer down, in
the runtime rather than in this codebase.

The coordinator never reads a project's `.codex/rules` for its own deterministic
allow (#0016 built that on operator-owned files beside `operator.toml`), so the
coordinator-side policy is not affected. What is affected is the runtime's own
decision to raise an approval at all, which happens before anything reaches the
coordinator.

### Not yet known

- Whether the same directory is honored when `cwd` is a subdirectory of the project
  or when the project is a git worktree (the probe used a plain directory).
- Whether a `forbidden` rule written by a worker can deny commands the operator
  would have allowed (the inverse direction; a denial-of-service on itself, lower
  consequence).

## Why it matters

Governance integrity: a path by which a worker session widens the policy governing
its own project's later sessions, silently, with no signal in this codebase's
events — a command that used to appear as an approval simply stops appearing. The
`session.started` provenance shows an unchanged operator boundary, which would
read as "nothing changed" while the runtime's effective policy did.

## What would close this

- `start_session` and `JudgedSessionSupervisor.start` refuse to start a thread in a
  project whose tree contains a `.codex/rules` directory (or any `*.rules` file
  under `.codex`), with a distinct event naming the path, so the first widened
  session never starts and the operator learns why. The check is repeated
  immediately before each `turn/start` on an existing thread as defense in depth,
  even though rules were observed to load only at thread start.
- The refusal is tested offline with a planted file, and the live behavior is
  re-verified after the fix: a project with a planted `.codex/rules/*.rules` cannot
  obtain a session, and a project that acquires one mid-session cannot obtain a
  second.
- `docs/security.md` states that the runtime reads project rules and that the
  coordinator refuses such projects, alongside the existing statement that nothing
  inside a worker project is read as configuration by this codebase.
- If a later pinned CLI offers an app-server or per-thread switch to ignore project
  rules, the coordinator sets it and asserts it loaded (see the loaded-vs-ignored
  probe in #0016's record); the refusal stays until that is proven live.
