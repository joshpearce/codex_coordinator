# Project guidance

Codex Coordinator is an installable Python prototype for transparent orchestration of Codex child sessions in separate local projects. The package lives in `src/codex_coordinator/`; tests are in `tests/`, examples in `examples/`, and design/security notes in `docs/`. Supported use is trusted, single-user, and loopback-only with the pinned Codex CLI. Do not describe it as safe for remote, multi-user, untrusted-client, or production authorization use.

Only `service.py` and `preflight.py` are installed commands. `live_e2e.py` and `installed_smoke.py` are test harnesses run with `python -m`; do not turn them into product surfaces.

The coordinator connects to the host user's already-running app-server at its standard socket. It must not set `CODEX_HOME`, render another home, or start/restart the daemon. Projects are a strict name-to-absolute-path mapping. A child inherits host/user and project-local Codex configuration as-is, which may intentionally grant unrestricted access.

The coordinator may override only the thread model and per-turn reasoning effort. Never send a permission profile, sandbox, approval policy, reviewer, rules, workspace roots, or another security setting on `thread/start` or `turn/start`.

Valid supported approval requests are automatically accepted. Prefer `acceptForSession` when offered, otherwise `accept`, and return requested permission data in the protocol-required shape. Unsupported or malformed requests must fail visibly as protocol errors. There is no judge, constitution, external verdict, pending approval queue, or HTTP approval-resolution route.

Retain concurrent sessions, follow-ups, cancellation, terminal waits, event streaming, connection-loss behavior, resource limits, and orderly shutdown through both the service and reusable Python API.

## Working with configured projects

Route all high-level requests about a configured project through the coordinator service and its child sessions. This includes requests to inspect, explain, search, diagnose, modify, test, or report on a project's code, files, configuration, or repository state. Do not satisfy such requests by reading or changing the configured project's files directly from the coordinator process, shell, or filesystem tools, even when its mapped path is locally accessible. Follow-ups and related work must remain in the same service-managed session when continuity matters.

Direct filesystem access is reserved for developing and maintaining this coordinator repository and its own test fixtures, or when a user explicitly asks for a low-level investigation of the coordinator itself. Treat a configured project's name-to-path mapping as routing information for the child session, not as permission for the coordinator to operate on that path.

## Issue tracking

Read `docs/issue-tracking.md` before changing issues. One Markdown file per issue lives in `issues/` and is named `NNNN-<severity>-<state>-<slug>.md`; filename severity/state are authoritative and numbers are never reused. Search by number when resolving references. Use `docs/ROADMAP.md` for sequencing. Delete a closed issue only with durable evidence under the documented convention.

Preserve still-relevant control-plane transport, logging, bounded-state, and lifecycle issues. Obsolete governance work is superseded by #0026 rather than claimed fixed.

## Working in this repository

Check the worktree before editing and preserve unrelated changes. Run focused tests for changed behavior and the full `pytest` suite for cross-cutting changes. The opt-in live E2E starts real Codex sessions and consumes usage; offline tests are not proof of effective configuration inheritance or live app-server behavior.
