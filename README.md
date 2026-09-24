# Codex Coordinator

Codex Coordinator is an installable Python prototype for starting and communicating with Codex child sessions in multiple local projects. It connects to the host user's already-running Codex app-server and exposes both a reusable Python API and a long-running loopback HTTP service.

This is transparent orchestration, not an authorization boundary. Each child inherits the Codex configuration that naturally applies at its configured project path, including host/user and project-local `.codex` configuration. That configuration may grant unrestricted access. If the runtime sends a valid approval request, the coordinator accepts it automatically, preferring a session-scoped acceptance when the protocol offers one.

Use this only as a trusted, single-user, loopback-only prototype. It is not safe for remote, multi-user, untrusted-client, or production authorization use.

## Quickstart: coordinate existing projects from Codex

The primary workflow is a normal parent Codex session in a small coordination
workspace delegating to child Codex sessions in existing projects:

```text
parent Codex session -> loopback coordinator service -> child project sessions
```

Install the two commands from this checkout:

```console
make install
command -v codex-coordinator-preflight codex-coordinator-service
```

Create a coordination workspace from the supplied template:

```console
mkdir -p ~/code/my_coordination
cp examples/coordination-workspace/Makefile ~/code/my_coordination/
cp examples/coordination-workspace/AGENTS.md ~/code/my_coordination/
cp examples/coordination-workspace/gitignore ~/code/my_coordination/.gitignore
cp examples/coordination-workspace/operator.toml.example ~/code/my_coordination/operator.toml
cd ~/code/my_coordination
```

Edit `operator.toml` so each stable name points to an absolute existing project
directory. Make sure the host app-server is running, then start the coordinator:

```console
make start
# Or choose another loopback port:
make start PORT=9876
```

Open a normal Codex session in the coordination workspace and ask it to delegate
through the HTTP service described in `AGENTS.md`. The coordination workspace
does not need to be registered as a child project.

Each child inherits the host/user and project-local Codex configuration that
already applies in its directory. In particular, a child project configured
with `approval_policy = "never"` and `sandbox_mode = "danger-full-access"` runs
in that unrestricted mode; the coordinator does not add a sandbox or narrow it.
See [Bootstrapping a Codex coordination workspace](docs/coordination-workspace.md)
before registering projects.

## Configuration

Create an operator TOML file with stable names mapped to absolute existing directories:

```toml
codex_command = "codex"
# socket_path = "/absolute/custom/socket"
# worker_model = "gpt-5.6-codex"
# worker_reasoning_effort = "medium"

[projects]
app = "/Users/me/code/app"
docs = "/Users/me/code/docs"
```

The service rejects relative, missing, and non-directory project paths. It does not impose containment, disjointness, ownership, or writable-root policy: registering a project authorizes the local service to start a Codex thread there.

The default socket is the standard socket below the host user's active Codex home. The coordinator never sets `CODEX_HOME`, creates a separate home, or starts/restarts the app-server. A missing, invalid, or unreachable socket is a startup error.

## Service

```console
codex-coordinator-preflight --config /absolute/operator.toml --require-socket
codex-coordinator-service --config /absolute/operator.toml
```

The loopback HTTP API provides `POST /sessions`, follow-up and cancel routes, `GET /sessions`, `GET /events`, `GET /health`, and `POST /shutdown`. Session creation names a configured project and accepts optional `model` and `effort`; follow-ups accept optional per-turn `effort`.

There is no approval-resolution endpoint. Successful automatic decisions are emitted as `approval.auto_approved`; malformed or unsupported server requests produce `approval.protocol_error` and a protocol error response.

## Python API

```python
import asyncio
from codex_coordinator import Coordinator, OperatorConfig

async def main():
    config = OperatorConfig.load(path="/absolute/operator.toml")
    async with await Coordinator.connect(config) as coordinator:
        app, docs = await asyncio.gather(
            coordinator.start("app", "Implement the feature"),
            coordinator.start("docs", "Update the guide"),
        )
        await asyncio.gather(app.wait(), docs.wait())
        await app.follow_up("Run the focused tests", effort="high")
        await app.wait()

asyncio.run(main())
```

See `examples/generic_coordinator.py` for a runnable two-project program. Run `.venv/bin/pytest -q` for the offline suite. The opt-in live compatibility exercise is separate because only a real host app-server can prove effective configuration inheritance; the latest recorded run is in [`docs/live-compatibility.md`](docs/live-compatibility.md).

`make live-e2e` uses the bundled coordination workspace as an actual parent and
the two bundled inventory projects as children. It hosts an ephemeral loopback
control plane, requires the parent to delegate routine waits to a monitoring
subagent, and emits a `parentMonitor` evidence object. It consumes Codex usage
and fails if the parent reports main-context waiting, shell sleep polling, an
invalid session map, or missing terminal monitor reports. The child prompts are
read-only inspections.

```console
make live-e2e
```

`LIVE_E2E_CONFIG`, `LIVE_E2E_PARENT`, `LIVE_E2E_FIRST`,
`LIVE_E2E_SECOND`, and the prompt variables remain available for a custom
three-project exercise.

Use `make live-e2e-compat LIVE_E2E_ARGS='...'` for the older direct two-child
compatibility mode.
