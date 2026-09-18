# 0026 — Replace governance with transparent child-session orchestration

**Found:** 2026-09-18, product-direction review after the governed-worker design
proved too complex for the desired coordinator
**Affects:** codex_coordinator, configuration, service, Python API, HTTP API,
examples, tests, and documentation

## What happens

Codex Coordinator has grown into a governance system. It owns a separate Codex
home, selects and verifies worker permission profiles, refuses project-owned
Codex rules, applies deterministic approval constraints, loads constitutions,
runs independent judges, and maintains external-verdict machinery. Most of the
configuration, implementation, tests, examples, and documentation now support
that boundary rather than the core requirement: one coordinating Codex session
starting and communicating with Codex sessions in other local projects through
the Python process.

That is no longer the desired product. A child project must use the Codex
configuration that naturally applies at its configured path, including its
project-local `.codex` configuration and the host user's Codex configuration.
The coordinator must not select, validate, narrow, replace, or describe those
rules and permissions. A child may intentionally be configured for unrestricted
operation. The coordinating session may be unrestricted as well.

The service must connect to the host user's already-running app-server at its
standard socket. It must use the host user's active Codex home implicitly,
without setting `CODEX_HOME`, rendering a separate home, or starting, restarting,
or otherwise managing the daemon. An unavailable host app-server is a clear
startup failure.

Projects are configured as a name-to-absolute-path mapping. There is no
`allowed_roots` hierarchy, containment policy, project nesting restriction, or
request-time arbitrary project path. Starting a session names a configured
project; the service resolves that name to its path and passes the path as the
thread's working directory. Validation that a configured path is absolute,
exists, and is a directory is input validation only, not a security boundary.

The coordinator may override only the child model and reasoning effort. It must
not send a permission profile, sandbox, approval policy, reviewer, rules, or any
other security configuration on `thread/start` or `turn/start`.

If a child emits a valid approval request, the coordinator automatically
approves it. There is no judge and no operator verdict. The approval handler
uses `acceptForSession` when that decision is offered, otherwise `accept`, and
returns requested permission data exactly as required by the supported
app-server protocol. Unknown or malformed request methods fail visibly as
protocol errors rather than receiving invented successful responses. Successful
automatic decisions are observable as `approval.auto_approved` events.

The useful orchestration behavior remains: concurrent named child sessions,
initial prompts, follow-up turns, cancellation, waiting for terminal results,
event streaming, connection-loss handling, bounded runtime state, and access
through both the long-running HTTP service and the reusable Python API.

## Why it matters

The present architecture solves a governance problem the product no longer
wants to own. Its policy layers make ordinary local orchestration difficult to
configure, understand, test, and maintain, while preventing child projects from
using the Codex settings already chosen for them by their owners.

This change deliberately removes the coordinator as an authorization boundary.
That tradeoff must be unmistakable: registering a project authorizes the local
service to start a Codex session there, the child's effective Codex
configuration determines its actual capabilities, and the coordinator approves
every valid request the runtime escalates. The resulting service remains a
trusted, single-user, local prototype and must not be presented as safe for
remote, multi-user, untrusted-client, or production authorization use.

## What would close this

### Product contract and issue history

- Rewrite `AGENTS.md`, `README.md`, `docs/architecture.md`, `docs/security.md`,
  and `docs/ROADMAP.md` around transparent local orchestration and automatic
  approval.
- Remove claims that the coordinator supplies or enforces a worker governance
  boundary.
- Explicitly document that project and host Codex configuration is trusted as-is
  and may grant unrestricted access.
- Reconcile every existing governance issue. Preserve still-relevant transport,
  resource-bound, logging, and lifecycle work; record obsolete governance work
  as superseded by this redesign in durable history rather than claiming it was
  fixed. Delete obsolete issue files only in the final verified commit, under
  `docs/issue-tracking.md`.

### Configuration and host app-server

- Replace `allowed_roots` and per-project policy configuration with a strict
  name-to-absolute-path `projects` mapping.
- Reject duplicate names, relative paths, missing paths, and non-directories.
  Do not impose containment, disjointness, nesting, ownership, or writable-root
  restrictions on configured projects.
