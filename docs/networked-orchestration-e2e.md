# Agent-driven orchestration E2E

This opt-in experiment makes the deliberately recursive architecture concrete:

```text
live_e2e harness
  ├─ starts trusted `codex-coordinator-service` outside the coordinator sandbox
  │    ├─ owns app-server WebSocket and child threads
  │    └─ independently judges worker approvals
  └─ starts coordinating `codex exec`
       ├─ scans the service stdout JSONL file
       ├─ calls the loopback HTTP API with curl
       └─ observes approval outcomes and verifies worker results
```

The two child sessions concurrently complete compatible inventory applications in
separate temporary projects copied from
[`examples/inventory-app`](../examples/inventory-app) and
[`examples/inventory-report`](../examples/inventory-report). Each project is a compact
test-driven scaffold with packaging, CLI wiring, documentation, contract tests, and
one implementation module containing the TODOs. Their checked-in
`.codex/config.toml` files use `workspace-write`, so each child authors its own project
while the service remains the middle-man for creating the session and sending turns.
The inventory child is instructed to make an explicit approval-path request for a
project-local test command and to attempt a network operation. The
[constitution](../examples/operator/constitution.md) allows the former and denies
the latter. The service judges approvals using the operator-owned constitution.
The coordinator must observe at least one `approve_once` and one `deny`,
reconcile the applications, run their tests, write `result.json`, and shut down the
service.

The top-level session runs from the checked-in
[`examples/coordinator`](../examples/coordinator) template. A live workspace has three
sibling projects plus `workspace/operator/`. The latter contains a path-resolved
[`operator.toml`](../examples/operator/operator.toml) and the
[`constitution.md`](../examples/operator/constitution.md), outside the coordinator's
writable project and both worker roots. Coordinator artifacts—including the
path-resolved `goal.md`, child goal prompts, event stream, decisions, and result—live
under `workspace/coordinator/`. Its `AGENTS.md` documents that application writes
must be delegated over HTTP. The coordinator's workspace permission profile keeps the
sibling projects outside its writable roots.

Run it only when real Codex usage, outbound access for service-owned judges, and access to the
local app-server socket are acceptable:

```sh
uv run python -m codex_coordinator.live_e2e \
  --workspace /tmp/codex-orchestration-run
```

The top-level coordinator uses the `coordinator` permission profile from its copied
`.codex/config.toml`. The profile extends the workspace baseline, enables networking
for localhost service calls, and allowlists only the resolved app-server Unix
socket. It is instructed to make all child application edits through HTTP-managed
sessions. The children and judges retain their narrower configurations. This
separation demonstrates the mechanism; it is not a production security boundary.

The harness defaults the coordinating session to `gpt-5.6-sol` with medium reasoning.
Child workers use `gpt-5.6-luna` with low reasoning to keep repeated work quick.
Per-approval judges use the configured Codex CLI's default model and reasoning
settings. The coordinator model and reasoning effort can be overridden with
command-line options.

The service has a configurable approval timeout (300 seconds by default). The
harness terminates both the coordinator and service process groups if interrupted.
Every
generated workspace is preserved, including the goal prompt, stdout events, final
response, child projects, and summary, if produced.

The trusted harness starts the service before the coordinating session and passes
its loopback port into the goal. The service is outside the coordinator's writable
project, so its restricted judge can run without nesting inside the coordinator's
Codex sandbox. The coordinator may call the control API but cannot start a second
service or submit an approval verdict.
The harness stores the full coordinator stream in `coordinator.jsonl`; the service
already records its complete stream in `service.jsonl`. By default, the terminal
filters both into a human-readable timeline. Initial child prompts and all follow-up
prompts are printed in full, along with approvals, verdicts, child completions, and the
final result. Token deltas, repeated full diffs, token accounting, and routine protocol
notifications remain in the log files but are hidden from the terminal.

Use `--verbose` for commands and file-change summaries. Use `--json` to print every
event with a `coordinator`, `service`, or `harness` source field. With Make, pass these
as `LIVE_E2E_ARGS`, for example:

```sh
make live-e2e LIVE_E2E_ARGS=--verbose
```

The ordinary `pytest` suite does not consume Codex usage. It deterministically covers
the same critical plumbing: concurrent RPC multiplexing, stdout approval emission,
service-owned verdict resolution, and child-session creation.

The live scenario is intentionally opt-in and is not part of the automated test
suite; completion depends on model behavior and consumes Codex usage.

Once the coordinator exits, the non-model harness acts as the final oracle. It reruns
both suites, produces and consumes a known inventory, checks strict schema-version and
extreme-decimal behavior, and correlates approval requests with resolutions. A run
fails if the network request was not explicitly denied, the project test request was
not approved once, an approval remains pending, or any black-box application check
fails.

The coordinator template uses a named workspace permission profile. The harness
resolves its `{{APP_SERVER_SOCKET}}` marker to the current user's control socket before
launch and does not use `--dangerously-bypass-approvals-and-sandbox`.
