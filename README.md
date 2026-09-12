# Codex Coordinator

Codex Coordinator is an experimental Python client for starting Codex worker
threads through `codex app-server`, observing approval requests over the daemon's
Unix WebSocket, and delegating decisions to independent Codex judges.

The one-shot judged-worker command has a deterministic policy ceiling around the
judge. The long-running HTTP service is a deliberately looser orchestration POC:
its external coordinator is responsible for applying the checked-in constitution.

## Status

This is a working prototype tested against Codex CLI 0.154.0. It is not a
general-purpose unattended authorization service. The app-server transport and
schema are experimental and should be version-tested when Codex is upgraded.

See [architecture](docs/architecture.md) for the responsibility split.

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

## Long-running control service

The proof-of-concept service owns one multiplexed app-server connection, accepts
commands over a loopback HTTP port, and writes app-server events to stdout as JSONL:

```sh
uv run codex-coordinator-service --port 8765
```

Its intentionally small, unauthenticated API is:

- `POST /sessions` with `{"project": "/abs/path", "prompt": "..."}`
- `POST /sessions/{id}/messages` with `{"prompt": "..."}` after its active turn ends
- `GET /sessions`
- `GET /events?after=N`
- `POST /approvals/{id}` with `{"verdict": "approve_once|approve_session|deny", "reason": "..."}`
- `POST /shutdown`

Approval requests have no timeout. They remain pending while the service emits an
`approval.requested` event and continue only after an outside actor posts a verdict.
This is deliberately a local experiment, not a hardened network service.

Run the real recursive orchestration experiment with:

```sh
uv run python -m codex_coordinator.live_e2e \
  --workspace /tmp/codex-orchestration-run
```

The live runner is repository-local because it copies the checked-in examples. It
copies the coordinator and child skeletons into three sibling
project folders. It writes the resolved goal to `workspace/coordinator/goal.md` and
starts a privileged `gpt-5.6-sol` coordinating `codex exec` with medium reasoning
there. Workers and approval judges stay on `gpt-5.6-luna` with low reasoning. The
coordinator starts the service itself, creates two read-only child sessions, scans the
service's stdout, and launches a separate read-only `codex exec` judge for each
approval. The generated workspace is retained for inspection. See
[the live E2E guide](docs/networked-orchestration-e2e.md) before running it.

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

## One-shot command safety model

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
