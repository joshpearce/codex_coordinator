# Codex Coordinator

Codex Coordinator is a research prototype for supervising multiple Codex workers.
It starts workers in separate projects, routes their approval requests to independent
judges, and applies deterministic policy limits before any approval is returned to
Codex. It includes a one-shot command, a long-running local control service, and a
live end-to-end orchestration example.

The central idea is that a model may recommend a decision, but must not define its
own authority. Both execution modes therefore share one governance boundary that
validates requests, seals the evidence seen by judges, limits paths and permissions,
and fails closed when a verdict exceeds the request or trusted policy.

## Security status

The known high-priority governance bypasses in the issue tracker are addressed and
covered by tests. Worker execution is constrained by an explicit app-server sandbox,
and judge verdicts cannot widen normalized requests or trusted permission ceilings.

This is still suitable only for controlled, single-user local experiments. The HTTP
control API is unauthenticated, a live one-shot judge turn using the restricted
read profile remains unverified,
the coordinator has unrestricted outbound network access, and logs may retain
sensitive content. The service now requires trusted allowed roots, caps request and
in-memory state, expires approvals, and checks socket type, ownership, and mode,
but listener authentication and durable audit storage remain unfinished. Do not
expose the service beyond loopback
or use it as a production or multi-user authorization system. See
the [security issue tracker](docs/issues/issues.md) for the current details.

