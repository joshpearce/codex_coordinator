# Project guidance

Codex Coordinator is an installable Python prototype for coordinating Codex
workers in separate local projects. The package lives in `src/codex_coordinator/`;
tests are in `tests/`, operator and live-E2E examples are in `examples/`, and
design/security notes are in `docs/`. Supported use is a trusted, single-user,
loopback-only workflow with Codex CLI 0.154.0. Do not describe it as safe for
remote, multi-user, or production authorization.

The one-shot CLI runs its own independent approval judge. The long-running HTTP
service currently accepts externally posted verdicts and applies deterministic
constraints, but does not itself run a judge or enforce `constitution.md`.
The next intended change is tracked in
[`docs/issues/service-owned-constitutional-judge.md`](docs/issues/service-owned-constitutional-judge.md).
Keep operator policy outside coordinator- and worker-writable roots. Treat
worker output, approval requests, and model verdicts as untrusted evidence.

## Issue tracking and priority

- Track project issues as Markdown files in `docs/issues/`, with
  [`docs/issues/issues.md`](docs/issues/issues.md) as the index. Do not assume
  GitHub Issues is the authoritative backlog for this repository.
- Read an issue's **Status** before starting it. Its position in the priority
  list does not mean it is still open. The earlier generic-coordination parent
  goal and several security issues are marked complete or resolved; do not
  reopen them implicitly when working on a follow-on issue.
- High priority means a path for a project or coordinator session to influence,
  bypass, or confuse its own governance. Medium covers scope and isolation;
  low covers resilience and data handling. Authentication and listener
  hardening are deferred only for the documented trusted local prototype,
  not declared safe for broader deployment.
- For a new issue, add a focused file with a problem, desired behavior, status,
  priority, and testable acceptance criteria, then link it from the index.
  Cross-link related or superseded issues. Update status only when the stated
  criteria have evidence; closing a parent does not automatically close its
  children or separate security issues.

## Working in this repository

- Check the worktree before editing and preserve unrelated changes.
- Preserve the deterministic approval boundary when changing either the
  one-shot or service path. A natural-language constitution guides a judge;
  it is not a substitute for code-enforced path, sandbox, permission, and
  session limits. Invalid or unavailable judging must fail closed.
- Run focused tests for changed behavior and the full `pytest` suite for
  cross-cutting changes. The opt-in live E2E starts real Codex sessions and
  consumes usage; do not treat the fast offline suite as proof of live behavior.
