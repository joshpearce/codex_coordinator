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

## 3. Create the coordination workspace

From the coordinator checkout, copy the reusable template:

```console
mkdir -p ~/code/my_coordination
cp examples/coordination-workspace/Makefile ~/code/my_coordination/
cp examples/coordination-workspace/AGENTS.md ~/code/my_coordination/
cp examples/coordination-workspace/gitignore ~/code/my_coordination/.gitignore
cp examples/coordination-workspace/operator.toml.example ~/code/my_coordination/operator.toml
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

Use a custom port when `8765` is occupied:

```console
make start PORT=9876
```

The template records the PID, selected port, and log in ignored local files.
`make stop` reads the recorded port, requests orderly HTTP shutdown, validates
the PID before any fallback signal, and removes stale runtime state.

## 5. Open the parent Codex session

Start Codex normally from the coordination workspace. Its `AGENTS.md` explains
the control API and trust boundary. A useful initial Goal is:

```text
/goal Coordinate the configured child projects until the requested outcome is
complete and verified. Delegate through the local Codex Coordinator HTTP
service, retain session IDs and the event cursor, poll conservatively while
work is active, reconcile through GET /sessions, and send focused follow-ups
when evidence is missing. Complete only after the required child work and
integration evidence are verified. If blocked, report the evidence gathered
and the exact input needed.
```

A Goal keeps the parent objective active across continuation turns. It does not
turn `GET /events` into push delivery. The current endpoint returns immediately,
so the parent should poll conservatively, advance the event cursor, and use
`GET /sessions` as the authoritative reconciliation surface.

## 6. Basic HTTP workflow

Create one child session per project:

```console
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  -d '{"project":"frontend","prompt":"Implement the requested frontend work and verify it."}' \
  http://127.0.0.1:8765/sessions

curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  -d '{"project":"backend","prompt":"Implement the requested backend work and verify it."}' \
  http://127.0.0.1:8765/sessions
```

Keep each returned session ID. Inspect state and events:

```console
curl --fail --silent --show-error http://127.0.0.1:8765/sessions
curl --fail --silent --show-error 'http://127.0.0.1:8765/events?after=0'
```

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