The prototype supports Codex CLI 0.154.0 only. Startup verifies that version
and the approval/session shapes in its generated app-server JSON Schema, and
fails before starting a worker if they differ. [Official OpenAI documentation](https://learn.chatgpt.com/docs/app-server)
notes that generated schemas are version-specific. Run the release gate when
upgrading Codex; add a version only after verifying the gate and a live run.

This repository currently has no license file or license metadata. The owner
has chosen to leave licensing undecided for now.

See [architecture](docs/architecture.md) for the responsibility split.
See the [security boundary](docs/security.md) and [lifecycle contract](docs/lifecycle.md)
for supported use and failure states.

## Requirements

- Python 3.11 or newer
- `uv` for development and source-checkout commands; not required to run an installed wheel
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
make installed-gate
make release-gate
```

Install the CLI with:

```sh
make install
```

`make installed-gate` builds the sdist and wheel, installs the wheel in a clean
virtual environment outside the checkout, checks all console entry points,
runs the two-project fixture as an installed package module, and checks the installed Codex CLI's
generated protocol schema. It needs Codex CLI 0.154.0, Python build tooling,
and access to the runtime dependency. The fixture uses a fake app-server client
to check package/API portability; it does not prove live Codex behavior. With
`GENERIC_FIRST` and `GENERIC_SECOND` set, that installed fixture uses the two
existing project directories without changing their configuration. Without
them, it creates disposable unrelated projects for the offline package check.
The `Verify` GitHub Actions workflow runs the full test suite and this
installed-artifact gate with the pinned Codex CLI on pushes and pull requests.

`make release-gate` also runs the source-only generic example from that fresh
environment against two real operator-approved projects. Set
`GENERIC_CONFIG`, `GENERIC_FIRST`, `GENERIC_SECOND`, `GENERIC_FIRST_GOAL`,
`GENERIC_SECOND_GOAL`, `GENERIC_FOLLOW_UP`, `GENERIC_ALLOW_COMMAND`, and
`GENERIC_DENY_COMMAND` to your own absolute config/project paths, tasks, and
exact command strings. The tasks must cause Codex to request approval for both
named commands, with at least one approval request from each of two unrelated
project directories. The gate fails unless both workers and the follow-up complete,
an `accept` response for the allowed command and a `decline` response for the
denied command are sent, and every observed approval resolves. It correlates
each computed response with a completed WebSocket send, the app-server's
`serverRequest/resolved` receipt, and the command item's final status. A
computed decision without that evidence cannot pass. The real Codex CLI must be signed in.
The command strings must match the app-server approval request's `command`
field, which can include a shell executable and `-lc` wrapper around the
`exec_command` `cmd` text. Do not loosen the judge to match a substring or a
suffix. For disposable test commands, run the example with
`--show-approval-commands` to inspect a failed gate's requested strings;
that flag can disclose sensitive command text and is not appropriate for
private production tasks. The full live gate passed on an unrestricted
operator host; it cannot run inside the current nested-sandbox workspace.

The one-shot Codex judge has a separate live profile check. On an unrestricted
host with a signed-in supported Codex CLI, run
`python scripts/judge_live_gate.py` from an environment where this package is
installed. It probes an allowed read and a denied unrelated read, then requires
a structured deny decision from a tool-disabled `codex exec` judge. This check
cannot run inside a nested Codex sandbox.

## Long-running control service

The proof-of-concept service owns one multiplexed app-server connection, accepts
commands over a loopback HTTP port, and writes minimal metadata events to stdout
as JSONL:

```sh
uv run codex-coordinator-service --port 8765 --allowed-root /absolute/path/to/workers
```

The service rejects session projects outside its startup-configured
`--allowed-root` directories. Repeat the flag to authorize multiple roots.

Before starting work, check the operator configuration and project prerequisites:

```sh
codex-coordinator-preflight --config /absolute/path/operator.toml \
  --project /absolute/path/worker-a --project /absolute/path/worker-b
```

Preflight verifies the supported Codex executable/schema and local sign-in,
canonical project roots, worker configuration, judge policy presence, and an
existing socket's type, ownership, and mode. `socketReady: false` means the
default daemon will need to start; pass `--require-socket` to make that a failure.
If a socket exists, preflight also probes its listener and fails on a stale or
inaccessible socket. This is a connection check, not a full worker run.
A custom `socket_path` must already be served by a private app-server Unix
listener; the coordinator does not start a second listener at that path.

Trusted settings can also be loaded with `--config /absolute/path/operator.toml`:

```toml
allowed_roots = ["/absolute/path/to/workers"]
codex_command = "codex"
socket_path = "/absolute/path/to/app-server-control.sock"
worker_model = "gpt-5.6-luna"
worker_reasoning_effort = "low"
approval_timeout_seconds = 300
allow_session_approval = false
event_capacity = 2048
event_max_bytes = 8388608
item_capacity = 256
item_max_bytes = 65536

[permission_ceilings."/absolute/path/to/workers/example"]
fileSystem = { read = ["/absolute/path/to/workers/example/input"] }
```

The file must be an absolute-path, owner-controlled regular file outside all
worker-writable allowed roots; workers and judges cannot change its trusted
ceilings. Precedence is built-in defaults, then TOML, then
`CODEX_COORDINATOR_*` environment variables, then explicit CLI flags. For
environment overrides, `ALLOWED_ROOTS` and `PERMISSION_CEILINGS` contain JSON;
other fields are strings, with `ALLOW_SESSION_APPROVAL` accepting `true` or
`false`. `CODEX_COORDINATOR_CONFIG` selects a TOML file when `--config` is
omitted. Paths in configuration must be absolute. The service requires at least
one allowed root, keeps the control API on `127.0.0.1`, and denies unanswered
approvals after the configured timeout. The service expects an external judge
to post verdicts; the one-shot command uses its built-in independent Codex judge.

Its intentionally small, unauthenticated API is:

- `POST /sessions` with `{"project": "/abs/path", "prompt": "..."}`
- `POST /sessions/{id}/messages` with `{"prompt": "..."}` after its active turn ends
- `GET /sessions`
- `GET /events?after=N`
- `POST /approvals/{id}` with `{"sessionId": "...", "verdict": "approve_once|approve_session|deny", "reason": "..."}`
- `POST /shutdown`

Approval requests expire after `approval_timeout_seconds` (300 seconds by default).
The service emits an `approval.requested` event and continues when an outside actor
posts a verdict or the request expires and is denied.
The approval ID and posted session ID must match the immutable registration made
when the worker thread started. This remains a local experiment: the loopback HTTP
API has no authentication and the medium/deferred hardening issues remain open.

`POST /sessions/{id}/cancel` interrupts its active turn and denies that session's
pending approvals. `/events?after=N` uses monotonically increasing sequence numbers;
when the in-memory window has evicted a cursor, it responds `410 Gone` with
`oldestSequence`. Active sessions become `connection_lost` if the app-server
transport drops; observation loss is not reported as successful completion.
The in-memory event window defaults to 2,048 records and 8 MiB; any single
event over 1 MiB is summarized, and oversized approval evidence is denied.
Event and item count/byte limits are operator-configurable.
The HTTP API keeps full non-oversized events in memory for local judges, but
stdout omits prompts and payloads by default. `--verbose-events` opts into
fuller JSONL output with known secret-bearing fields redacted; command text and
model output can still contain secrets. Set `umask 077` before redirecting
service output to a file and remove logs according to your local retention policy.

## Reusable Python API

This repository is currently unlicensed by the owner's choice; the package
metadata intentionally declares no license.

Install the wheel with `python -m pip install dist/codex_coordinator-*.whl` from a
release build, or install from Git with
`python -m pip install git+https://github.com/joshpearce/codex_coordinator.git`.
The library needs no repository-relative files at runtime. In a trusted operator
process, configure two project roots and provide a judge implementation:

```python
import asyncio
from pathlib import Path

from codex_coordinator import Coordinator, JudgeDecision, OperatorConfig

class OperatorJudge:
    async def decide(self, case):
        # Replace with a trusted, application-specific review. Deny by default.
        return JudgeDecision("deny", "no operation has been approved")

async def main():
    config = OperatorConfig.load(path=Path("/absolute/path/operator.toml"))
    async with await Coordinator.connect(config, OperatorJudge()) as coordinator:
        first, second = await asyncio.gather(
            coordinator.start("/absolute/path/worker-a", "Complete task A"),
            coordinator.start("/absolute/path/worker-b", "Complete task B"),
        )
        results = await asyncio.gather(first.wait(), second.wait())
        print(results)
        await first.follow_up("Review the result")
        print(await first.wait())

asyncio.run(main())
```

`SessionHandle` exposes `follow_up`, `cancel`, and `wait`; `Coordinator.events(after=N)`
streams typed, session-correlated events. `Coordinator.event_cursor` gives the
latest emitted sequence when a caller needs to wait for its event consumer to
catch up. For manual review, an `approval.requested` event exposes
`event.approval` as a typed `ApprovalRequest`; pass it with a `JudgeDecision`
to `coordinator.resolve_approval_request(...)`. The lower-level
`resolve_approval(...)` method also accepts explicit IDs. Both paths use the
same deterministic policy boundary. Judge failures and expiry deny the
request. `wait(timeout=...)` raises `TimeoutError` without calling the turn
complete. The in-memory event cursor can expire, and no state survives process
restart. The source-only generic example in `examples/generic_coordinator.py`
supports interactive decisions or exact-command gate decisions across two
operator-selected projects.

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
  --allowed-root /absolute/path/to \
  /absolute/path/to/worker-project \
  "Worker instructions" \
  "Approve only the explicitly permitted operation; deny everything else."
```

The command requires at least one trusted `--allowed-root` or `allowed_roots`
entry in operator configuration. It rejects projects outside those roots before
reading project-local worker configuration. It accepts the same `--config`,
`--socket`, `--codex-command`, and
worker-model settings as the service. Its third positional judge-policy argument
is optional when `judge_policy` is set in operator configuration. The command
prints a JSON report containing the worker thread ID, final state,
approval request, judge verdict and app-server response. It starts the existing
daemon if needed and uses the default control socket at
`~/.codex/app-server-control/app-server-control.sock`.

The judge runs as a separate ephemeral `codex exec` process with a restrictive
permission profile and approvals disabled. A read-isolation probe runs before
each invocation and fails closed if its unrelated-file read is not denied;
the profile still needs successful runtime verification on a host that permits
nested sandbox tests. A future implementation can instead provide a
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
