# 0002 — The judge is never told what the worker was asked to do

**Found:** 2026-09-13, multi-capability workspace assessment, code review of the
judge prompt construction
**Affects:** codex_coordinator, approval judging, `src/codex_coordinator/coordinator.py`

## What happens

`ApprovalCase` carries `method`, `thread_id`, `project`, `request`,
`declared_intent`, and `enforced_capabilities` (`coordinator.py:41-50`), and the
judge prompt passes exactly those (`coordinator.py:466-481`). It does not carry
the prompt the coordinator sent to `start_session`/`send_message`, the session's
turn history, or any label for what kind of worker this is.

The constitution's first rule is "the action is necessary for the task assigned
to that child" (`examples/operator/constitution.md`). The judge has no way to
evaluate it. In practice it must infer the task from `reason` and `command` —
the two fields a compromised worker authors. Injection does not need to override
the rule; it supplies the only evidence the rule can be applied to.

This is worse once several projects with genuinely different legitimate behavior
share a coordinator: `curl` is an attack in a self-modifying project and routine
in a web-searching one, and the judge cannot tell them apart from the case alone.

## Why it matters

Governance integrity. The judge is asked to apply a necessity test using only
worker-authored evidence, so the rule that is meant to be the primary defense
against in-scope-but-unrelated actions is unevaluable.

## What would close this

- `ApprovalCase` carries a trusted, operator- or coordinator-supplied task
  descriptor that the worker cannot author, and the judge prompt separates it
  from `untrusted_evidence`.
- The descriptor's provenance is recorded in `approval.requested` so an audit
  can show what the judge was told the task was.
- A test shows a request that is in-scope but unrelated to the declared task
  reaching the judge with both facts distinguishable.
