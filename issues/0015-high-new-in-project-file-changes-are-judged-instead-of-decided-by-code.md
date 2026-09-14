# 0015 — In-project file changes are judged instead of decided by code; read-only workers deadlock

**Found:** 2026-09-14, live E2E runs: first with `sandbox_mode = "read-only"`
children, where every patch was denied; then with `workspace-write` children
under `untrusted`, where 13 of 21 judged approvals were in-project file changes
and 8 of those were the same docstring cleanup denied repeatedly
**Affects:** codex_coordinator, approval routing and judge evidence,
`src/codex_coordinator/coordinator.py` (`ApprovalPolicy`),
`src/codex_coordinator/service.py` (`ApprovalBroker`)
**Depends on:** #0016 (closed; the coordinator-side deterministic allow this extends)

## The intended model

The E2E demo is meant to be modeled on normal development: an agent session
reads, writes, and creates files inside its own project directory by right,
the way a developer does in a checkout. That authority is already granted and
enforced twice over — the turn sandbox makes the project the only writable
root, and `normalize_path` rejects any change path outside it before a judge
ever sees a case. Judging exists for what *leaves* the project: reads of other
projects or operator files, network reach, new dependencies, package or
environment changes, and commands whose effect cannot be seen. #0016 made that
split real for commands. It is not yet real for file changes.

## What happens

Under `untrusted`, every `item/fileChange/requestApproval` reaches a judge,
including a patch that edits the one module the worker was assigned. The judge
is shown the diff with no task context (#0002) and applies necessity to it. In
the second networked run this produced 13 judged file changes, 8 of them one
inventory-app patch that only removed the scaffold's stale `TODO` paragraphs,
denied each time as "no implementation, documentation change excluded". The
run still succeeded, but a third of its judge calls decided writes the sandbox
had already confined, and the only wrong verdicts of the run were among them.

With `read-only` children the failure is total. `ApprovalPolicy.enforced_capabilities`
reports `filesystemWriteRoots` as `[]`, that value is handed to the judge as
the deterministic ceiling, and a judge applying containment correctly refuses
every write:

> The patch advances the assigned implementation work, but it writes domain.py
> while the deterministic ceiling permits no filesystem writes.

Neither child could make progress; every patch was denied. The evidence
conflates what the sandbox permits unapproved with what an approved change may
do, and under `read-only` an approved file change is the only way a write could
happen at all.

## Why it matters

Governance quality and correctness. Judging in-project writes spends the
judging budget where the deterministic layer already decides the outcome, adds
worker-authored diffs to the untrusted-evidence surface for no decision, and
produces the run's false denials. It also inverts the demo's premise: a worker
that must ask permission to edit its own module is not modeling how an agent
works in a project. Meanwhile the strictest sandbox is the one that cannot be
governed at all, and the judge is handed evidence that misstates the boundary,
which is the failure the deterministic ceiling exists to prevent.

## What would close this

- Under `workspace-write`, a file-change approval whose every change path
  normalizes inside the registered project is accepted by code, before any
  judge, as a single-turn `accept`. It is recorded like `approval.allowed_by_policy`
  — same normalized evidence, no `approvalId`, no pending approval, no model
  call — with the containment rule that decided it. Nothing about the sandbox
  widens: writes still land only in the project and the network stays off.
- What still reaches a judge for a file change is anything the containment rule
  cannot decide: a path outside the project (already rejected by
  `normalize_path`, so the answer is a deterministic decline, not a judgment),
  a change list that cannot be correlated to its item, or a `grantRoot` that
  asks for anything. An operator who wants some paths inside the project
  reviewed anyway (supplied tests, packaging) can name them in the project's
  execpolicy rules file as paths that always escalate; that is the only case
  in which an in-project write is judged, and it is opt-in.
- Under `read-only`, a file change is declined deterministically with a distinct
  reason and event: the worker has no write authority and the evidence given to
  a judge must never say otherwise. `read-only` is the inspection-only mode;
  authoring work is configured as `workspace-write`. The misleading
  `filesystemWriteRoots: []` ceiling is therefore never shown to a judge for a
  file change at all.
- Regression tests: an in-project multi-file patch is accepted with no judge
  call and the event carries every normalized path; a patch touching a path
  outside the project is declined without a judge; a `read-only` project's
  patch is declined without a judge and the reason names the sandbox mode; an
  operator-named always-escalate path still reaches the judge.
- A live run of the E2E scenario shows zero judged in-project file changes,
  both children completing, and the judged set consisting only of what left the
  project — the planted out-of-project read, the `find -exec` reads, and
  anything else outside the rules — with the denial still reached.
- `docs/security.md`, `docs/architecture.md`, `docs/networked-orchestration-e2e.md`,
  and `README.md` state the model: inside its project a worker acts by right
  under the sandbox; a judge decides what leaves it.
