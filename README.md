# Codex Coordinator

Codex Coordinator is an experimental Python client for starting Codex worker
threads through `codex app-server`, observing approval requests over the daemon's
Unix WebSocket, and delegating decisions to independent Codex judges.

Both the one-shot judged-worker command and long-running service use the same
deterministic governance boundary. External judges provide advice inside that
boundary; they do not grant capabilities themselves.

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
permissions from the app-server daemon's startup context. The example application
projects use `workspace-write` because their child sessions own and author those
projects; session creation and prompting still pass through the HTTP service.

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
- `POST /approvals/{id}` with `{"sessionId": "...", "verdict": "approve_once|approve_session|deny", "reason": "..."}`
- `POST /shutdown`

Approval requests have no timeout. They remain pending while the service emits an
`approval.requested` event and continue only after an outside actor posts a verdict.
The approval ID and posted session ID must match the immutable registration made
when the worker thread started. This remains a local experiment: the loopback HTTP
API has no authentication and the medium/deferred hardening issues remain open.

Run the real recursive orchestration experiment with:

```sh
make live-e2e
```

The live runner is repository-local because it copies the checked-in examples. It
copies the coordinator and two compact, test-driven child scaffolds into three sibling
project folders. Each child completes TODOs in one module while packaging, CLI wiring,
documentation, contract tests, and the shared JSON contract are already present. The
full goal is checked in at `examples/coordinator/goal.md`; the harness substitutes
only runtime project paths in the copied file at `workspace/coordinator/goal.md` and
starts a `gpt-5.6-sol` coordinating `codex exec` with medium reasoning there. Its
project-local permission profile grants workspace execution, network access, and the
specific app-server Unix socket instead of bypassing the sandbox. Workers and approval
judges stay on `gpt-5.6-luna` with low reasoning. The
coordinator starts the service itself, creates two concurrent workspace-write child
sessions, scans the service's stdout, and launches a separate read-only `codex exec`
judge for each approval. The terminal shows a compact timeline containing session prompts,
follow-ups, approvals, completions, and the final result. Full-fidelity logs remain in
`workspace/coordinator/coordinator.jsonl` and `workspace/coordinator/service.jsonl`.
Use `make live-e2e LIVE_E2E_ARGS=--verbose` to include commands and file-change
summaries, or `LIVE_E2E_ARGS=--json` for the complete machine-readable stream. The
generated workspace is retained for inspection. See
[the live E2E guide](docs/networked-orchestration-e2e.md) before running it.

After the coordinating session exits, the harness independently reruns both child
test suites and a producer/consumer integration scenario. It also checks strict schema
typing, extreme-decimal error handling, complete approval resolution, and that the
recorded denial belongs to the network request rather than an unrelated operation.
Model-written `result.json` claims alone cannot make the command succeed.

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

## Governance and execution safety model

- Each managed thread is registered once with an immutable session ID, canonical
  project root, and policy. Unknown threads are denied without emitting their data.
- Approval methods, fields, paths, permission shapes, values, and session identity
  are normalized and checked before a request can enter the pending queue.
- File paths are canonicalized and checked against the project root, including
  traversal, prefix-confusion, and existing symlink cases.
- Permission responses are intersected with both the normalized request and the
  trusted policy ceiling. A judge can deny or narrow, never expand.
- Session-wide approval is disabled by default. Service operators must pass
  `--allow-session-approval`, and the individual request must support it.
- Every turn receives an explicit app-server sandbox policy. Workspace-write turns
  have the canonical project as their only writable root, network disabled, and
  both ambient temporary-directory write exceptions removed. The OS sandbox applies
  this boundary to subprocesses, symlinks, and indirect effects.
- Logs record untrusted declared intent separately from enforced capabilities.
- Invalid, ambiguous, conflicting, or unavailable judge responses fail closed.
