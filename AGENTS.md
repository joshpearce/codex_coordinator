# Project guidance

Codex Coordinator is an installable Python prototype for coordinating Codex
workers in separate local projects. The package lives in `src/codex_coordinator/`;
tests are in `tests/`, operator and live-E2E examples are in `examples/`, and
design/security notes are in `docs/`. Supported use is a trusted, single-user,
loopback-only workflow with Codex CLI 0.154.0. Do not describe it as safe for
remote, multi-user, or production authorization.

Only `service.py` and `preflight.py` are installed as commands. `cli.py`,
`live_e2e.py`, and `installed_smoke.py` are test harnesses, not product
surfaces; each is run with `python -m`. `cli.py` is the single-worker harness
built on `JudgedSessionSupervisor` and `JudgedApprovalHandler`: it exercises the
approval boundary without the HTTP service. Keep it working and keep it honest,
but do not document or extend it as a way to run workers.

The long-running HTTP service is the supported surface. It has an explicit
service-owned mode that loads a two-tier constitution —
one overall document plus one per project, the overall one being a ceiling the
project one may only narrow — and runs independent judges,
plus an explicit external-verdict mode. A judge receives the overall document
and its own project's, never another project's. Both modes apply the same
deterministic constraints. That work is complete and recorded as
milestone M2 in [`docs/ROADMAP.md`](docs/ROADMAP.md).
Keep operator policy outside coordinator- and worker-writable roots. Treat
worker output, approval requests, and model verdicts as untrusted evidence.
Worker projects must not be told that judging exists: what reaches a judge is
decided by each project's operator-owned permissions file and its actual task,
never by instructing a worker to stage approval requests. A worker project holds
no Codex configuration of its own; its boundary is declared beside
`operator.toml`, outside every root a worker can write. A constitution states
principles and capability profiles, never a list of allowed or blocked commands;
`tests/test_judge_live_gate.py` fails if a catalogued request is named verbatim
in a policy document. Commands are named only in a project's operator-owned
execpolicy rules file (`exec_policy` in its permissions file), which the
coordinator evaluates itself for mundane project-local commands; the Codex
runtime's own rules loading is not used, for reasons recorded in
`docs/security.md`.

## Issue tracking and priority

- Read [`docs/issue-tracking.md`](docs/issue-tracking.md) before filing,
  starting, renaming, or closing an issue. It is the authoritative convention:
  one Markdown file per issue in top-level `issues/`, named
  `NNNN-<severity>-<state>-<slug>.md`, with severity and state carried by the
  filename rather than the body. Do not assume GitHub Issues is the
  authoritative backlog for this repository.
- Read an issue's state in its filename before starting it. Its position in any
  list does not mean it is still open, and numbers are never reused. Search by
  number when resolving references, since filenames change:
  `rg --files issues | rg '/0002-'`.
- Use [`docs/ROADMAP.md`](docs/ROADMAP.md) for capability sequencing. A roadmap
  capability is not itself a defect; an observed failure belongs in an issue
  even when the roadmap describes its fix.
- High severity means a path for a project or coordinator session to influence,
  bypass, or confuse its own governance. Medium covers scope and isolation; low
  covers resilience and data handling. Authentication and listener hardening are
  deferred only for the documented trusted local prototype, not declared safe
  for broader deployment.
- Update state only when the stated closure conditions have evidence, and delete
  a closed issue file in the final commit that completes the verified work.
  Closing a parent capability does not automatically close separate security
  issues.

## Working in this repository

- Check the worktree before editing and preserve unrelated changes.
- Preserve the deterministic approval boundary when changing either the
  service or the harness path. A natural-language constitution guides a judge;
  it is not a substitute for code-enforced path, sandbox, permission, and
  session limits. Invalid or unavailable judging must fail closed.
- Run focused tests for changed behavior and the full `pytest` suite for
  cross-cutting changes. The opt-in live E2E starts real Codex sessions and
  consumes usage; do not treat the fast offline suite as proof of live behavior.
