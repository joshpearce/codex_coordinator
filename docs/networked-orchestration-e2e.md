# Agent-driven orchestration E2E

This opt-in experiment makes the deliberately recursive architecture concrete:

```text
live_e2e harness
  └─ coordinating `codex exec`
       ├─ starts `codex-coordinator-service` with no process timeout
       │    └─ owns app-server WebSocket and child threads
       ├─ scans the service stdout JSONL file
       ├─ calls the loopback HTTP API with curl
       └─ for each approval, starts a fresh `codex exec` judge
```

The two child sessions build compatible inventory applications in separate temporary
projects copied from [`examples/inventory-app`](../examples/inventory-app) and
[`examples/inventory-report`](../examples/inventory-report). Their checked-in
`.codex/config.toml` files use `workspace-write`, so each child authors its own project
while the service remains the middle-man for creating the session and sending turns.
The inventory child is instructed to make an explicit approval-path request for a
project-local test command and to attempt a network operation. The
[constitution](../examples/coordinator/constitution.md) allows the former and denies
the latter. The coordinator must observe at least one `approve_once` and one `deny`,
reconcile the applications, run their tests, write `result.json`, and shut down the
service.

The top-level session runs from the checked-in
[`examples/coordinator`](../examples/coordinator) template. A live workspace has three
sibling projects, and all coordinator artifacts—including the path-resolved `goal.md`,
child goal prompts, constitution, event stream, decisions, and result—live under
`workspace/coordinator/`. Its `AGENTS.md` explicitly documents that application writes
must be delegated over HTTP. The coordinator's workspace permission profile keeps the
sibling projects outside its writable roots.

Run it only when real Codex usage, outbound access for nested judges, and access to the
local app-server socket are acceptable:

```sh
uv run python -m codex_coordinator.live_e2e \
  --workspace /tmp/codex-orchestration-run
```

The top-level coordinator uses the `coordinator` permission profile from its copied
`.codex/config.toml`. The profile extends the workspace baseline, enables networking
for localhost and nested Codex judges, and allowlists only the resolved app-server Unix
socket. It is instructed to make all child application edits through HTTP-managed
sessions. The children and judges retain their narrower configurations. This
separation demonstrates the mechanism; it is not a production security boundary.

The harness defaults the coordinating session to `gpt-5.6-sol` with medium reasoning.
Child workers and per-approval judges remain on `gpt-5.6-luna` with low reasoning to
keep the repeated work quick. The coordinator model and reasoning effort can be
overridden with command-line options.

The service has no approval timeout. The harness terminates the coordinator's process
group if interrupted, preventing the nested service from being left behind. Every
generated workspace is preserved, including the goal prompt, stdout events, final
response, child projects, and summary, if produced.

The coordinating session starts the service as a foreground command held by its shell
execution session. It intentionally does not daemonize with `nohup`: managed execution
hosts may reap detached descendants as soon as the launching tool call returns.
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
HTTP verdict submission, and child-session creation.

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
