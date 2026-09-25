# Bootstrapping a Codex coordination workspace

The common use case is one parent Codex session coordinating child sessions in
two or more existing local projects. The parent runs in a small control
workspace; the children run in their own project directories.

```text
coordination workspace
  parent Codex session
        |
        | curl to 127.0.0.1
        v
  codex-coordinator-service
        |-- child session in existing project A
        `-- child session in existing project B
```

The coordination workspace is organizational. It holds the operator mapping,
parent instructions, service lifecycle files, and perhaps plans or integration
notes. It does not contain or replace the child projects, and it does not need
to appear in the project mapping unless it should also be a child.

## 1. Inspect each child before registering it

Codex Coordinator supplies only the child `cwd`, plus optional model and
per-turn reasoning-effort overrides. It never sends a permission profile,
sandbox, approval policy, reviewer, rules, or workspace roots.

Inspect the host/user configuration and each child's `.codex/config.toml`.
These settings mean the child is intentionally unrestricted:

```toml
approval_policy = "never"
sandbox_mode = "danger-full-access"
```

Registering such a project authorizes the coordinator to start child sessions
with those capabilities. Supported approval requests from other configurations
are automatically accepted. Use this only for trusted projects, prompts, and a
trusted single-user host.

## 2. Install the coordinator commands

From the Codex Coordinator checkout:

```console
make install
command -v codex-coordinator-preflight codex-coordinator-service
```

The default `make install` uses `uv tool install --force .`; its tool bin
directory must be on `PATH`.

The coordinator attaches to the host user's already-running app-server. It
does not start or restart that daemon. Check it with:

```console
codex app-server daemon version
```

If necessary, the host user can start it through Codex itself:

```console
codex app-server daemon start
```

`codex-coordinator-preflight --require-socket` reports socket metadata and
connection failures as a single `preflight failed:` diagnostic. An access
denial names the socket and operating-system reason; it does not recommend
starting a second daemon. Grant the parent process access to the existing host
socket, then rerun preflight.

## 3. Create the coordination workspace

From the coordinator checkout, copy the reusable template:

```console
mkdir -p ~/code/my_coordination
cp examples/coordination-workspace/Makefile ~/code/my_coordination/
cp examples/coordination-workspace/AGENTS.md ~/code/my_coordination/
cp examples/coordination-workspace/gitignore ~/code/my_coordination/.gitignore
cp examples/coordination-workspace/operator.toml.example ~/code/my_coordination/operator.toml
mkdir -p ~/code/my_coordination/.codex/agents
cp examples/coordination-workspace/.codex/agents/coordinator-monitor.toml \
  ~/code/my_coordination/.codex/agents/
cd ~/code/my_coordination
git init
```

Edit `operator.toml` and replace the example paths with absolute existing
directories:

```toml
codex_command = "codex"

