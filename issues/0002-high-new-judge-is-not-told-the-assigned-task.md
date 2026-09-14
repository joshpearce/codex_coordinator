# 0002 — The judge is never told what the worker was asked to do

**Found:** 2026-09-13, multi-capability workspace assessment, code review of the
judge prompt construction
**Affects:** codex_coordinator, approval judging, `src/codex_coordinator/coordinator.py`

## What happens

`ApprovalCase` carries `method`, `thread_id`, `project`, `request`,
`declared_intent`, and `enforced_capabilities`, and the judge prompt passes
those. It does not carry the prompt the coordinator sent to
`start_session`/`send_message`, the session's turn history, or any trusted
statement of what this session was asked to do.

The two-tier constitution (#0003) narrows this but does not close it. A project
constitution is trusted, operator-authored, and now reaches the judge, so the
judge can be told what kind of worker this is and what a project's standing
assignment is — that is what distinguishes `curl` in a web-searching project
from `curl` in a self-modifying one. It is static per project, so it still
cannot say what *this session* was asked to do, and a project running several
different turns has one description for all of them. The judge prompt now also
carries the coordinator-normalized `declared_intent` alongside the raw request,
which is derived rather than worker-authored prose, but it is derived from
worker-authored content and is not a task descriptor.

The constitution's first rule is "the action is necessary for the task assigned
to that child" (`examples/operator/constitution.md`). The judge has no way to
evaluate it. In practice it must infer the task from `reason` and `command` —
the two fields a compromised worker authors. Injection does not need to override
the rule; it supplies the only evidence the rule can be applied to.

This is worse once several projects with genuinely different legitimate behavior
share a coordinator: `curl` is an attack in a self-modifying project and routine
in a web-searching one. Per-project policy lets the judge tell those two
projects apart; it still cannot tell two turns of the same project apart.

## Why it matters

Governance integrity. The judge is asked to apply a necessity test using only
worker-authored evidence, so the rule that is meant to be the primary defense
against in-scope-but-unrelated actions is unevaluable.

## What would close this

- `ApprovalCase` carries a trusted, per-session task descriptor that the worker
  cannot author, and the judge prompt separates it from `untrusted_evidence`.
  The per-project constitution is the static half of this; the session-scoped
  half is missing.
- The descriptor's provenance is recorded in `approval.requested` so an audit
  can show what the judge was told the task was.
- A test shows a request that is in-scope but unrelated to the declared task
  reaching the judge with both facts distinguishable.
