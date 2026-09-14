# 0003 — One constitution governs every worker regardless of capability profile

**Found:** 2026-09-13, multi-capability workspace assessment, code review of
constitution loading and judge construction
**Affects:** codex_coordinator, trusted policy configuration,
`src/codex_coordinator/config.py`, `src/codex_coordinator/coordinator.py`

## What happens

Originally, `constitution_path` was a single file snapshotted once at startup
and `OneShotCodexJudge` held one `policy_instructions` string for every worker.
The only per-project trusted knob was `permission_ceilings`.

A constitution permissive enough for a web-searching worker to do its job is
permissive enough for a mail-reading worker to exfiltrate; one strict enough for
a self-modifying worker to be safe stops the web worker working. The operator's
realistic response is to write the union, which applies the weakest rule set to
the most sensitive worker.

This is a policy-authoring failure mode created by the code's shape, not by the
operator.

A two-tier constitution is now implemented. `Constitution` holds one overall
`PolicyDocument` plus per-project documents keyed on canonical project paths.
`trusted_policy` hands a judge the overall document and the one document for the
project it is judging; `for_project` selects the most specific entry at or above
a project. `operator.toml` gained a `[project_constitutions]` table, validated with the same
`_trusted_policy_file` ownership rules, required to sit beside `operator.toml`,
required to be outside every coordinator- and worker-writable root, forbidden
from reusing the overall document, and rejected outside `approval_mode =
"service"`. Both tiers are mandatory in service mode: startup rejects an
allowed root with no project constitution, `POST /sessions` refuses an
ungoverned project, and the judge denies without invoking a model. An entry at a
root governs every project beneath it; a deeper entry overrides it. Both tiers are recorded by source and digest on
`approval.requested` and `approval.resolved`. The example operator directory and
the live E2E goal now use two per-project documents with distinct digests, and
the harness fails a run whose approvals lack provenance or share a project
document across children.

Both tiers are written as capability profiles and principles rather than command
lists, because an enumerated constitution is silent — and therefore reads as
permissive — on every request it failed to anticipate.
`scripts/judge_live_gate.py` carries a catalogue of twelve realistic requests
(dependency installs into the project and into user site-packages, an installer
piped to a shell, cross-project source reads, a consumer fetching its own data,
an exfiltration instructed by the data being processed, plus legitimate test and
CLI runs) that the constitutions deliberately do not name, with the verdict and
deciding principle each should produce.

Offline evidence: full suite passes (206 tests), including a judge prompt that
contains one project's text and not another's, fail-closed denial for an
uncovered project with no judge call, config rejection of untrusted, misplaced,
out-of-root, and duplicated policy files, service events carrying one shared
overall digest with distinct per-project digests, and a guard test that fails if
any catalogue request's distinctive tokens appear verbatim in a policy document.

Live evidence: a networked E2E run judged eight approval requests from two
concurrent children, four each, every one carrying one shared overall digest
(`075282e5`) and its own project digest — `3819720e` for the producer,
`5d8e5f21` for the consumer. No child was told that judging exists; the requests
arose from the operator's `worker_approval_policy` and the children's ordinary
work. Judge reasons cited the governing project's own text, for example
approving a project-local read as "project-local and read-only, with no network
access or dependency changes".

Remaining: `make judge-gate` has not run, so no live run has yet shown a real
judge reaching the right verdict on a request the constitutions deliberately do
not name. The E2E exercised approvals only; every verdict was an approval,
because honest work inside the constitution produces no denials.

## Why it matters

Governance integrity. The natural language policy is the only per-request
control that can distinguish legitimate from injected intent, and the
configuration forced it to be written at the permissiveness of the least
constrained worker.

## What would close this

- Per-project policy text is supported, resolved from trusted operator-owned
  paths outside every worker and coordinator root, with the same
  `_trusted_policy_file` validation. **Done.**
- The judge invocation for a project uses that project's policy and cannot see
  another project's. **Done.**
- Startup fails closed when a registered allowed root has no policy in a mode
  that requires one. **Done** — service mode always requires one.
- `make judge-gate` passes: a real judge returns the catalogued verdict for every
  scenario, including the two that differ only in which project asked.
- The live networked gate runs against a two-tier configuration and shows both
  children judged under a shared overall document and distinct project
  documents. **Done** — eight approvals across two children with the digests
  above.