[projects]
frontend = "/Users/me/code/frontend"
backend = "/Users/me/code/backend"
```

Project names are the API identifiers the parent uses. Paths must be absolute,
existing directories. Registration is authority to start Codex there.

## 4. Start and verify the service

```console
make start
make status
curl --fail --silent --show-error http://127.0.0.1:8765/health
```

During an active parent session, if session creation reports that this service
is unreachable, invoke `make start` once with the sandbox escalation required
for local control-plane access. A successful `make start` already proves health;
proceed directly to session creation instead of issuing separate preflight and
health probes.

Use a custom port when `8765` is occupied:

```console
make start PORT=9876
```

The supported template is macOS-specific: `make start` submits the service to
the user's `launchctl` supervisor so it remains alive after the initiating
Codex tool call returns. Startup succeeds only after `/health` responds. The
template records the selected port, log, and durable managed-session registry
in ignored local files; it does not
treat a short-lived background PID as proof of startup. `make status` checks
HTTP health and distinguishes a stale supervisor job or legacy PID/port files,
with `make stop` then `make start` as the supported recovery. `make stop`
unloads the supervised job; the resulting SIGTERM uses the service's orderly
shutdown path. If no supervised job remains, it falls back to the HTTP shutdown
route for recovery from older launch methods.

Restarting the service with the same `.coordinator-state.json` resumes its
managed app-server threads before `/health` becomes reachable. Keep this file
private and paired with the same `operator.toml`; startup fails if a saved
thread's returned ID or canonical working directory does not match the current
project mapping. Removing the file deliberately forgets coordinator handles,
but does not delete app-server threads.

## 5. Open the parent Codex session

Start Codex normally from the coordination workspace. Its `AGENTS.md` explains
the control API and trust boundary. A useful initial Goal is:

```text
/goal Coordinate the configured child projects until the requested outcome is
complete and verified. Follow this workspace's AGENTS.md coordination and
monitor contract. Retain responsibility for task decisions and final
verification; complete only after the required child work and integration
evidence are verified. If blocked, report the evidence and exact input needed.
```

A Goal keeps the parent objective active across continuation turns. Use the
monitoring structure in the template `AGENTS.md` while children are active.
Codex discovers the tracked project-scoped definition at
`.codex/agents/coordinator-monitor.toml`; spawn its declared
`coordinator_monitor` agent once per active coordination batch without the
parent's task history. Include an explicit transport-access mode in its bounded
brief: use `require_escalated` when the managed sandbox blocks local HTTP or
socket access. The monitor uses the bounded blocking form
`GET /events?after=N&wait=30`,
advances the cursor only from returned events, and treats `outcome=timeout` as
no change worth returning to the parent. This keeps one HTTP request parked in
the service and routine waiting out of the main coordination context. When the
parent has no other ready work, it should use one long `wait_agent` call sized
to the remaining deadline and re-arm only if that collaboration wait expires.
Use `GET /sessions` as the authoritative reconciliation surface.

Copied workspaces own their customized `AGENTS.md`, but should periodically
merge coordinator-contract changes from both the template `AGENTS.md` and
`.codex/agents/coordinator-monitor.toml`. `codex-coordinator-preflight` reports
a workspace warning when the monitor definition is missing or predates the
current contract.

## 6. Basic HTTP workflow

Create a batch in one request; the returned service ID, event cursor, and
session snapshots form the complete initial monitor brief:

```console
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  -d '{"sessions":[{"project":"frontend","prompt":"Implement the requested frontend work and verify it."},{"project":"backend","prompt":"Implement the requested backend work and verify it."}]}' \
  http://127.0.0.1:8765/sessions/batch
```

Keep each returned session ID. Inspect state and events:

```console
curl --fail --silent --show-error http://127.0.0.1:8765/sessions
curl --fail --silent --show-error 'http://127.0.0.1:8765/events?after=0'
curl --fail --silent --show-error 'http://127.0.0.1:8765/events?after=0&wait=30'
```

`GET /events` is the concise orchestration feed. It contains child messages,
session transitions, automatic-approval/protocol outcomes, and terminal
results; it intentionally omits routine raw item and delta traffic. For a
bounded diagnostic capture only, use `GET /debug/events?after=N`. That debug
feed has a separate cursor and exposes redacted app-server-specific payloads;
normal clients should not download or filter it.

If `/events` returns 410, read the authoritative `/sessions` state and its
bounded `evidence` summaries, then resume `/events` with the response's
`recovery.resumeAfter` cursor. Do not retry the expired cursor or poll faster:
raw high-volume work does not consume the orchestration retention window.

Send a focused follow-up:

```console
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Review the result, address any gaps, and run the focused tests."}' \
  http://127.0.0.1:8765/sessions/SESSION_ID/messages
```

Stop the service when coordination is finished:

```console
make stop
```

This is transparent orchestration, not an authorization boundary. Keep the
service loopback-only and do not expose it to remote or untrusted clients.