- Retain only orchestration settings such as the Codex executable, service
  listener/resource limits, optional model and reasoning overrides, and an
  optional socket override if it does not imply a separate home.
- Remove `codex_home` and every constitution, judge, permission-ceiling,
  worker-permission, permission-profile, execpolicy, and approval-timeout field.
- Connect to the host user's standard app-server socket without setting
  `CODEX_HOME`. Do not start or restart the daemon. Report an actionable startup
  error when the socket is missing, invalid, or unreachable.

### Transparent thread and turn startup

- `thread/start` sends the configured project path as `cwd` and, only when
  explicitly configured or requested, a model override. It sends no
  coordinator-selected approval, reviewer, permission, sandbox, rule, or
  profile field.
- `turn/start` sends the thread identity, prompt, and, only when explicitly
  configured or requested, a reasoning-effort override. It sends no security
  boundary field.
- Session creation accepts a configured project name rather than a caller-
  supplied filesystem path. Session and event records include both the stable
  project name and resolved path for operability, without treating either as an
  enforced sandbox boundary.
- Follow-up turns continue on the existing child thread and may override only
  reasoning effort. Model selection remains a thread-start concern unless the
  supported app-server protocol explicitly provides a safe model-change
  operation.

### Automatic approvals

- Replace `ApprovalBroker` and the judge/pending-verdict flow with a small
  automatic-approval handler for every supported app-server approval method.
- Prefer `acceptForSession` when offered and otherwise use `accept`; include the
  runtime-requested permission payload when the protocol requires it.
- Preserve request/response correlation, bounded evidence retention, clean
  shutdown, connection-loss behavior, and observable automatic-approval events.
- Remove approval resolution endpoints and the public types used to submit
  verdicts. No model call, human decision, constitution, policy evaluation, or
  pending approval queue remains.
- Treat an unsupported or malformed server request as an explicit protocol
  failure with a diagnostic event. Do not silently approve a shape the installed
  compatibility gate does not understand.

### Code removal and retained orchestration

- Delete the judge, constitution, approval-policy, permission-profile,
  execpolicy, project-rule-refusal, separate-Codex-home, permission-ceiling,
  and assignment-provenance implementations and their exports.
- Delete the single-worker `cli.py` judged-boundary harness rather than turning
  it into another product surface. Keep only `service.py` and `preflight.py` as
  installed commands unless the simplified design demonstrates that preflight
  has no useful transport/configuration checks left.
- Retain and simplify `ProtocolClient`, session/thread correlation, start,
  follow-up, wait, interrupt, event streaming, resource limits, connection-loss
  handling, and orderly shutdown.
- Keep the reusable Python API capable of allowing a coordinating Codex session
  to start and communicate with multiple child sessions through the Python
  process.

### Tests and examples

- Replace judge and governance tests rather than weakening their assertions.
  Remove tests for constitutions, verdict constraints, permission ceilings,
  profiles, execpolicy, project-rule refusal, and isolated Codex homes.
- Add wire-level tests proving that thread and turn requests contain no
  security-setting overrides and contain model/reasoning fields only when
  requested.
- Test project-name resolution, nonexistent projects, concurrency, follow-ups,
  cancellation, terminal waits, event routing, automatic approval of every
  supported request shape, malformed request failure, connection loss, bounded
  state, and unavailable host socket behavior.
- Replace the examples with two ordinary child projects, a small named-project
  configuration, and one coordinating Python program. Remove operator
  constitutions, permission descriptors, rules files, and rendered Codex-home
  fixtures.
- Add an opt-in live compatibility test against the supported Codex CLI and the
  host app-server proving that two projects can inherit distinct project-local
  `.codex` settings, host/user settings remain in effect, an unrestricted child
  is not narrowed by the coordinator, and valid escalated approvals are
  automatically accepted.
- Update the installed-wheel smoke test for the simplified public surface.
- Run focused tests throughout, then the full offline `pytest` suite. Run the
  opt-in live test separately and record it as the required evidence for actual
  configuration inheritance; the offline suite alone is not proof of app-server
  behavior.
