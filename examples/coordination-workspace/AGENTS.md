# Coordination workspace guidance

This repository is a control workspace for a parent Codex session coordinating
child sessions in the existing projects named in `operator.toml`.

Use the Codex Coordinator service at `http://127.0.0.1:8765`. Start child work
through the service rather than launching another Codex CLI process or directly
changing a child project from this workspace.

Before delegating, check `GET /health` and inspect `GET /sessions`. Create
sessions with `POST /sessions`, retain each returned session ID, and use
`POST /sessions/{id}/messages` for follow-ups. Use
`POST /sessions/{id}/cancel` only when cancellation is actually needed.

Track the event cursor returned by `GET /events?after=N`. The endpoint is
snapshot-based, so poll it conservatively and reconcile authoritative session
state through `GET /sessions`; do not busy-loop. Treat a changed service ID, an
expired cursor, connection loss, or an uncertain terminal state explicitly.

When operating under a Goal, keep coordinating until the requested outcome is
verified across the relevant child projects. While children are active, wait
between event checks, inspect new events, reconcile session states, and send
precise follow-ups. Do not declare the Goal complete merely because child turns
ended: verify the requested artifacts or test evidence. If work needs user
input or no defensible path remains, report the blocker and specific input
needed.

This is a trusted, single-user, loopback-only prototype. Registering a project
authorizes the coordinator to start Codex there. Child sessions inherit host,
user, and project-local Codex configuration as-is, including any
danger-full-access/never-approve settings. Supported approval requests are
accepted automatically. Do not describe this setup as a security boundary or
as suitable for remote, multi-user, untrusted-client, or production use.
