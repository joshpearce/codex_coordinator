# Codex Coordinator

Codex Coordinator is an experimental Python client that starts a Codex worker
thread through `codex app-server`, watches its approval requests over the
daemon's Unix WebSocket, and delegates each decision to an independent Codex
judge.

The transport and policy code—not the judge model—remain the final permission
boundary. Unknown threads, unsupported request types, out-of-project working
directories, excessive permission categories, malformed judge output, and
judge failures are denied deterministically.

## Status

This is a working prototype tested against Codex CLI 0.154.0. It is not a
general-purpose unattended authorization service. The app-server transport and
schema are experimental and should be version-tested when Codex is upgraded.

See [architecture](docs/architecture.md) for the responsibility split and
[live test results](docs/live-e2e.md) for the recorded approval and denial runs.

## Requirements

- Python 3.11 or newer
- `uv`
- A signed-in Codex CLI with `codex app-server daemon`
- A worker project containing `.codex/config.toml`

Worker projects must currently declare:

```toml
approval_policy = "on-request"
approvals_reviewer = "user"
sandbox_mode = "read-only" # or "workspace-write"
```

The coordinator validates these values and sends them explicitly when starting
the thread. This prevents the worker from accidentally inheriting broader
permissions from the app-server daemon's startup context.

## Development

```sh
make sync
make test
make check
make build
```

Install the CLI with:

```sh
make install
```

## Run a judged worker

```sh
uv run codex-coordinator \
  /absolute/path/to/worker-project \
  "Worker instructions" \
  "Approve only the explicitly permitted operation; deny everything else."
```

The command prints a JSON report containing the worker thread ID, final state,
approval request, judge verdict and app-server response. It starts the existing
daemon if needed and uses the default control socket at
`~/.codex/app-server-control/app-server-control.sock`.

The judge runs as a separate ephemeral `codex exec` process with a read-only
sandbox and approvals disabled. A future implementation can instead provide a
dedicated judge thread over a second app-server connection.

## Safety model

- Only thread IDs registered by this coordinator can receive decisions.
- The worker's request `cwd` and any file-change `grantRoot` must remain inside
  its configured project.
- Permission requests are denied unless their categories are explicitly
  allowlisted by the embedding application.
- Session-wide approval is downgraded to one-turn approval by default.
- The judge receives request content as untrusted JSON data.
- Invalid or unavailable judge responses fail closed.

The current shell-command policy still relies on the Codex judge to interpret
the command's effects. Production use should add an application-specific
deterministic command allowlist or exec-policy layer.
