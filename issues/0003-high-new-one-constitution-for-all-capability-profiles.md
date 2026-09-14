# 0003 — One constitution governs every worker regardless of capability profile

**Found:** 2026-09-13, multi-capability workspace assessment, code review of
constitution loading and judge construction
**Affects:** codex_coordinator, trusted policy configuration,
`src/codex_coordinator/config.py`, `src/codex_coordinator/coordinator.py`

## What happens

`constitution_path` is a single file snapshotted once at startup
(`config.py:220-237`), and `OneShotCodexJudge` holds one `policy_instructions`
string for every worker (`coordinator.py:455-463`). The only per-project trusted
knob is `permission_ceilings`.

A constitution permissive enough for a web-searching worker to do its job is
permissive enough for a mail-reading worker to exfiltrate; one strict enough for
a self-modifying worker to be safe stops the web worker working. The operator's
realistic response is to write the union, which applies the weakest rule set to
the most sensitive worker.

This is a policy-authoring failure mode created by the code's shape, not by the
operator.

## Why it matters

Governance integrity. The natural language policy is the only per-request
control that can distinguish legitimate from injected intent, and the
configuration forces it to be written at the permissiveness of the least
constrained worker.

## What would close this

- Per-project policy text is supported, resolved from trusted operator-owned
  paths outside every worker and coordinator root, with the same
  `_trusted_policy_file` validation.
- The judge invocation for a project uses that project's policy and cannot see
  another project's.
- Startup fails closed when a registered allowed root has no policy in a mode
  that requires one.
