# Make the service own constitutional approval judging

## Priority

High — governance integrity for agent-driven coordination.

## Status

Resolved — service-owned constitutional judging is implemented and verified.
The explicit `external` mode remains available for manual integrations, but the
service never falls back to it from `service` mode.

Evidence: trusted constitution and mode validation, concurrent approval,
HTTP-bypass, fail-closed, and request-to-resolution tests pass in the focused
suite (`131 passed`) and full suite (`185 passed`). The live networked gate
passed with one `approve_once` for a
project-local test command and one denial for a network command; both workers
and the cross-project integration check passed. The coordinating session did
not launch judges or submit approval verdicts.

## Problem

The intended workflow starts a Codex session in a coordinator project, then
asks that session to delegate cross-project work and monitor the workers. The
service already identifies and normalizes worker approval requests, and
`ApprovalBroker` has an optional asynchronous judge hook. However, the service
entry point constructs the broker without a judge. The coordinating session
must read `constitution.md`, launch a judge, and POST its verdict to
`/approvals/{id}`. It can instead post an approving verdict without consulting
the constitution. The deterministic `ApprovalPolicy` still limits that verdict,
but cannot establish that the constitutional review took place.

## Desired behavior

In a service-owned judging mode, the operator selects a trusted
`constitution.md` alongside `operator.toml`. The service loads the constitution
outside coordinator- and worker-writable roots, runs an isolated independent
judge for each valid worker approval request, and applies the existing
deterministic ceiling before replying to the app-server. The coordinator agent
may dispatch tasks, inspect events, send follow-ups, and verify outcomes, but
cannot supply an approving verdict or alter the trusted judge policy.

## Acceptance criteria

- Expose a documented service configuration for an absolute constitution path.
  Validate the file and its parent as operator-controlled; reject symlinks and
  coordinator- or worker-writable locations. Load a stable policy snapshot at
  service startup so later coordinator or worker edits cannot change it.
- Wire the existing broker judge hook to an independently invoked, restricted
  judge for every normalized approval request in this mode. Reuse the one-shot
  judge's isolation and fail-closed behavior where appropriate; keep the
  request's session and project identity bound to the resulting verdict.
- Make the HTTP approval endpoint unable to bypass judging in this mode:
  reject caller-supplied approvals (or permit only explicit denials). If
  external/manual judging remains available, require an explicit separate
  mode with no silent fallback from service-owned judging.
- Preserve the deterministic project, sandbox, requested-decision, permission,
  and session-lifetime ceilings regardless of the judge's recommendation.
  Invalid output, judge startup/probe failure, timeout, cancellation, service
  shutdown, and connection loss fail closed and leave no hanging approval.
- Cover concurrent workers and multiple approvals, judge failure/timeout,
  denied bypass attempts through HTTP, and event correlation from request
  through verdict, wire send, and app-server resolution. A live gate should
  exercise both an approved project-local action and a denied action without
  the coordinating session submitting approving verdicts.
- Update the generic operator setup and live example to use the same
  `operator.toml` plus `constitution.md` arrangement. The coordinator goal
  should no longer instruct the agent to launch judges or POST approvals;
  documentation must distinguish service-owned and explicit external modes.

## Scope note

This makes constitutional review mandatory in the selected service mode; it
does not make natural-language judgment mathematically enforceable. The
existing deterministic checks remain the hard authorization boundary. The
HTTP service remains limited to the documented trusted, single-user local
environment until its separate authentication issue is addressed.
