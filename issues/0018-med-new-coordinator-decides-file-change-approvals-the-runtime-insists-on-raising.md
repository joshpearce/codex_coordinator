# 0018 — The coordinator answers file-change approvals because no Codex approval policy separates them from commands

**Found:** 2026-09-14, reviewing `ApprovalPolicy._decide_file_change` after the
#0015 work, then reading the pinned CLI's generated `AskForApproval` schema
**Affects:** codex_coordinator, approval routing,
`src/codex_coordinator/coordinator.py` (`ApprovalPolicy._decide_file_change`),
`src/codex_coordinator/compatibility.py`

## What happens

This codebase is meant to do two things: supply operator-owned configuration to
Codex's own permission system, and judge the approval requests that system
escalates. `_decide_file_change` is a third thing — it answers a class of
approval request from a rule of its own — and that is a replication of the
permission model it is supposed to be governing, however small.

It exists because the pinned CLI raises the question and offers no way to stop
raising it. The complete wire vocabulary, from
`codex app-server generate-json-schema --experimental`:

```
AskForApproval = "untrusted" | "on-request" | "never"
               | {granular: {mcp_elicitations, request_permissions, rules,
                             sandbox_approval, skill_approval}}
```

- `untrusted` raises an approval before a command *and* before an in-project
  file change, though the turn sandbox already confines the write.
- `on-request` leaves the decision to the worker; a worker that never asks is
  never judged, which is the configuration an earlier run showed produces zero
  approvals.
- `never` executes unjudged.
- `granular` has no file-change category, and probe P8 in #0016's record showed
  it letting an unmatched command run with no approval at all.

The rule language offers no alternative either: `prefix_rule`, `network_rule`,
and `host_executable` are the whole set of builtins in 0.154.0's execpolicy
Starlark dialect, and none of them constrains a path.

So under the only usable policy, some reviewer has to answer file changes.
`_decide_file_change` answers them from the boundary already configured: every
change path is normalized against the registered project and the turn sandbox
makes that project the only writable root, so an in-project change is accepted
and a `read-only` project's change is declined, each without a judge call.

### Not the same as the command case

The coordinator also evaluates command rules itself (#0016), but for different
reasons — the runtime's rules are global to `$CODEX_HOME`, ignore paths, are
readable from a worker-writable directory, and do not decide the guard shape
workers issue. Those reasons survive a per-thread configuration key. The
file-change reason does not: one new approval policy or one new granular
category would remove it entirely.

### Known cost

- `ApprovalPolicy` carries containment, sandbox-mode, and `grantRoot` decisions
  that the runtime could in principle make.
- Those decisions rest on an assumption about the runtime's sandbox that only
  `tests/test_runtime_boundary.py` and the live E2E verify. If a CLI upgrade
  changed sandbox enforcement, the deterministic accept would be wrong. This is
  medium rather than high because the compatibility gate pins the CLI version,
  so no upgrade reaches this code without a human decision, and the boundary
  regression proves confinement on the pinned version.
- The `grantRoot` decline is the least defensible of the three: it adjudicates
  a capability grant rather than routing one, and could equally be handed to a
  judge as something that leaves the project.

## Why it matters

Scope and maintenance, and a divergence risk on upgrade. Every rule evaluated
here is a rule that can drift from what the runtime enforces, and Codex is a
moving target. The value of recording this is that the code is provisional: it
should be deleted, not extended, the moment the runtime can be configured to
stop asking.

## What would close this

- A pinned CLI offers an approval policy — a new `AskForApproval` variant, or a
  `granular` category — that escalates commands, network, and permission
  requests but not file changes whose paths lie inside the thread's writable
  root; the coordinator sets it, and a live run shows in-project file changes
  raising no approval request at all while an out-of-project read still does.
- `_decide_file_change` and the `CodeDecision` file-change branch are deleted,
  along with their regressions, and `docs/security.md` records the switch that
  replaced them.
- If instead a later CLI confirms no such policy will exist, this is closed by
  recording that decision and keeping the code, with `docs/architecture.md`
  stating plainly that answering file-change approvals is a permanent
  responsibility of this codebase rather than a gap being worked around.
- Either way, `compatibility.py` continues to assert the approval-policy and
  granular-category sets, so the question is re-asked on every CLI upgrade
  rather than forgotten.
