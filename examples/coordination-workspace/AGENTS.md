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
The template persists managed handles in `.coordinator-state.json`; after a
service restart, reconcile and continue the returned session IDs instead of
starting replacement threads.

Delegate routine event waiting and session reconciliation to one dedicated
monitoring subagent for each active coordination batch. Give that monitor a
small, bounded brief containing only the service URL, the retained event cursor,
the session ID-to-project/task map, and the reporting contract below; do not
give it the parent's full task history. The main parent must not repeatedly
resume its own large context merely to observe a timeout.

The monitor retains the cursor and session IDs and uses
`GET /events?after=N&wait=30`, the concise orchestration projection. It must not
filter raw app-server schemas, busy-loop, or use shell sleeps. On `events`, it
advances the cursor and reconciles authoritative state through `GET /sessions`
when needed. On `timeout`, it continues waiting without reporting to the parent.
On `shutdown`, an expired cursor, a changed service ID, connection loss, or an
uncertain terminal state, it follows the documented recovery where possible and
reports a monitoring failure when it cannot safely continue. On a 410, it
reconciles `GET /sessions` including the evidence summaries, then resumes from
`recovery.resumeAfter`. `GET /debug/events` remains limited to explicit,
bounded diagnosis.

The monitor reports to the main parent only when there is actionable child
progress requiring a decision or follow-up, a terminal state, or a monitoring
failure. Each report includes the affected session ID, the last safe cursor,
the relevant state/evidence, and the action required from the parent. The
monitor sends no empty, timeout, or non-actionable progress messages. It does
not decide task scope, send child follow-ups, cancel sessions, ask the user
questions, or verify the final result. It is neither a second coordinator nor
an authorization boundary.

When operating under a Goal, keep coordinating until the requested outcome is
verified across the relevant child projects. The main parent retains task
decisions, focused child follow-ups, cancellation, user questions, and final
verification. Use monitor reports to make those decisions; replace a failed
monitor explicitly rather than silently reverting to main-context polling. Do
not repeatedly resume the main context with short collaboration waits. When no
other parent work is ready, use one long `wait_agent` call sized to the remaining
coordination deadline; re-arm it only if that collaboration wait itself expires
without a monitor report. Do not declare the Goal complete merely because child
turns ended: verify the requested artifacts or test evidence. If work needs
user input or no defensible path remains, report the blocker and specific input
needed.

This is a trusted, single-user, loopback-only prototype. Registering a project
authorizes the coordinator to start Codex there. Child sessions inherit host,
user, and project-local Codex configuration as-is, including any
danger-full-access/never-approve settings. Supported approval requests are
accepted automatically. Do not describe this setup as a security boundary or
as suitable for remote, multi-user, untrusted-client, or production use.
